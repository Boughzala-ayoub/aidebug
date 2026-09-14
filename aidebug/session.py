"""One live debug session; only DAP stack/scopes/variables for inspection."""

import os
from pathlib import Path
import secrets
import shutil
import threading
import time
import tokenize

from .dap import DAP, DebugError
from .adapters import get as get_adapter
from .recording import Recording


def clipped(value, limit=512):
    value = str(value)
    return value[:limit], len(value) > limit


class Session:
    def __init__(self):
        self.cv = threading.Condition(threading.RLock())
        self.state = "exited"
        self.phase = "idle"
        self.reason = None
        self.error = None
        self.exit_code = None
        self.thread = None
        self.epoch = 0
        self.id = secrets.token_hex(8)
        self.handles = {}
        self.frames = {}
        self.breakpoints = {}
        self.dap = None
        self.root_dap = None
        self.adapter = None
        self.language = None
        self.target = None
        self.launch_args = None
        self.endpoint = None
        self.child_claimed = False
        self.capabilities = {}
        self.initialized = threading.Event()
        self.output = {"stdout": "", "stderr": "", "adapter": ""}
        self.output_dropped = dict.fromkeys(self.output, 0)
        self.target_pid = None
        self.closing = False
        self.launched = False
        self.recording = None

    def capture(self, category, text):
        if category == "console":
            category = "stdout"
        if category not in self.output:
            return
        with self.cv:
            combined = self.output[category] + text
            self.output_dropped[category] += max(0, len(combined) - 8192)
            self.output[category] = combined[-8192:]
            if self.recording:
                self.recording.append("output", category=category, text=text[-8192:], truncated=len(text) > 8192)

    def invalidate(self):
        self.epoch += 1
        self.handles.clear()
        self.frames.clear()

    def event(self, name, body):
        with self.cv:
            if name == "initialized":
                self.initialized.set()
            elif name == "stopped" and not self.closing:
                self.invalidate()
                self.state, self.phase = "stopped", "ready"
                self.reason = body.get("reason")
                self.thread = body.get("threadId")
            elif name == "continued" and self.state == "stopped":
                self.invalidate()
                self.state = "running"
            elif name == "exited":
                self.exit_code = body.get("exitCode")
                self.state, self.phase = "exited", "done"
                self.invalidate()
            elif name == "terminated" and self.state != "failed":
                self.state, self.phase = "exited", "done"
                self.invalidate()
            elif name == "process":
                self.target_pid = body.get("systemProcessId")
            elif name == "capabilities":
                self.capabilities.update(body.get("capabilities", {}))
            elif name == "output":
                self.capture(body.get("category"), body.get("output", ""))
            if self.recording and name in ("stopped", "continued", "exited", "terminated"):
                self.recording.append("event", event=name, state=self.state, reason=self.reason,
                                      generation=self.epoch, exit_code=self.exit_code)
            self.cv.notify_all()

    def failure(self, message):
        with self.cv:
            if not self.closing and self.state != "exited":
                self.state, self.phase = "failed", "done"
                self.error = {"code": "adapter_failure", "message": clipped(message)[0]}
                self.invalidate()
                if self.recording:
                    self.recording.append("error", state="failed", error=self.error)
            self.cv.notify_all()

    def status(self):
        with self.cv:
            result = {"ok": self.state != "failed", "state": self.state, "phase": self.phase,
                      "session": self.id, "generation": self.epoch, "reason": self.reason,
                      "exit_code": self.exit_code, "thread": self.thread,
                      "language": self.language, "target": self.target,
                      "processes": {"helper": os.getpid(), "adapter": self.root_dap.process.pid if self.root_dap else None,
                                    "target": self.target_pid}}
            if self.error:
                result["error"] = self.error
            if self.recording:
                result["recording"] = {"id": self.id, "truncated": self.recording.full, "error": self.recording.error}
            return result

    def handle(self, kind, reference):
        if reference is None or (kind == "v" and reference == 0):
            return None
        key = f"{self.id}:{self.epoch}:{kind}:{reference}"
        self.handles[key] = (kind, reference)
        return key

    def resolve(self, key, kind):
        if self.state != "stopped":
            raise DebugError("invalid_state", "Inspection requires a stopped session")
        if key not in self.handles or self.handles[key][0] != kind:
            raise DebugError("invalid_handle", "Unknown or expired handle; inspect the current stop again")
        return self.handles[key][1]

    def request(self, command, arguments, deadline):
        if not self.dap:
            raise DebugError("invalid_state", "No program has been launched")
        return self.dap.request(command, arguments, deadline)

    def launch(self, args):
        if self.launched:
            raise DebugError("session_exists", "Stop this session before launching another program")
        self.launched = True
        self.launch_args = args
        self.language, self.target = args["language"], args["target"]
        self.adapter = get_adapter(self.language)
        self.breakpoints = args["breakpoints"]
        self.state, self.phase = "running", "initializing"

        def setup():
            try:
                with self.cv:
                    if self.closing:
                        return
                    command = self.adapter.command(args)
                    if self.adapter.TRANSPORT == "ipc":
                        self.endpoint = (rf"\\.\pipe\aidebug-{self.id}-{secrets.token_hex(16)}" if os.name == "nt"
                                         else str(Path(args["workspace"]) / ".aidebug" / "adapter.sock"))
                        if os.name != "nt":
                            Path(self.endpoint).unlink(missing_ok=True)
                        command.append(self.endpoint)
                    args["profile"] = str(Path(args["workspace"]) / ".aidebug" / f"browser-{self.id}")
                    self.root_dap = self.dap = DAP(self.event, self.failure, lambda text: self.capture("adapter", text),
                        command=command, endpoint=self.endpoint, reverse=self.start_child,
                        connect_back=self.adapter.TRANSPORT == "connect", capture=self.capture)
                self.configure(self.root_dap, self.adapter.configuration(args), self.initialized)
                with self.cv:
                    if self.phase == "initializing":
                        self.phase = "ready"
                    self.cv.notify_all()
            except Exception as exc:
                self.failure(str(exc))
        threading.Thread(target=setup, daemon=True).start()

    def configure(self, dap, configuration, initialized, request="launch"):
        deadline = time.monotonic() + 30
        capabilities = dap.request("initialize", {"clientID": "aidebug", "adapterID": self.adapter.ID,
            "pathFormat": "path", "linesStartAt1": True, "columnsStartAt1": True,
            "supportsVariableType": True, "supportsVariablePaging": False,
            "supportsStartDebuggingRequest": bool(self.endpoint), "supportsRunInTerminalRequest": False}, deadline)
        self.capabilities = capabilities
        ticket = dap.send(request, {"name": "aidebug", "request": request, **configuration})
        while not initialized.wait(0.05):
            if ticket[1][0].is_set():
                dap.wait(ticket, deadline)
            if self.closing:
                return
            if time.monotonic() >= deadline:
                raise DebugError("adapter_failure", "Adapter did not initialize within 30 seconds")
        filters = self.adapter.exception_filters(self.launch_args["uncaught"])
        if filters or capabilities.get("exceptionBreakpointFilters"):
            dap.request("setExceptionBreakpoints", {"filters": filters}, deadline)
        for file, lines in list(self.breakpoints.items()):
            dap.request("setBreakpoints", {"source": {"path": file},
                "breakpoints": [{"line": line} for line in lines]}, deadline)
        if capabilities.get("supportsConfigurationDoneRequest"):
            dap.request("configurationDone", {}, deadline)
        dap.wait(ticket, deadline)

    def start_child(self, parent, request):
        configuration = request.get("arguments", {}).get("configuration", {})
        if request["command"] != "startDebugging" or not self.endpoint or not configuration.get("__pendingTargetId"):
            raise DebugError("unsupported", "Only the launched JavaScript target can create a child DAP session")
        with self.cv:
            if self.closing or self.child_claimed:
                raise DebugError("unsupported", "One debug target per workspace; workers and child processes are unsupported")
            self.child_claimed = True
        initialized = threading.Event()

        def event(name, body):
            if name == "initialized":
                initialized.set()
            else:
                self.event(name, body)
        try:
            child = DAP(event, self.failure, lambda text: self.capture("adapter", text),
                        endpoint=self.endpoint, reverse=self.start_child)
            with self.cv:
                if self.closing:
                    child.close()
                    return
                self.dap = child
            self.configure(child, configuration, initialized, request["arguments"].get("request", "launch"))
        except Exception as exc:
            self.failure(str(exc))
            raise

    def resume(self, command):
        if self.state != "stopped":
            raise DebugError("invalid_state", "Execution command requires a stopped session")
        thread = self.thread
        self.invalidate()
        self.state, self.reason = "running", None

        def execute():
            try:
                self.request(command, {"threadId": thread}, time.monotonic() + 30)
            except Exception as exc:
                self.failure(str(exc))
        threading.Thread(target=execute, daemon=True).start()

    def wait(self, deadline):
        with self.cv:
            while self.state == "running":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True
                self.cv.wait(remaining)
        return False

    def variables(self, reference, start, limit, deadline):
        # Advertise no client paging, then bound/page the returned children uniformly.
        # ponytail: adapters materialize children; native paging if large objects demand it.
        body = self.request("variables", {"variablesReference": reference}, deadline)
        raw = body.get("variables", [])[start:start + limit + 1]
        values = []
        for item in raw[:limit]:
            name, name_cut = clipped(item.get("name", ""), 128)
            value, value_cut = clipped(item.get("value", ""))
            type_name, type_cut = clipped(item.get("type", ""), 128)
            values.append({"name": name, "value": value, "type": type_name,
                           "handle": self.handle("v", item.get("variablesReference")),
                           "named": item.get("namedVariables"), "indexed": item.get("indexedVariables"),
                           "truncated": name_cut or value_cut or type_cut or "..." in value or "\u2026" in value})
        more = len(raw) > limit
        return {"items": values, "start": start, "truncated": more,
                "next_start": start + limit if more else None}

    def locals(self, frame, deadline, limit=20):
        scopes = self.request("scopes", {"frameId": frame}, deadline).get("scopes", [])
        local = next((s for s in scopes if s.get("presentationHint") == "locals" or s["name"] in ("Locals", "Local")), None)
        if not local:
            local = next((s for s in scopes if s.get("name") in ("Block", "Script", "Module") and not s.get("expensive")), None)
        if not local:
            return {"items": [], "truncated": False}
        ref = local["variablesReference"]
        result = self.variables(ref, 0, limit, deadline)
        result["handle"] = self.handle("v", ref)
        return result

    def inspect(self, deadline, frame_handle=None, limit=20):
        if self.state != "stopped":
            raise DebugError("invalid_state", "Inspection requires a stopped session")
        epoch = self.epoch
        threads = self.request("threads", {}, deadline).get("threads", [])
        if self.thread is None and threads:
            self.thread = threads[0]["id"]
        stack = self.request("stackTrace", {"threadId": self.thread, "startFrame": 0, "levels": 21}, deadline)
        frames = []
        raw = stack.get("stackFrames", [])
        for item in raw[:20]:
            self.frames[item["id"]] = item
            name, name_cut = clipped(item["name"], 128)
            file, file_cut = clipped(item.get("source", {}).get("path", ""), 1024)
            frames.append({"handle": self.handle("f", item["id"]), "name": name, "file": file,
                           "line": item["line"], "truncated": name_cut or file_cut})
        frame = self.resolve(frame_handle, "f") if frame_handle else (raw[0]["id"] if raw else None)
        # Some adapters allocate new IDs on every stackTrace request. A previously
        # returned frame remains usable until execution resumes.
        selected = self.frames.get(frame)
        result = {"stack": {"frames": frames, "truncated": len(raw) > 20 or stack.get("totalFrames", 0) > 20}}
        if selected:
            file = selected.get("source", {}).get("path", "")
            line = selected["line"]
            excerpt = []
            try:
                with (tokenize.open(file) if self.language == "python" else open(file, encoding="utf-8-sig", errors="replace")) as source:
                    for number, text in enumerate(source, 1):
                        if number > line + 2:
                            break
                        if number >= max(1, line - 2):
                            text, cut = clipped(text.rstrip(), 240)
                            excerpt.append({"line": number, "text": text, "truncated": cut})
            except (OSError, UnicodeError, SyntaxError):
                pass
            result["location"] = {"file": file, "line": line, "source": excerpt,
                                  "frame": self.handle("f", frame)}
            result["locals"] = self.locals(frame, deadline, limit)
        if self.epoch != epoch or self.state != "stopped":
            raise DebugError("state_changed", "Execution changed during inspection; request status again")
        return result

    def command(self, args):
        if args["command"] == "launch" and not self.launched:
            self.recording = Recording(args, self.id)
        if self.recording:
            # Record only public command arguments, never the control-channel token.
            public = {k: v for k, v in args.items() if k not in ("workspace", "breakpoints")}
            self.recording.append("command", command=args["command"], arguments=public)
        try:
            result = self.execute(args)
        except Exception as exc:
            if self.recording:
                self.recording.append("result", command=args["command"], response={**self.status(), "ok": False,
                    "error": {"code": getattr(exc, "code", "invalid_request"), "message": str(exc)[:512]}})
            raise
        if self.recording:
            self.recording.source(result.get("location", {}).get("file"))
            if args["command"] in ("note", "stop"):
                for file in list(self.recording.sources):
                    self.recording.source(file)
            self.recording.append("result", command=args["command"], handle=args.get("handle"), response=result)
            if args["command"] == "stop":
                self.recording.close(result)
        return result

    def execute(self, args):
        deadline = time.monotonic() + args["timeout"]
        command = args["command"]
        extra = {}
        timed_out = False
        if command == "launch":
            self.launch(args)
            timed_out = self.wait(deadline)
        elif command in ("continue", "step"):
            self.resume("continue" if command == "continue" else {"into": "stepIn", "over": "next", "out": "stepOut"}[args["kind"]])
            timed_out = self.wait(deadline)
        elif command == "break":
            if self.phase != "ready" or self.state not in ("running", "stopped"):
                raise DebugError("invalid_state", "Breakpoints require a configured live session")
            file = args["file"]
            lines = set(self.breakpoints.get(file, []))
            if args["action"] == "set":
                lines.update(args["lines"])
            elif args["lines"]:
                lines.difference_update(args["lines"])
            else:
                lines.clear()
            if len(lines) > 100:
                raise DebugError("invalid_request", "At most 100 breakpoints per file")
            response = self.request("setBreakpoints", {"source": {"path": file},
                "breakpoints": [{"line": line} for line in sorted(lines)]}, deadline)
            self.breakpoints[file] = sorted(lines)
            extra["breakpoints"] = [{"line": b.get("line"), "verified": b.get("verified", False),
                                      "message": clipped(b.get("message", ""))[0]}
                                     for b in response.get("breakpoints", [])]
        elif command == "exceptions":
            if self.phase != "ready" or self.state not in ("running", "stopped"):
                raise DebugError("invalid_state", "Exception settings require a configured live session")
            filters = self.adapter.exception_filters(args["mode"] == "on")
            offered = {f["filter"] for f in self.capabilities.get("exceptionBreakpointFilters", [])}
            if not set(filters).issubset(offered):
                raise DebugError("unsupported", "This adapter does not advertise the requested exception filter")
            self.request("setExceptionBreakpoints", {"filters": filters}, deadline)
            self.launch_args["uncaught"] = args["mode"] == "on"
            extra["uncaught"] = args["mode"] == "on"
        elif command == "variables":
            epoch = self.epoch
            reference = self.resolve(args["handle"], "v")
            extra["variables"] = self.variables(reference, args["start"], args["limit"], deadline)
            if epoch != self.epoch:
                raise DebugError("state_changed", "Execution changed during inspection")
        elif command == "output":
            with self.cv:
                extra["output"] = {key: {"text": value, "truncated": bool(self.output_dropped[key]),
                    "dropped_chars": self.output_dropped[key]} for key, value in self.output.items()}
        elif command == "stop":
            self.close()
        elif command == "note":
            if not self.recording:
                raise DebugError("invalid_state", "Launch a program before adding a note")
            self.recording.append("note", text=args["text"])
            extra["note"] = args["text"]
        elif command not in ("status", "inspect"):
            raise DebugError("invalid_request", "Unknown command")
        result = self.status()
        result.update(extra)
        result["timed_out"] = timed_out
        if command in ("launch", "continue", "step", "status", "inspect") and self.state == "stopped":
            try:
                result.update(self.inspect(deadline, args.get("frame"), args.get("limit", 20)))
            except DebugError as exc:
                if exc.code != "timeout":
                    raise
                result["inspection_truncated"] = True
                result["timed_out"] = True
        elif command == "inspect":
            raise DebugError("invalid_state", "Inspection requires a stopped session")
        return result

    def close(self):
        with self.cv:
            self.closing = True
            dap = self.dap
        if dap:
            try:
                dap.request("disconnect", {"terminateDebuggee": True}, time.monotonic() + 1)
            except (DebugError, OSError):
                pass
            dap.close()
        if self.root_dap and self.root_dap is not dap:
            self.root_dap.close()
        if self.endpoint and os.name != "nt":
            Path(self.endpoint).unlink(missing_ok=True)
        if self.target == "browser" and self.launch_args.get("profile"):
            profile = Path(self.launch_args["profile"])
            directory = Path(self.launch_args["workspace"]) / ".aidebug"
            # Delete only our generated profile, after terminating the browser job.
            if profile.resolve().parent != directory.resolve() or profile.is_symlink():
                raise DebugError("unsafe_state", "Browser profile moved outside the session directory")
            deadline = time.monotonic() + 5
            while profile.exists():
                try:
                    shutil.rmtree(profile)
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise DebugError("cleanup_failure", "Browser profile cleanup failed: " + clipped(exc)[0])
                    time.sleep(0.05)
        with self.cv:
            self.state, self.phase = "exited", "closed"
            self.invalidate()
            self.cv.notify_all()
