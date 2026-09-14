"""Append-only, bounded observations. Recording never sends debugger requests."""

import hashlib
import json
import os
from pathlib import Path
import threading
import time
import tokenize


MAX_RECORDING = 32 * 1024 * 1024
MAX_EVENT = 128 * 1024


class Recording:
    def __init__(self, args, session):
        directory = Path(args["workspace"]) / ".aidebug" / "recordings"
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.resolve().parent != directory.parent.resolve():
            raise ValueError("Recording directory must not be a link or junction")
        self.path = directory / f"{session}.jsonl"
        self.stream = self.path.open("xb", buffering=0)
        self.path.chmod(0o600)
        self.lock = threading.RLock()
        self.started = time.monotonic()
        self.seq = 0
        self.size = 0
        self.full = False
        self.error = None
        self.sources = {}
        self.append("session", session=session, program=args["script"], language=args["language"],
                    target=args["target"], cwd=args["cwd"], helper=os.getpid(), version=1)

    def append(self, kind, **fields):
        with self.lock:
            if self.stream.closed or self.full or self.error:
                return
            self.seq += 1
            event = {"seq": self.seq, "time": time.time(), "elapsed": round(time.monotonic() - self.started, 3),
                     "kind": kind, **fields}
            data = json.dumps(event, ensure_ascii=True).encode() + b"\n"
            if len(data) > MAX_EVENT:
                event = {k: event[k] for k in ("seq", "time", "elapsed", "kind")}
                event.update(truncated=True, message="Observation exceeded 128 KiB")
                data = json.dumps(event).encode() + b"\n"
            if self.size + len(data) > MAX_RECORDING - 1024:
                event = {"seq": self.seq, "time": time.time(), "elapsed": event["elapsed"], "kind": "limit",
                         "truncated": True, "message": "Recording reached 32 MiB; later activity is not recorded"}
                data = json.dumps(event).encode() + b"\n"
                self.full = True
            try:
                while data:
                    written = self.stream.write(data)
                    if not written:
                        raise OSError("Recording stream stopped accepting data")
                    self.size += written
                    data = data[written:]
            except OSError as exc:
                self.error = str(exc)[:512]

    def source(self, file):
        if not file or self.full or self.error:
            return
        try:
            with (tokenize.open(file) if Path(file).suffix == ".py" else open(file, encoding="utf-8-sig", errors="replace")) as source:
                text = source.read(24001)
            cut = len(text) > 24000
            text = text[:24000]
            digest = hashlib.sha256(text.encode()).hexdigest()
            with self.lock:
                if self.sources.get(file) != digest:
                    changed = file in self.sources
                    self.sources[file] = digest
                    self.append("source", file=file, text=text, changed=changed, truncated=cut)
        except (OSError, UnicodeError, SyntaxError):
            pass

    def close(self, outcome=None):
        with self.lock:
            if not self.stream.closed:
                self.append("end", message="Session closed", exit_code=(outcome or {}).get("exit_code"))
                self.stream.close()
