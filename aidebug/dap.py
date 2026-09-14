"""DAP framing, response matching, and owned adapter processes."""

import ctypes
import io
import json
import os
import secrets
import signal
import socket
import subprocess
import threading
import time


class DebugError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def hidden():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class WindowsPipe(io.RawIOBase):
    """Overlapped duplex I/O: a pending read must not block a DAP write."""

    def __init__(self, path):
        import _winapi
        self.api = _winapi
        self.handle = _winapi.CreateFile(path, 0xC0000000, 0, 0, 3, 0x40000000, 0)

    def readable(self):
        return True

    def readinto(self, buffer):
        operation, _ = self.api.ReadFile(self.handle, len(buffer), overlapped=True)
        count, error = operation.GetOverlappedResult(True)
        if error:
            raise OSError(error, "Named pipe read failed")
        buffer[:count] = operation.getbuffer()
        return count

    def write(self, data):
        operation, _ = self.api.WriteFile(self.handle, data, overlapped=True)
        count, error = operation.GetOverlappedResult(True)
        if error:
            raise OSError(error, "Named pipe write failed")
        return count

    def close(self):
        if not self.closed and hasattr(self, "handle"):
            self.api.CloseHandle(self.handle)
        super().close()


class ProcessTree:
    """A Windows kill-on-close job, or a POSIX process group."""

    def __init__(self):
        self.job = None
        self.process = None
        if os.name == "nt":
            from ctypes import wintypes as w

            class Basic(ctypes.Structure):
                _fields_ = [("per_process", ctypes.c_int64), ("per_job", ctypes.c_int64),
                            ("flags", w.DWORD), ("min_ws", ctypes.c_size_t),
                            ("max_ws", ctypes.c_size_t), ("active", w.DWORD),
                            ("affinity", ctypes.c_size_t), ("priority", w.DWORD),
                            ("scheduling", w.DWORD)]

            class Extended(ctypes.Structure):
                _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                            ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
            self.kernel.CreateJobObjectW.restype = w.HANDLE
            self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
            self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
            self.kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
            self.kernel.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
            self.kernel.CloseHandle.argtypes = [w.HANDLE]
            self.job = self.kernel.CreateJobObjectW(None, None)
            info = Extended()
            info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.job or not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                self.close()
                raise ctypes.WinError(ctypes.get_last_error())

    def start(self, command):
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **(hidden() if os.name == "nt" else {"start_new_session": True}))
        if self.job and not self.kernel.AssignProcessToJobObject(self.job, int(self.process._handle)):
            self.process.kill()
            self.process.wait()
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        return self.process

    def close(self):
        if self.job:
            # Waiting only for the adapter leaves a race with browser descendants
            # still releasing profile files. Wait for the owned job to drain first.
            class Accounting(ctypes.Structure):
                _fields_ = [("times", ctypes.c_int64 * 4), ("counts", ctypes.c_ulong * 4)]
            accounting = Accounting()
            self.kernel.TerminateJobObject(self.job, 1)
            deadline = time.monotonic() + 3
            while self.kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                if accounting.counts[2] == 0 or time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
            self.kernel.CloseHandle(self.job)
            self.job = None
        elif os.name != "nt" and self.process:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


class DAP:
    def __init__(self, event, failure, diagnostics, command=None, endpoint=None, reverse=None, connect_back=False, capture=None):
        self.event, self.failure = event, failure
        self.reverse = reverse
        self.lock = threading.Lock()
        self.pending = {}
        self.seq = 0
        self.closed = False
        self.tree = ProcessTree() if command else None
        self.socket = None
        listener = None
        try:
            if connect_back:
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen(4)
                token = secrets.token_hex(32)
                command = [*command, "--connect", str(listener.getsockname()[1]), "--auth-token", token]
            self.process = self.tree.start(command) if command else None
            if connect_back:
                deadline = time.monotonic() + 5
                while True:
                    listener.settimeout(max(0.01, deadline - time.monotonic()))
                    connection, _ = listener.accept()
                    connection.settimeout(max(0.01, deadline - time.monotonic()))
                    stream = connection.makefile("rwb", buffering=0)
                    try:
                        header = stream.readline(256)
                        if not secrets.compare_digest(header, f"Auth-Token: {token}\r\n".encode()):
                            raise ValueError("Invalid adapter authentication")
                    except (OSError, ValueError):
                        stream.close()
                        connection.close()
                        if time.monotonic() >= deadline:
                            raise DebugError("adapter_failure", "Adapter connection authentication timed out")
                        continue
                    connection.settimeout(None)
                    self.socket, self.input, self.output = connection, stream, stream
                    break
            elif endpoint:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        if os.name == "nt":
                            self.output = WindowsPipe(endpoint)
                            self.input = io.BufferedReader(self.output)
                        else:
                            self.socket = socket.socket(socket.AF_UNIX)
                            self.socket.connect(endpoint)
                            self.input = self.output = self.socket.makefile("rwb", buffering=0)
                        break
                    except OSError:
                        if self.socket:
                            self.socket.close()
                        if time.monotonic() >= deadline or (self.process and self.process.poll() is not None):
                            raise DebugError("adapter_failure", "Adapter did not open its local DAP endpoint")
                        time.sleep(0.025)
            else:
                self.input, self.output = self.process.stdout, self.process.stdin
        except Exception:
            if self.tree:
                self.tree.close()
            raise
        finally:
            if listener:
                listener.close()
        threading.Thread(target=self.read, daemon=True).start()

        def drain(stream, receive):
            while chunk := stream.read1(4096):
                receive(chunk.decode("utf-8", "replace"))
        if self.process:
            stderr = (lambda text: capture("stderr", text)) if connect_back else diagnostics
            threading.Thread(target=drain, args=(self.process.stderr, stderr), daemon=True).start()
            if endpoint or connect_back:
                stdout = (lambda text: capture("stdout", text)) if connect_back else diagnostics
                threading.Thread(target=drain, args=(self.process.stdout, stdout), daemon=True).start()

    def send(self, command, arguments=None):
        with self.lock:
            if self.closed:
                raise DebugError("adapter_failure", "Debug adapter is closed")
            self.seq += 1
            seq = self.seq
            slot = [threading.Event(), None]
            self.pending[seq] = slot
            try:
                self.write({"seq": seq, "type": "request", "command": command,
                            "arguments": arguments or {}})
            except (OSError, ValueError) as exc:
                self.pending.pop(seq, None)
                raise DebugError("adapter_failure", str(exc)) from exc
            return seq, slot

    def write(self, message):
        body = json.dumps(message).encode("utf-8")
        data = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        while data:
            written = self.output.write(data)
            if not written:
                raise OSError("DAP stream closed during write")
            data = data[written:]
        self.output.flush()

    def respond(self, request, success, message=None):
        with self.lock:
            self.seq += 1
            self.write({"seq": self.seq, "type": "response", "request_seq": request["seq"],
                        "command": request["command"], "success": success, "message": message})

    def reverse_request(self, request):
        try:
            if not self.reverse:
                raise DebugError("unsupported", "Reverse request is unsupported")
            self.reverse(self, request)
            self.respond(request, True)
        except Exception as exc:
            try:
                self.respond(request, False, str(exc)[:512])
            except OSError:
                pass

    def wait(self, ticket, deadline):
        seq, slot = ticket
        if not slot[0].wait(max(0, deadline - time.monotonic())):
            with self.lock:
                self.pending.pop(seq, None)
            raise DebugError("timeout", "Debug adapter request deadline expired")
        response = slot[1]
        if not response.get("success"):
            raise DebugError("adapter_failure", response.get("message", "Debug adapter rejected request"))
        return response.get("body", {})

    def request(self, command, arguments, deadline):
        if time.monotonic() >= deadline:
            raise DebugError("timeout", "Command deadline expired")
        return self.wait(self.send(command, arguments), deadline)

    def read(self):
        try:
            stream = self.input
            while True:
                headers = {}
                while True:
                    line = stream.readline(8193)
                    if not line:
                        raise EOFError("Debug adapter disconnected")
                    if len(line) > 8192:
                        raise ValueError("Oversized DAP header")
                    if line == b"\r\n":
                        break
                    if b":" not in line:
                        raise ValueError("Malformed DAP header: " + repr(line[:120]))
                    key, value = line.decode("ascii").split(":", 1)
                    headers[key.lower()] = value.strip()
                length = int(headers["content-length"])
                if not 0 < length <= 16 * 1024 * 1024:
                    raise ValueError("Oversized DAP message")
                chunks = []
                remaining = length
                while remaining:
                    chunk = stream.read(remaining)
                    if not chunk:
                        raise EOFError("Incomplete DAP message")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                body = b"".join(chunks)
                message = json.loads(body)
                if message["type"] == "response":
                    with self.lock:
                        slot = self.pending.pop(message["request_seq"], None)
                        if slot:
                            slot[1] = message
                            slot[0].set()
                elif message["type"] == "event":
                    self.event(message["event"], message.get("body", {}))
                elif message["type"] == "request":
                    threading.Thread(target=self.reverse_request, args=(message,), daemon=True).start()
        except Exception as exc:
            with self.lock:
                was_closed = self.closed
                self.closed = True
                for slot in self.pending.values():
                    slot[1] = {"success": False, "message": str(exc)}
                    slot[0].set()
                self.pending.clear()
            if not was_closed:
                self.failure(str(exc))

    def close(self):
        with self.lock:
            self.closed = True
        if self.tree:
            self.tree.close()
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.socket.close()
        # Kill/close the producer first so closing its buffered reader cannot hang.
        if self.tree or self.socket:
            for stream in {self.input, self.output}:
                try:
                    stream.close()
                except OSError:
                    pass
