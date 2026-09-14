"""Real multi-file test diagnosis, module launch, and isolated project interpreter.

Run: python checks/project.py --cli PATH/TO/aidebug [--pytest-python PYTHON]
The optional interpreter must already have pytest installed; this check downloads nothing.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import venv

from integration import alive, redact, until


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--pytest-python")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cli = os.path.abspath(options.cli)
    transcript, groups = [], []
    started = time.monotonic()
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    with tempfile.TemporaryDirectory(prefix="aidebug-project-") as temporary:
        workspace = Path(temporary)
        project = workspace / "checkout"
        shutil.copytree(root / "examples/project", project)
        venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(workspace / ".venv")
        python = workspace / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        site = subprocess.check_output([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
        Path(site, "project_marker.py").write_text("VALUE = 731\n")
        (project / "probe.py").write_text("import sys, project_marker\nmarker = project_marker.VALUE\nprint(marker, sys.prefix)\n")

        def call(*args, ok=True, env=None):
            process = subprocess.run([cli, "--workspace", temporary, "--timeout", "20", *args],
                                     cwd=root, capture_output=True, timeout=32, env=environment if env is None else env)
            result = json.loads(process.stdout)
            transcript.append({"command": list(args), "response": result})
            assert len(process.stdout) <= 65536 and not process.stderr, process.stderr
            assert result["ok"] == ok and process.returncode == (0 if ok else 1), result
            return result

        def stop(result):
            call("stop")
            until(lambda: all(not alive(pid) for pid in result["processes"].values() if pid))
            assert not (workspace / ".aidebug/session.json").exists()

        def values(result):
            return {v["name"]: v["value"] for v in result["locals"]["items"]}

        def diagnose(module, interpreter=None):
            pricing = project / "pricing.py"
            original = pricing.read_text()
            flags = ["--python", interpreter] if interpreter else []
            runner_args = ["-q", "test_checkout"] if module == "unittest" else ["-q", "test_checkout.py"]
            launch = ["launch", "--cwd", "checkout", *flags, "--module", "--run", "--uncaught"]
            result = call(*launch, "--breakpoint", "checkout/pricing.py:4", module, "--", *runner_args)
            assert result["state"] == "stopped" and result["location"]["line"] == 4, result
            assert Path(result["location"]["file"]).name == "pricing.py"
            inside = call("step", "into")
            assert Path(inside["location"]["file"]).name == "orders.py", inside
            assert "100" in values(inside)["orders"]
            call("break", "set", "checkout/pricing.py", "6")
            call("step", "out")
            evidence = call("continue")
            assert values(evidence)["count"] == "3" and values(evidence)["prices"] == "[10, 20]", evidence
            caller = next(f for f in evidence["stack"]["frames"] if Path(f["file"]).name == "test_checkout.py")
            assert "100" in values(call("inspect", "--frame", caller["handle"]))["orders"]
            call("note", "The prices contain two paid orders, but count includes all three orders. The average is 10 instead of 15.")
            failed = call("continue")
            # Runners catch assertions; an uncaught SystemExit may stop in runner code.
            if failed["state"] == "stopped":
                assert failed["reason"] == "exception" and Path(failed["location"]["file"]).parent != project, failed
                call("exceptions", "off")
                failed = call("continue")
            assert failed["state"] == "exited" and failed["exit_code"] != 0, failed
            output = call("output")["output"]
            assert "15" in output["stderr"]["text"] + output["stdout"]["text"]
            pricing.write_text(original.replace("count = len(orders)", "count = len(prices)"))
            call("note", "Fixed the divisor to count only paid prices. A fresh launch will verify the change.")
            stop(result)
            fixed = call(*[arg for arg in launch if arg != "--uncaught"], module, "--", *runner_args)
            assert fixed["state"] == "exited" and fixed["exit_code"] == 0, fixed
            call("note", "Verified: the same test now passes with average 15.0.")
            stop(fixed)
            recorded = [json.loads(line) for line in (workspace / ".aidebug/recordings" / (result["session"] + ".jsonl")).read_text().splitlines()]
            assert recorded[0]["program"] == "-m " + module
            assert any(e["kind"] == "source" and e.get("changed") for e in recorded)
            pricing.write_text(original)
            groups.append(module + ": multi-file stepping, caller values, captured failure, fix, passing rerun, recording and cleanup")
            print("PASS", groups[-1], flush=True)

        try:
            for args in [("--module", "a/b"), ("--module", "--language", "rust", "pkg"),
                         ("--module", "--target", "browser", "pkg"), ("--module", "a..b")]:
                assert call("launch", *args, ok=False)["error"]["code"] == "invalid_request"
            assert not (workspace / ".aidebug/session.json").exists()
            result = call("launch", "--run", "--cwd", "checkout", "--module", "probe")
            assert result["state"] == "exited" and result["exit_code"] == 0, result
            assert os.path.normcase(result["python"]) == os.path.normcase(str(python))
            output = call("output")["output"]["stdout"]["text"]
            assert "731" in output and str(workspace / ".venv") in output, output
            stop(result)
            relative = str(python.relative_to(workspace))
            explicit_project = call("launch", "--run", "--python", relative, "--cwd", "checkout", "checkout/probe.py")
            assert explicit_project["exit_code"] == 0 and explicit_project["python"] == str(python), explicit_project
            stop(explicit_project)
            # The same workspace-only import fails with the explicit CLI interpreter.
            explicit = call("launch", "--run", "--python", sys.executable, "--cwd", "checkout", "--module", "probe")
            assert explicit["exit_code"] != 0 and explicit["python"] == os.path.abspath(sys.executable), explicit
            stop(explicit)
            missing = call("launch", "--run", "--module", "aidebug_nonexistent_module_731")
            assert missing["state"] == "exited" and missing["exit_code"] != 0, missing
            stop(missing)
            active = workspace / "custom-environment"
            venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(active)
            active_python = active / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            (project / "environment.py").write_text("import sys\nprint(sys.prefix)\n")
            activated = {**environment, "VIRTUAL_ENV": str(active)}
            selected = call("launch", "--run", "checkout/environment.py", env=activated)
            assert selected["python"] == str(active_python) and selected["exit_code"] == 0, selected
            assert str(active) in call("output")["output"]["stdout"]["text"]
            stop(selected)
            override = call("launch", "--run", "--python", str(python), "checkout/environment.py", env=activated)
            assert override["python"] == str(python) and override["exit_code"] == 0, override
            stop(override)
            assert call("launch", "checkout/environment.py", env={**environment, "VIRTUAL_ENV": str(workspace / "missing-env")}, ok=False)["error"]["code"] == "missing_dependency"
            assert not (workspace / ".aidebug/session.json").exists()
            groups.append("module validation, missing module, cwd, workspace-only dependency and explicit interpreter override")
            print("PASS", groups[-1], flush=True)
            diagnose("unittest")
            if options.pytest_python:
                diagnose("pytest", os.path.abspath(options.pytest_python))
        finally:
            subprocess.run([cli, "--workspace", temporary, "stop"], capture_output=True, timeout=15)
            # Store reproducible evidence without the developer's absolute paths.
            report = json.dumps({"passed": len(groups), "failed": int(sys.exc_info()[0] is not None),
                "groups": groups, "seconds": round(time.monotonic() - started, 2), "transcript": transcript}, indent=2)
            report = redact(report, [(temporary, "<workspace>"), (str(root), "<repo>"), (str(Path.home()), "<home>")])
            (root / "checks/project-results.json").write_text(report, encoding="utf-8", newline="\n")
    print(f"PASS {len(groups)} project groups")


if __name__ == "__main__":
    main()
