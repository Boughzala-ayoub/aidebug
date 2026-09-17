"""Run with the installed package: python -I checks/recording.py."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from aidebug.dap import DebugError
from aidebug.recording import Recording, prune
from aidebug.session import Session
from aidebug.viewer import events, record_path


def controls():
    with tempfile.TemporaryDirectory(prefix="aidebug-recording-controls-") as temporary:
        workspace = Path(temporary)
        directory = workspace / ".aidebug"
        folder = directory / "recordings"
        sample = workspace / "sample.py"
        sample.write_text("value = 41\nvalue += 1\nprint(value)\n")

        def call(*arguments, ok=True):
            process = subprocess.run([sys.executable, "-I", "-m", "aidebug.cli", "--workspace", temporary, *arguments],
                                     cwd=temporary, capture_output=True, timeout=25)
            result = json.loads(process.stdout)
            assert not process.stderr and len(process.stdout) <= 65536, process.stderr
            assert result["ok"] == ok and process.returncode == (0 if ok else 1), result
            return result

        try:
            assert "--no-record" in call("launch", "--help")["help"]
            assert "--dry-run" in call("recordings", "--help")["help"]
            for arguments in [(), ("--older-than", "-1"), ("--older-than", "nan"), ("--older-than", "365001")]:
                assert call("recordings", "prune", *arguments, ok=False)["error"]["code"] == "invalid_request"
            assert call("recordings", "prune", "--older-than", "0")["deleted"] == 0
            assert not directory.exists(), "Empty cleanup must not create workspace state"

            entry = call("launch", "--no-record", "--module", "sample")
            assert entry["recording"] == {"enabled": False}
            call("break", "set", "sample.py", "3")
            stopped = call("continue")
            assert any(v["name"] == "value" and v["value"] == "42" for v in stopped["locals"]["items"])
            assert call("inspect")["recording"]["enabled"] is False
            assert call("note", "do not save this", ok=False)["error"]["code"] == "invalid_state"
            assert call("recordings", "prune", "--older-than", "0", ok=False)["error"]["code"] == "busy"
            assert call("continue")["exit_code"] == 0
            assert call("output")["output"]["stdout"]["text"] == "42\n"
            call("stop")
            assert not folder.exists(), "Opt-out must not create any recordings"

            entry = call("launch", "--run", "sample.py")
            archived = folder / (entry["session"] + ".jsonl")
            assert entry["recording"]["enabled"] and archived.is_file()
            assert call("recordings", "prune", "--older-than", "0", ok=False)["error"]["code"] == "busy"
            assert archived.is_file(), "Cleanup must preserve an open recording after target exit"
            call("stop")
            args = {"workspace": temporary, "script": "sample.py", "language": "python", "target": "process", "cwd": temporary}
            recent = Recording(args, "1111222233334444")
            recent.close()
            future = time.time() + 40 * 86400
            os.utime(recent.path, (future, future))
            interrupted = Recording(args, "2222333344445555")
            interrupted.stream.close()
            old = time.time() - 40 * 86400
            for file in (archived, interrupted.path):
                os.utime(file, (old, old))
            unrelated = folder / "notes.txt"
            unrelated.write_text("keep me")
            malformed = folder / "3333444455556666.jsonl"
            malformed.write_text("{}\n")
            os.utime(malformed, (old, old))
            expected_bytes = archived.stat().st_size + interrupted.path.stat().st_size
            preview = call("recordings", "prune", "--older-than", "30", "--dry-run")
            assert preview["matched"] == 2 and preview["matched_bytes"] == expected_bytes and preview["deleted"] == 0, preview
            assert archived.exists() and interrupted.path.exists()
            removed = call("recordings", "prune", "--older-than", "30")
            assert removed["deleted"] == 2 and removed["deleted_bytes"] == expected_bytes, removed
            assert recent.path.exists() and unrelated.exists() and malformed.exists()
            assert call("recordings", "prune", "--older-than", "30")["deleted"] == 0

            # A failed deletion must report the error and preserve the file for retry.
            with patch.object(Path, "unlink", side_effect=PermissionError("file is locked")):
                failed = prune({"workspace": temporary, "older_than": 0, "dry_run": False})
            assert not failed["ok"] and failed["error"]["code"] == "cleanup_failure" and failed["deleted"] == 0
            assert recent.path.exists()
            assert call("recordings", "prune", "--older-than", "0")["deleted"] == 1
            assert sorted(file.name for file in folder.iterdir()) == [malformed.name, unrelated.name]

            outside = workspace / "other-workspace"
            outside.mkdir()
            sentinel = outside / "5555666677778888.jsonl"
            sentinel.write_text('{"kind":"session","session":"5555666677778888"}\n')
            # Exercise real directory links, including Windows junctions without symlink privileges.
            def link(target):
                try:
                    target.symlink_to(outside, target_is_directory=True)
                except OSError:
                    if os.name != "nt":
                        raise
                    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "New-Item -ItemType Junction -Path $env:AIDEBUG_TEST_LINK -Target $env:AIDEBUG_TEST_TARGET -ErrorAction Stop | Out-Null"],
                        env=dict(os.environ, AIDEBUG_TEST_LINK=str(target), AIDEBUG_TEST_TARGET=str(outside)),
                        capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                    assert result.returncode == 0, result.stderr

            redirected_file = folder / "4444555566667777.jsonl"
            link(redirected_file)
            try:
                assert call("recordings", "prune", "--older-than", "0")["deleted"] == 0
                assert sentinel.exists() and redirected_file.exists()
            finally:
                if redirected_file.is_symlink():
                    redirected_file.unlink()
                else:
                    redirected_file.rmdir()
            linked_workspace = workspace / "linked-workspace"
            (linked_workspace / ".aidebug").mkdir(parents=True)
            redirected_folder = linked_workspace / ".aidebug/recordings"
            link(redirected_folder)
            try:
                result = call("--workspace", str(linked_workspace), "recordings", "prune", "--older-than", "0", ok=False)
                assert result["error"]["code"] == "unsafe_state" and sentinel.exists(), result
            finally:
                if redirected_folder.is_symlink():
                    redirected_folder.unlink()
                else:
                    redirected_folder.rmdir()
        finally:
            call("stop")
    print("PASS installed CLI recording opt-out, inspection/output, cleanup preview/age/all, interrupted sessions, active-session lock, deletion failure and link protection")


def main():
    with tempfile.TemporaryDirectory(prefix="aidebug-recording-") as temporary:
        workspace = Path(temporary)
        directory = workspace / ".aidebug"
        directory.mkdir()
        args = {"workspace": temporary, "script": "sample.py", "language": "python", "target": "process", "cwd": temporary}
        sample = workspace / "sample.py"
        sample.write_text("value = 41\n", encoding="utf-8")
        record = Recording(args, "1234567890abcdef")
        record.source(str(sample))
        sample.write_text("value = 42\n", encoding="utf-8")
        record.source(str(sample))
        record.append("note", text="The value changed")
        session = Session()
        session.recording = record
        error = DebugError("adapter_failure", "Variables unavailable in this frame", 9234)
        with patch.object(session, "execute", side_effect=error):
            try:
                session.command({"command": "inspect"})
            except DebugError:
                pass
            else:
                raise AssertionError("Expected the adapter failure")
        session.state = "running"
        session.failure(error)
        record.close()
        response = events(directory, "1234567890abcdef", 0)
        result = next(event for event in response["events"] if event["kind"] == "result")
        assert result["response"]["error"] == error.as_dict()
        assert next(event for event in response["events"] if event["kind"] == "error")["error"] == error.as_dict()
        sources = [event for event in response["events"] if event["kind"] == "source"]
        assert [source["text"] for source in sources] == ["value = 41\n", "value = 42\n"]
        assert sources[1]["changed"] and response["events"][-1]["kind"] == "end"
        assert not events(directory, "1234567890abcdef", response["next"])["events"]
        partial = Recording(args, "1111222233334444")
        stream = partial.stream
        class ShortWriter:
            @property
            def closed(self):
                return stream.closed
            def write(self, data):
                return stream.write(data[:7])
            def close(self):
                stream.close()
        partial.stream = ShortWriter()
        partial.append("note", text="Writes can be partial")
        partial.append("result", command="inspect", response={"value": "x" * 200000})
        partial.close()
        captured = events(directory, "1111222233334444", 0)["events"]
        assert captured[-3]["text"] == "Writes can be partial" and captured[-2]["truncated"]
        with record.path.open("ab") as stream:
            stream.write(b'{"partial":')
        assert events(directory, "1234567890abcdef", response["next"])["next"] == response["next"]
        for bad in ("../session", "A" * 16, "0" * 17):
            try:
                record_path(directory, bad)
            except ValueError:
                pass
            else:
                raise AssertionError("Accepted an invalid recording path")
        try:
            events(directory, "1234567890abcdef", 3)
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted a cursor inside an observation")
        with patch("aidebug.recording.MAX_RECORDING", 4096):
            bounded = Recording(args, "abcdef1234567890")
            for _ in range(30):
                bounded.append("output", category="stdout", text="x" * 500)
            assert bounded.full and bounded.path.stat().st_size <= 4096
            bounded.close()
            assert json.loads(bounded.path.read_text().splitlines()[-1])["kind"] == "limit"
        failed = Recording(args, "0123456789abcdef")
        class FullDisk:
            closed = False
            def write(self, data):
                raise OSError("disk full")
        failed.stream.close()
        failed.stream = FullDisk()
        failed.append("note", text="test")
        assert failed.error == "disk full", "Recording I/O errors must be reported without crashing the debugger"
    print("PASS recording persistence, source versions, partial writes, cursor/path validation, size bounds and disk failure")
    controls()


if __name__ == "__main__":
    main()
