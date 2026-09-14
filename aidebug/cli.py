"""JSON CLI and an authenticated, workspace-scoped local helper."""

import argparse
import csv
import ctypes
import hmac
import json
import math
import os
from pathlib import Path
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

USAGE_LOOP = """Agent debugging loop (run in the target project, or add --workspace PATH):
  aidebug view --open
  aidebug launch --module --run --breakpoint pricing.py:6 pytest -- -q test_checkout.py
  aidebug inspect
  aidebug inspect --frame FRAME_HANDLE
  aidebug variables VALUE_HANDLE
  aidebug note "Describe the observed values and suspected cause."
  aidebug step over
  aidebug continue
  aidebug output
  aidebug stop

Replace the test and breakpoint with your project paths. For a script, use
launch script.py; for unittest, use launch --module unittest -- -q test_module.
Pytest must be installed in the target interpreter. Test runners catch assertions;
--uncaught can stop at their final SystemExit instead. Set a breakpoint near the failure.
Python selection: --python, VIRTUAL_ENV, workspace .venv, then the CLI interpreter.
Check the response's python path. Every command returns JSON: check ok and state.
Handles expire when execution resumes. Expand only values needed for the investigation.
timed_out is not an exit: inspect status, or pause a running target, or stop it.
Wait for phase: ready before pause. Idle browsers may wait for the next JS task.
After editing source, stop and relaunch to verify. Recordings remain available.
Conditional stop: break set pricing.py 6 --condition "count == 437"
Conditions use the adapter's expression syntax and can execute target code.
No arbitrary evaluate command is provided. See launch --help and break --help.
"""


def encode(result):
    data = json.dumps(result, ensure_ascii=True).encode() + b"\n"
    if len(data) > MAX_RESPONSE:
        result = dict(result)
        for key in ("output", "locals", "variables", "stack", "location", "breakpoints"):
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


def parse(argv):
    parser = Parser(prog="aidebug", description="Local Python, JS/TS, and Rust debugger. Every command prints JSON.",
                    epilog=USAGE_LOOP, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", default=os.getcwd(), help="session workspace, default: current directory")
    parser.add_argument("--timeout", type=float, default=10, help="command deadline in seconds, 0.01 to 60")
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("launch", help="launch a script, executable, or local browser URL")
    launch.add_argument("--cwd", help="target working directory, default: workspace")
    launch.add_argument("--python", help="target interpreter; default: VIRTUAL_ENV, workspace .venv, then CLI interpreter")
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
    launch.add_argument("--uncaught", action="store_true", help="stop on uncaught exceptions, or Rust panics")
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
    step = sub.add_parser("step")
    step.add_argument("kind", choices=["into", "over", "out"])
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--frame", help="frame handle from the current stop")
    inspect.add_argument("--limit", type=positive, default=20)
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
    args = vars(parser.parse_args(argv))
    if not math.isfinite(args["timeout"]) or not 0.01 <= args["timeout"] <= 60:
        raise DebugError("invalid_request", "Timeout must be between 0.01 and 60 seconds")
    if args.get("limit", 20) > 30 or args.get("start", 0) < 0:
        raise DebugError("invalid_request", "Limit must be 1..30 and start must be nonnegative")
    if args["command"] == "note" and (not args["text"].strip() or len(args["text"]) > 4000):
        raise DebugError("invalid_request", "Notes must contain 1..4000 characters")
    workspace = Path(args["workspace"]).resolve()
    if not workspace.is_dir():
        raise DebugError("invalid_request", "Workspace directory does not exist")
    args["workspace"] = str(workspace)
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
                if not commands.acquire(blocking=False):
                    raise DebugError("busy", "Another command is active; retry after its deadline")
                try:
                    result = session.command(args)
                    stop = args["command"] == "stop"
                finally:
                    commands.release()
            except DebugError as exc:
                result = session.status()
                result.update(ok=False, error={"code": exc.code, "message": str(exc)[:512]})
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


def run(argv):
    args = parse(argv)
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
    try:
        result = run(sys.argv[1:])
    except DebugError as exc:
        result = {"ok": False, "state": "unknown", "error": {"code": exc.code, "message": str(exc)[:512]}}
    except Exception as exc:
        result = {"ok": False, "state": "unknown", "error": {"code": "control_failure", "message": str(exc)[:512]}}
    sys.stdout.write(encode(result).decode("ascii"))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
