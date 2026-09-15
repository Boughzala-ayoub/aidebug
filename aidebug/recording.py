"""Append-only, bounded observations. Recording never sends debugger requests."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import tokenize


MAX_RECORDING = 32 * 1024 * 1024
MAX_EVENT = 128 * 1024


def prune(args):
    from .cli import protect, lock_workspace
    from .dap import DebugError

    directory = Path(args["workspace"]) / ".aidebug"
    result = {"ok": True, "action": "prune", "dry_run": args["dry_run"],
              "matched": 0, "matched_bytes": 0, "deleted": 0, "deleted_bytes": 0, "skipped": 0}
    if not os.path.lexists(directory):
        return result
    protect(directory)
    # ponytail: workspace lock excludes live sessions; skip active files if concurrent cleanup is needed.
    lock = lock_workspace(directory)
    if not lock:
        raise DebugError("busy", "Stop the debug session before pruning recordings")
    try:
        folder = directory / "recordings"
        if not os.path.lexists(folder):
            return result
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or folder.resolve().parent != directory.resolve():
            raise DebugError("unsafe_state", "Recording directory must be a directory, not a link or junction")
        cutoff = time.time() - args["older_than"] * 86400
        for file in folder.iterdir():
            info = file.lstat()
            if (not re.fullmatch(r"[a-f0-9]{16}\.jsonl", file.name) or
                    not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400):
                result["skipped"] += 1
                continue
            if args["older_than"] and info.st_mtime >= cutoff:
                continue
            # A valid session header identifies our files, including interrupted recordings.
            with file.open("rb") as stream:
                header = stream.readline(MAX_EVENT + 1)
            try:
                if len(header) > MAX_EVENT or not header.endswith(b"\n"):
                    raise ValueError()
                header = json.loads(header)
                if not isinstance(header, dict) or header.get("kind") != "session" or header.get("session") != file.stem:
                    raise ValueError()
            except (ValueError, UnicodeError):
                result["skipped"] += 1
                continue
            result["matched"] += 1
            result["matched_bytes"] += info.st_size
            if not args["dry_run"]:
                file.unlink()
                result["deleted"] += 1
                result["deleted_bytes"] += info.st_size
    except OSError as exc:
        result.update(ok=False, error={"code": "cleanup_failure", "message": str(exc)[:512]})
    finally:
        lock.close()
    return result


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
        self.append("session", session=session, program=("-m " if args.get("module") else "") + args["script"],
                    python=args.get("python"), language=args["language"],
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
