"""Run with: python checks/reports.py"""
import json
from pathlib import Path
import tempfile

from integration import write_report


with tempfile.TemporaryDirectory() as directory:
    destination = Path(directory) / "results.json"
    summary = {"passed": 2, "failed": 1, "groups": ["breakpoints", "cleanup"], "python": "3.13"}
    for attempt in range(2):
        full = {**summary, "seconds": attempt, "cli": f"/tmp/run-{attempt}/aidebug",
                "transcript": [{"response": {"error": "adapter failed", "run": attempt}}]}
        write_report(destination, json.dumps(full))
        assert json.loads(destination.read_text()) == summary
        assert json.loads((destination.parent / "artifacts/results.json").read_text()) == full
        assert destination.read_bytes() == (json.dumps(summary, indent=2) + "\n").encode()
print("PASS stable summaries preserve counts and versions; full failure evidence stays in artifacts")
