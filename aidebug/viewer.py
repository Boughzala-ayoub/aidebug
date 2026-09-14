"""Protected loopback HTTP viewer. It reads recordings, never controls DAP."""

import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import urllib.request
import webbrowser

from .dap import DebugError, hidden
from .recording import MAX_EVENT, MAX_RECORDING


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def record_path(directory, identity):
    if not re.fullmatch(r"[a-f0-9]{16}", identity):
        raise ValueError("Invalid recording ID")
    folder = directory / "recordings"
    file = folder / f"{identity}.jsonl"
    if folder.is_symlink() or folder.resolve().parent != directory.resolve() or file.is_symlink() or file.resolve().parent != folder.resolve():
        raise ValueError("Recording links are not allowed")
    return file


def sessions(directory):
    active = read_json(directory / "session.json")
    if not alive(active.get("pid")):
        active = {}
    files = sorted((directory / "recordings").glob("*.jsonl"), key=lambda file: file.stat().st_mtime, reverse=True)
    items = []
    for file in files[:100]:
        try:
            file = record_path(directory, file.stem)
            with file.open("rb") as stream:
                header = json.loads(stream.readline(MAX_EVENT + 1))
                stream.seek(max(0, file.stat().st_size - 512))
                tail = stream.read(512).splitlines()
            try:
                last = json.loads(tail[-1]) if tail else {}
            except ValueError:
                last = {}  # A large or incomplete last observation is normal while recording.
            if header.get("kind") != "session":
                continue
            items.append({"id": file.stem, "program": header.get("program", ""), "language": header.get("language"),
                          "target": header.get("target"), "time": header.get("time"), "bytes": file.stat().st_size,
                          "active": active.get("session") == file.stem,
                          "ended": last.get("kind") == "end", "limited": last.get("kind") == "limit",
                          "exit_code": last.get("exit_code")})
        except (OSError, ValueError, KeyError):
            pass
    return {"sessions": items, "truncated": len(files) > 100}


def events(directory, identity, after):
    if not 0 <= after <= MAX_RECORDING:
        raise ValueError("Invalid recording cursor")
    file = record_path(directory, identity)
    items, size = [], 0
    with file.open("rb") as stream:
        if after:
            stream.seek(after - 1)
            if stream.read(1) != b"\n":
                raise ValueError("Cursor must follow a complete observation")
        while len(items) < 128 and size < 256 * 1024:
            start = stream.tell()
            line = stream.readline(MAX_EVENT + 1)
            if not line:
                break
            if len(line) > MAX_EVENT:
                raise ValueError("Oversized recording observation")
            if not line.endswith(b"\n"):
                stream.seek(start)
                break
            items.append(json.loads(line))
            size += len(line)
        cursor = stream.tell()
    return {"events": items, "next": cursor, "more": cursor < file.stat().st_size and bool(items)}


def serve(workspace):
    from .cli import protect, lock_workspace, remove_state
    directory = Path(workspace) / ".aidebug"
    protect(directory)
    lock = lock_workspace(directory, "viewer.lock")
    if not lock:
        return
    token = secrets.token_hex(32)
    assets = Path(__file__).parent / "web"
    metadata = directory / "viewer.json"

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *args):
            pass

        def reply(self, status, data, content_type="application/json"):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            return secrets.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode())

        def local(self):
            host = f"127.0.0.1:{self.server.server_port}"
            return self.headers.get("Host") == host and self.headers.get("Origin", "http://" + host) == "http://" + host

        def do_GET(self):
            try:
                if not self.local():
                    return self.reply(403, {"error": "Local origin required"})
                url = urlsplit(self.path)
                static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8"),
                          "/sans.ttf": ("sans.ttf", "font/ttf"), "/mono.ttf": ("mono.ttf", "font/ttf")}
                if url.path in static:
                    name, content_type = static[url.path]
                    return self.reply(200, (assets / name).read_bytes(), content_type)
                if not self.authorized():
                    return self.reply(401, {"error": "Viewer token required. Run aidebug view and use its URL."})
                if url.path == "/api/health":
                    return self.reply(200, {"ok": True, "pid": os.getpid()})
                if url.path == "/api/sessions":
                    return self.reply(200, sessions(directory))
                match = re.fullmatch(r"/api/sessions/([a-f0-9]{16})/events", url.path)
                if match:
                    after = int(parse_qs(url.query).get("after", ["0"])[0])
                    return self.reply(200, events(directory, match[1], after))
                self.reply(404, {"error": "Not found"})
            except FileNotFoundError:
                self.reply(404, {"error": "Recording not found"})
            except (ValueError, KeyError):
                self.reply(400, {"error": "Invalid or damaged recording request"})
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            except OSError:
                self.reply(500, {"error": "Could not read the recording"})

        def do_POST(self):
            if not self.local() or not self.authorized():
                return self.reply(403, {"error": "Authorized local request required"})
            if self.path != "/api/shutdown":
                return self.reply(404, {"error": "Not found"})
            self.reply(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    class Server(ThreadingHTTPServer):
        daemon_threads = True
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
            temporary = directory / "viewer.tmp"
            temporary.write_text(json.dumps({"port": server.server_port, "pid": os.getpid(), "token": token}), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(metadata)
            server.serve_forever(poll_interval=0.1)
    finally:
        remove_state(metadata)
        lock.close()


def start(args):
    from .cli import protect, lock_workspace, remove_state
    directory = Path(args["workspace"]) / ".aidebug"
    protect(directory)
    path = directory / "viewer.json"

    def request(info, route, method="GET"):
        port, token = info["port"], info["token"]
        if not isinstance(port, int) or not 1 <= port <= 65535 or not isinstance(token, str):
            raise ValueError("Invalid viewer metadata")
        req = urllib.request.Request(f"http://127.0.0.1:{port}" + route, method=method,
                                     headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=2) as response:
            return json.load(response)

    info = read_json(path)
    try:
        request(info, "/api/health")
    except (OSError, ValueError, KeyError):
        lock = lock_workspace(directory, "viewer.lock")
        if not lock:
            raise DebugError("viewer_unavailable", "Viewer owns its lock but did not answer")
        try:
            remove_state(path)
        finally:
            lock.close()
        info = None
    if args["stop"]:
        if info:
            request(info, "/api/shutdown", "POST")
            deadline = time.monotonic() + 5
            while path.exists() and time.monotonic() < deadline:
                time.sleep(0.03)
            if path.exists():
                raise DebugError("cleanup_failure", "Viewer did not remove its state")
        return {"ok": True, "viewer": "stopped", "recordings_preserved": True}
    if not info:
        with (directory / "viewer.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-m", "aidebug.cli", "_view", args["workspace"]],
                cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                **(hidden() if os.name == "nt" else {"start_new_session": True}))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            info = read_json(path)
            if info:
                break
            if process.poll() not in (None, 0):
                raise DebugError("viewer_failure", "Viewer failed to start; see .aidebug/viewer.log")
            time.sleep(0.03)
        if not info:
            raise DebugError("viewer_failure", "Viewer did not publish its endpoint")
        request(info, "/api/health")
    url = f"http://127.0.0.1:{info['port']}/#token={info['token']}"
    if args["open"]:
        webbrowser.open(url)
    return {"ok": True, "viewer": "running", "url": url, "pid": info["pid"], "workspace": args["workspace"]}
