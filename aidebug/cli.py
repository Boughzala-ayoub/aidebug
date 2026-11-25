"""JSON CLI and an authenticated, workspace-scoped local helper."""

import argparse
import csv
import ctypes
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time

from .dap import DebugError, hidden
from .session import Session
from . import launch as launch_options

MAX_REQUEST = 32768
MAX_RESPONSE = 65536

USAGE_LOOP = """Choose the tool: run the smallest relevant test first. If the failure and source
already establish the cause, fix it and rerun the test. Use aidebug when a value,
branch, caller, closure, or state transition remains uncertain. State that question
before launching. Use a profiler or equivalent timings for performance measurements.

Agent debugging loop (run in the target project, or add --workspace PATH):
  aidebug view --open
  aidebug launch --module --run --breakpoint pricing.py:6 pytest -- -q test_checkout.py
  aidebug inspect
  aidebug inspect --frame FRAME_HANDLE
  aidebug scopes
  aidebug value --scope SCOPE_HANDLE renderer.info.memory.geometries
  aidebug variables VALUE_HANDLE
  aidebug note "Describe the observed values and suspected cause."
  aidebug step over
  aidebug continue
  aidebug output
  aidebug stop

Replace the test and breakpoint with your project paths. For a script, use
launch script.py; for unittest, use launch --module unittest -- -q test_module.
Test runners catch assertions, so set a breakpoint near the failure; --uncaught
can only stop at their final SystemExit. Pytest must be in the target interpreter.
Python selection: --python, VIRTUAL_ENV (ignored when it is aidebug's own
environment), workspace .venv, then the CLI interpreter. Check the returned python.

Every command returns JSON. ok means the debugger command succeeded, not that the
target passed. Handles expire when execution resumes; use fresh ones from each stop.
timed_out is a deadline, not an exit: run status, pause a running target, or stop it.
Wait for phase: ready before pause. Idle browsers may wait for the next JS task.
Long runs: launch --no-wait TARGET, then --timeout 300 wait.
Conditional stop: break set pricing.py 6 --condition "count == 437"
Conditions execute target code. There is no evaluate command; value follows child
names through DAP variables. Error messages name the next step to take.
After editing source, stop and relaunch to verify.
If target_exit_confirmed is false, the exit is unknown: read diagnostics and output
and never infer exit 0. Run a safe reproduction outside the debugger for its code.

Reduce stdout with global --fields before the command ([] selects array items):
  aidebug --fields state,reason,timed_out wait
  aidebug --fields 'stack.frames[].name,stack.frames[].line,stack.frames[].handle' inspect
On Windows, set PYTHONIOENCODING=utf-8 before piping output into a Python script.

Frame-loop breakpoints distort timing. A running soak proves nothing until you
confirm the application progressed.
More: launch --help (browser options, PowerShell quoting), doctor --help (adapter
setup), break --help, recordings --help. sources checks breakpoint binding;
automation returns private CDP access to a launched browser page.
"""

ADAPTER_SETUP = """Path checks only: doctor does not start or validate adapter binaries, check
compatibility, compile targets, or contact a browser's web server.

Adapter selection: --adapter PATH, then AIDEBUG_JS_DEBUG / AIDEBUG_CODELLDB,
then the selected workspace's .tools directory. Use an absolute shared path to
avoid setting up the adapter in every project. No automatic downloads.

Node/TypeScript/browser: install Node.js and download the standalone
js-debug-dap archive from https://github.com/microsoft/vscode-js-debug/releases
Extract so .tools/js-debug/src/dapDebugServer.js exists, or set AIDEBUG_JS_DEBUG
to its absolute path. Pass --node EXE if Node is not on PATH.
Rust: download your platform's CodeLLDB archive from
https://github.com/vadimcn/codelldb/releases and extract the whole archive into
.tools/codelldb, retaining extension/adapter and extension/lldb together.
The executable is extension/adapter/codelldb.exe on Windows, codelldb elsewhere.
Alternatively set AIDEBUG_CODELLDB to that executable's absolute path.
Browser targets also need Chrome/Chromium/Edge; pass --browser EXE if needed.

PowerShell shared installation example (replace the path with your installation):
  $env:AIDEBUG_JS_DEBUG = 'C:/debug tools/js-debug/src/dapDebugServer.js'
  aidebug doctor --language javascript
  aidebug launch --run app.js
"""

POWERSHELL_USAGE = """PowerShell: use & before a quoted executable path. Put launch options before
the target, then -- and target arguments. Outer double quotes can contain the
single quotes used by Python/JavaScript conditions, without native double-quote
escaping. These examples work in Windows PowerShell 5.1:
  & 'C:/debug tools/aidebug.exe' launch --run --breakpoint 'app.py:4' --condition "name == 'ready'" 'app.py' -- 'two words'
  & 'C:/debug tools/aidebug.exe' break set 'app.py' 4 --condition "name == 'ready'"
Avoid embedding double quotes in native arguments on PowerShell 5.1 when single
quotes express the same condition. For adapter setup, run aidebug doctor --help.
"""


def encode(result):
    data = json.dumps(result, ensure_ascii=True).encode() + b"\n"
    if len(data) > MAX_RESPONSE:
        result = dict(result)
        for key in ("output", "locals", "variables", "stack", "location", "breakpoints", "scopes", "sources", "value", "launch", "diagnostics"):
            if key in result:
                result[key] = {"truncated": True, "message": "Response exceeds 64 KiB; request fewer values"}
                result["truncated"] = True
                data = json.dumps(result, ensure_ascii=True).encode() + b"\n"
                if len(data) <= MAX_RESPONSE:
                    break
    return data


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise DebugError("invalid_request", message)

    def print_help(self, file=None):
        print(json.dumps({"ok": True, "help": self.format_help()}))


def positive(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected an integer")
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def condition(value):
    if not value.strip() or len(value) > 512 or "\0" in value:
        raise argparse.ArgumentTypeError("condition must contain 1..512 characters without NUL")
    return value


def fields(value):
    paths = value.split(",")
    if len(value) > 2048 or not 1 <= len(paths) <= 32:
        raise argparse.ArgumentTypeError("fields requires 1..32 paths within 2048 characters")
    tree = {}
    for path in paths:
        parts = path.strip().split(".")
        if not 1 <= len(parts) <= 8 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(\[\])?", part) for part in parts):
            raise argparse.ArgumentTypeError("use complete dot-separated field paths, with [] for arrays (at most 8 levels)")
        node = tree
        for index, part in enumerate(parts):
            if any(key != part and key.removesuffix("[]") == part.removesuffix("[]") for key in node):
                raise argparse.ArgumentTypeError("a field cannot be selected as both an object and an array")
            if part in node and (not node[part] or index == len(parts) - 1):
                raise argparse.ArgumentTypeError("field paths must be distinct and must not select both a parent and its child")
            node = node.setdefault(part, {})
    return tree


def project_fields(value, tree):
    if not tree:
        return value
    if not isinstance(value, dict):
        return None
    result = {}
    for part, children in tree.items():
        key = part.removesuffix("[]")
        item = value.get(key)
        if part.endswith("[]"):
            result[key] = [project_fields(child, children) for child in item] if isinstance(item, list) else None
        else:
            result[key] = project_fields(item, children)
    for key in ("ok", "error", "truncated", "inspection_truncated", "breakpoints_truncated", "next_start"):
        if key in value:
            result[key] = value[key]
    return result


def parse(argv):
    parser = Parser(prog="aidebug", description="Local Python, JS/TS, and Rust debugger. Every command prints JSON.",
                    epilog=USAGE_LOOP, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", default=os.getcwd(), help="session workspace, default: current directory")
    parser.add_argument("--timeout", type=float, default=10, help="deadline: 0.01..60 seconds; wait allows up to 3600")
    parser.add_argument("--fields", type=fields, help="project JSON stdout using complete comma-separated paths, e.g. state,stack.frames[].name; put before command")
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("launch", help="launch a script, executable, or local browser URL",
                            epilog=POWERSHELL_USAGE, formatter_class=argparse.RawDescriptionHelpFormatter)
    launch.add_argument("--cwd", help="target working directory, default: workspace")
    launch.add_argument("--python", help="target interpreter; default: VIRTUAL_ENV (except aidebug's own), workspace .venv, then CLI interpreter")
    launch.add_argument("--module", action="store_true", help="launch the positional name as a Python module")
    launch.add_argument("--language", choices=["python", "javascript", "typescript", "js", "ts", "rust"], help="infer from script extension when omitted")
    launch.add_argument("--target", choices=["process", "node", "browser"])
    launch.add_argument("--adapter", help="CodeLLDB executable or standalone dapDebugServer.js")
    launch.add_argument("--node", help="Node executable for the JS adapter and Node targets")
    launch.add_argument("--browser", help="Chrome/Chromium/Edge executable")
    launch.add_argument("--headed", action="store_true", help="show the launched browser window for UI interaction")
    launch.add_argument("--web-root", help="browser URL paths map to this directory")
    launch.add_argument("--out-files", action="append", default=[], help="generated JS glob for source maps; repeatable")
    launch.add_argument("--breakpoint", action="append", default=[], help="set FILE:LINE before execution; repeatable")
    launch.add_argument("--condition", type=condition, help="condition for all launch breakpoints; may execute target code")
    launch.add_argument("--run", action="store_true", help="run without stopping at entry")
    launch.add_argument("--no-wait", action="store_true", help="start asynchronously; use wait or status for progress")
    launch.add_argument("--disable-gpu", action="store_true", help="explicit software-rendering option for browser launches")
    launch.add_argument("--url-path", action="append", default=[], metavar="URL_PATH=LOCAL_FILE", help="explicit browser source mapping; repeatable")
    launch.add_argument("--uncaught", action="store_true", help="stop on uncaught exceptions, or Rust panics")
    launch.add_argument("--no-record", action="store_true", help="disable saved observations, notes and viewer activity for this launch")
    launch.add_argument("script")
    launch.add_argument("args", nargs=argparse.REMAINDER)
    bp = sub.add_parser("break", help="add or remove breakpoints")
    bp.add_argument("action", choices=["set", "remove"])
    bp.add_argument("file")
    bp.add_argument("lines", type=positive, nargs="*")
    bp.add_argument("--condition", type=condition, help="condition for the given lines; omit to make them unconditional")
    exceptions = sub.add_parser("exceptions")
    exceptions.add_argument("mode", choices=["on", "off"])
    sub.add_parser("continue")
    sub.add_parser("pause", help="request a stop in the running target")
    sub.add_parser("wait", help="wait for a stop or session end without blocking other commands")
    sub.add_parser("automation", help="request a private CDP connection to the launched browser page")
    sources = sub.add_parser("sources", help="list adapter-loaded sources and breakpoint binding results")
    sources.add_argument("--filter", default="", help="case-insensitive source path substring")
    sources.add_argument("--limit", type=positive, default=20)
    sources.add_argument("--start", type=int, default=0)
    doctor = sub.add_parser("doctor", help="check local tool paths without starting a program",
                            epilog=ADAPTER_SETUP, formatter_class=argparse.RawDescriptionHelpFormatter)
    doctor.add_argument("--language", choices=["python", "javascript", "typescript", "rust"], default="python")
    doctor.add_argument("--target", choices=["process", "node", "browser"])
    for flag in ("python", "adapter", "browser", "node"):
        doctor.add_argument("--" + flag)
    step = sub.add_parser("step")
    step.add_argument("kind", choices=["into", "over", "out"])
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--frame", help="frame handle from the current stop")
    inspect.add_argument("--limit", type=positive, default=20)
    scopes = sub.add_parser("scopes", help="list all scopes of the selected stopped frame")
    scopes.add_argument("--frame")
    value = sub.add_parser("value", help="select named children through DAP variables, without evaluating expressions")
    value.add_argument("path", nargs="?", default="", help="dot-separated variable names; use repeated --key for exact names")
    value.add_argument("--scope", required=True, help="scope or variable handle from the current stop")
    value.add_argument("--key", action="append", default=[], help="additional exact child name, including numeric indices")
    variables = sub.add_parser("variables")
    variables.add_argument("handle")
    variables.add_argument("--start", type=int, default=0)
    variables.add_argument("--limit", type=positive, default=20)
    for name in ("status", "output", "stop"):
        sub.add_parser(name)
    note = sub.add_parser("note", help="record an explanation alongside debugger observations")
    note.add_argument("text")
    view = sub.add_parser("view", help="start or locate the local live/replay viewer")
    view.add_argument("--open", action="store_true", help="open the viewer in your browser")
    view.add_argument("--stop", action="store_true", help="stop only the viewer server")
    recordings = sub.add_parser("recordings", help="remove old recordings from this workspace", description="Prune saved recordings after stopping the debug session. Unrelated files and links are preserved.")
    recordings.add_argument("action", choices=["prune"])
    recordings.add_argument("--older-than", type=int, required=True, metavar="DAYS", help="days since last write, 0 removes all recordings; range 0..365000")
    recordings.add_argument("--dry-run", action="store_true", help="report matching file count and bytes without deleting recordings")
    skills = sub.add_parser("skills", help="install, inspect, or remove agent skills")
    skills.add_argument("action", choices=["install", "status", "uninstall"])
    skills.add_argument("--agent", action="append", choices=["codex", "claude-code"], help="repeat for both agents; status defaults to both")
    skills.add_argument("--scope", choices=["user", "project"], default="user")
    args = vars(parser.parse_args(argv))
    maximum = 3600 if args["command"] == "wait" else 60
    if not math.isfinite(args["timeout"]) or not 0.01 <= args["timeout"] <= maximum:
        raise DebugError("invalid_request", f"Timeout must be between 0.01 and {maximum} seconds")
    if args["command"] == "value":
        args["keys"] = (args["path"].split(".") if args["path"] else []) + args["key"]
        if not 1 <= len(args["keys"]) <= 16 or any(not key or len(key) > 256 for key in args["keys"]):
            raise DebugError("invalid_request", "Value paths require 1..16 nonempty names of at most 256 characters")
    if args.get("limit", 20) > 30 or args.get("start", 0) < 0:
        raise DebugError("invalid_request", "Limit must be 1..30 and start must be nonnegative")
    if args["command"] == "recordings" and not 0 <= args["older_than"] <= 365000:
        raise DebugError("invalid_request", "Recording age must be 0..365000 days")
    if args["command"] == "note" and (not args["text"].strip() or len(args["text"]) > 4000):
        raise DebugError("invalid_request", "Notes must contain 1..4000 characters")
    workspace = Path(args["workspace"]).resolve()
    if not workspace.is_dir():
        raise DebugError("invalid_request", "Workspace directory does not exist")
    args["workspace"] = str(workspace)
    if args["command"] == "skills" and args["action"] != "status" and not args["agent"]:
        raise DebugError("invalid_request", "Skill install/uninstall requires --agent codex or --agent claude-code")
    if args["command"] == "launch":
        launch_options.resolve(args)
    if args["command"] == "break":
        if args["action"] != "set" and args["condition"] is not None:
            raise DebugError("invalid_request", "--condition requires break set")
        args["file"] = str((workspace / args["file"]).resolve())
        if args["action"] == "set" and (not args["lines"] or not Path(args["file"]).is_file()):
            raise DebugError("invalid_request", "Setting breakpoints requires an existing file and positive lines")
        if len(args["lines"]) > 100:
            raise DebugError("invalid_request", "At most 100 breakpoint lines per command")
    return args


def protect(directory):
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise DebugError("unsafe_state", "Session directory must not be a link or junction")
    if os.name == "nt":
        # Apply an exact protected DACL, including inheritance for future files.
        from ctypes import wintypes as w
        sid = next(csv.reader(subprocess.check_output(
            ["whoami", "/user", "/fo", "csv", "/nh"], **hidden()).decode().splitlines()))[1]
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        advapi.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p]
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        descriptor = ctypes.c_void_p()
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(f"D:P(A;OICI;FA;;;{sid})", 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not advapi.SetFileSecurityW(str(directory), 0x80000004, descriptor):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.LocalFree(descriptor)
    else:
        if info.st_uid != os.getuid():
            raise DebugError("unsafe_state", "Session directory belongs to another user")
        directory.chmod(0o700)


def lock_workspace(directory, name="lock"):
    file = (directory / name).open("a+b")
    try:
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        return None
    return file


def read_json(stream, limit):
    data = stream.readline(limit + 1)
    if len(data) > limit or not data.endswith(b"\n"):
        raise DebugError("invalid_request", "Missing newline or oversized JSON message")
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise DebugError("invalid_request", "Malformed JSON") from exc
    if not isinstance(result, dict):
        raise DebugError("invalid_request", "Expected a JSON object")
    return result


def rpc(metadata, argv, timeout):
    with socket.create_connection(("127.0.0.1", metadata["port"]), timeout=2) as sock:
        sock.settimeout(timeout + 8)
        request = json.dumps({"token": metadata["token"], "argv": argv}).encode() + b"\n"
        if len(request) > MAX_REQUEST:
            raise DebugError("invalid_request", "Command exceeds 32 KiB")
        sock.sendall(request)
        with sock.makefile("rb") as stream:
            return read_json(stream, MAX_RESPONSE)


def serve(workspace):
    directory = Path(workspace) / ".aidebug"
    protect(directory)
    lock = lock_workspace(directory)
    if not lock:
        return
    metadata_path = directory / "session.json"
    token = secrets.token_hex(32)
    session = Session()
    commands = threading.Lock()
    waiter = threading.Lock()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(2)
            stop = False
            try:
                request = read_json(self.rfile, MAX_REQUEST)
                if not isinstance(request.get("token"), str) or not hmac.compare_digest(request["token"], token):
                    raise DebugError("unauthorized", "Invalid session token")
                argv = request.get("argv")
                if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
                    raise DebugError("invalid_request", "argv must be an array of strings")
                args = parse(argv)
                if args["workspace"] != workspace:
                    raise DebugError("invalid_request", "Workspace does not match this session")
                selected_lock = waiter if args["command"] == "wait" else commands
                if not selected_lock.acquire(blocking=False):
                    raise DebugError("busy", "Another wait is active; use status, pause or stop" if args["command"] == "wait"
                                     else "Another command is active; retry after its deadline")
                try:
                    result = session.command(args)
                    stop = args["command"] == "stop"
                finally:
                    selected_lock.release()
            except DebugError as exc:
                result = session.status()
                result.update(ok=False, error=exc.as_dict())
            except (Exception, SystemExit) as exc:
                result = {"ok": False, "state": session.state,
                          "error": {"code": "invalid_request", "message": str(exc)[:512]}}
            try:
                self.wfile.write(encode(result))
            except OSError:
                pass
            if stop:
                threading.Thread(target=server.shutdown, daemon=True).start()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = False
        clients = threading.BoundedSemaphore(16)

        def process_request(self, request, address):
            if self.clients.acquire(blocking=False):
                super().process_request(request, address)
            else:
                self.shutdown_request(request)

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.clients.release()

    try:
        with Server(("127.0.0.1", 0), Handler) as server:
            metadata = {"port": server.server_address[1], "token": token, "pid": os.getpid(), "session": session.id}
            temp = directory / "session.tmp"
            temp.write_text(json.dumps(metadata), encoding="utf-8")
            temp.chmod(0o600)
            temp.replace(metadata_path)
            server.serve_forever(poll_interval=0.05)
    finally:
        session.close()
        if session.recording:
            session.recording.close(session.status())
        remove_state(metadata_path)
        lock.close()


def metadata(directory):
    try:
        data = json.loads((directory / "session.json").read_text(encoding="utf-8"))
        if not isinstance(data.get("port"), int) or not 1 <= data["port"] <= 65535 or not isinstance(data.get("token"), str):
            return None
        return data
    except (OSError, ValueError, AttributeError):
        return None


def remove_state(path):
    deadline = time.monotonic() + 2
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            # Windows readers can temporarily deny deletion despite the OS lock.
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def run(argv, args):
    if args["command"] == "doctor":
        return launch_options.doctor(args)
    if args["command"] == "recordings":
        from .recording import prune
        return prune(args)
    if args["command"] == "skills":
        from .skills import manage
        return manage(args)
    if args["command"] == "view":
        from .viewer import start
        return start(args)
    # The helper has its own cwd. Forward the caller's resolved launch paths.
    argv = ["--workspace", args["workspace"], *argv]
    if args["command"] == "launch":
        argv = launch_options.arguments(args)
    directory = Path(args["workspace"]) / ".aidebug"
    info = metadata(directory)
    if info:
        try:
            result = rpc(info, argv, args["timeout"])
            if args["command"] == "stop" and result.get("ok"):
                end = time.monotonic() + 5
                while (directory / "session.json").exists() and time.monotonic() < end:
                    time.sleep(0.02)
                if metadata(directory) == info:
                    raise DebugError("cleanup_failed", "Helper did not remove session state within 5 seconds")
            return result
        except OSError:
            pass
    if directory.exists():
        protect(directory)
        lock = lock_workspace(directory)
        if not lock:
            raise DebugError("session_unavailable", "Helper owns the workspace but its control channel is unavailable")
        try:
            remove_state(directory / "session.json")
        finally:
            lock.close()
    if args["command"] == "stop":
        return {"ok": True, "state": "exited", "phase": "closed", "stale_state_removed": True}
    if args["command"] != "launch":
        raise DebugError("no_session", "No live session; launch a program first")
    protect(directory)
    # Helpers compete for the OS lock; only the winner can publish its token.
    with (directory / "helper.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "aidebug.cli", "_serve", args["workspace"]],
            cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
            **(hidden() if os.name == "nt" else {"start_new_session": True}))
    end = time.monotonic() + 5
    while time.monotonic() < end:
        info = metadata(directory)
        if info:
            return rpc(info, argv, args["timeout"])
        if process.poll() is not None and process.returncode != 0:
            message = (directory / "helper.log").read_text(errors="replace")[-512:]
            raise DebugError("helper_failure", message or "Helper failed to start")
        time.sleep(0.03)
    raise DebugError("helper_failure", "Helper did not publish its local endpoint within 5 seconds")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "_view":
        from .viewer import serve as serve_viewer
        serve_viewer(sys.argv[2])
        return
    if len(sys.argv) == 3 and sys.argv[1] == "_serve":
        serve(sys.argv[2])
        return
    selection = None
    try:
        args = parse(sys.argv[1:])
        selection = args.pop("fields")
        result = run(sys.argv[1:], args)
    except DebugError as exc:
        result = {"ok": False, "state": "unknown", "error": exc.as_dict()}
    except Exception as exc:
        result = {"ok": False, "state": "unknown", "error": {"code": "control_failure", "message": str(exc)[:512]}}
    sys.stdout.write(encode(project_fields(result, selection)).decode("ascii"))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
