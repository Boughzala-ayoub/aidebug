"""Run with the installed package: python -I checks/recording.py."""

import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from aidebug.recording import Recording
from aidebug.viewer import events, record_path


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
        record.close()
        response = events(directory, "1234567890abcdef", 0)
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


if __name__ == "__main__":
    main()
