"""Run against an installed aidebug executable, using real debugpy and processes.

    python checks/integration.py --cli .venv/Scripts/aidebug.exe
"""

import argparse
import ctypes
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time


def redact(report, paths):
    for path, label in paths:
        for value in (path, path.replace("\\", "/"), path.replace("\\", "\\\\")):
            report = re.sub(re.escape(json.dumps(value)[1:-1]), lambda match: label, report, flags=re.I)
    return report


def write_report(destination, report):
    artifacts = destination.parent / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / destination.name).write_text(report, encoding="utf-8", newline="\n")
    summary = {key: value for key, value in json.loads(report).items()
               if key not in ("transcript", "seconds", "cli")}
    destination.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")


def alive(pid):
    if not pid:
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
    except ProcessLookupError:
        return False


def until(predicate, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    assert predicate(), "condition did not become true before deadline"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", default=shutil.which("aidebug"))
    options = parser.parse_args()
    assert options.cli, "Install the project first or pass --cli"
    cli = str(Path(options.cli).resolve())
    root = Path(__file__).resolve().parents[1]
    transcript = []
    checks = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="aidebug-check-") as temp:
        workspace = Path(temp)
        for source in (root / "examples").glob("*.py"):
            shutil.copy(source, workspace)
        (workspace / "work dir").mkdir()
        if os.name == "nt":
            (workspace / "console.py").write_text(
                "import ctypes\n"
                "ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p\n"
                "ctypes.windll.user32.IsWindowVisible.argtypes = [ctypes.c_void_p]\n"
                "print(bool(ctypes.windll.user32.IsWindowVisible(ctypes.windll.kernel32.GetConsoleWindow())))\n")

        def call(*args, ok=True, timeout=15, implicit=False):
            command = [cli, *([] if implicit else ["--workspace", temp]), "--timeout", str(timeout), *args]
            process = subprocess.run(command, cwd=temp, capture_output=True, timeout=timeout + 12)
            assert len(process.stdout) <= 65536, len(process.stdout)
            try:
                result = json.loads(process.stdout)
            except Exception:
                raise AssertionError((command, process.stdout, process.stderr))
            assert isinstance(result, dict), result
            assert result["ok"] is ok, (args, result, process.stderr,
                (workspace / ".aidebug" / "helper.log").read_text(errors="replace") if (workspace / ".aidebug" / "helper.log").exists() else "")
            assert process.returncode == (0 if ok else 1), (args, process.returncode, result)
            assert not process.stderr, process.stderr
            transcript.append({"command": list(args), "response": result})
            return result

        def cleanup(pids):
            call("stop")
            assert not (workspace / ".aidebug" / "session.json").exists()
            until(lambda: all(not alive(pid) for pid in pids if pid))

        def values(response, key="locals"):
            return {item["name"]: item for item in response[key]["items"]}

        try:
            # Every call is a new OS process, running outside the source checkout.
            help_text = call("--help")["help"]
            assert all(value in help_text for value in ("aidebug launch", "aidebug inspect", "aidebug step", "aidebug stop", "timed_out", "VIRTUAL_ENV", "Handles expire")), help_text
            entry = call("launch", "--cwd", "work dir", "--python", sys.executable, "walk.py", "hello world", "--flag", implicit=True)
            pids = list(entry["processes"].values())
            assert entry["state"] == "stopped" and entry["reason"] == "entry", entry
            old_frame = entry["location"]["frame"]
            call("launch", "walk.py", ok=False)
            call("break", "set", "walk.py", "12", "13")
            removed = call("break", "remove", "walk.py", "13")
            assert [b["line"] for b in removed["breakpoints"]] == [12]
            stopped = call("continue")
            assert stopped["reason"] == "breakpoint" and stopped["location"]["line"] == 12, stopped
            assert values(stopped)["known"]["value"] == "41", stopped
            call("inspect", "--frame", old_frame, ok=False)
            nested_handle = values(stopped)["nested"]["handle"]
            nested = call("variables", nested_handle)
            items_handle = values(nested, "variables")["'items'"]["handle"]
            expanded = call("variables", items_handle)
            assert any(v["value"] == "8" for v in expanded["variables"]["items"]), expanded
            # Paging must still work when debugpy ignores DAP start/count.
            page = call("variables", nested_handle, "--limit", "1")
            assert page["variables"]["truncated"] and page["variables"]["next_start"] == 1, page
            page2 = call("variables", nested_handle, "--start", "1", "--limit", "1")
            assert page["variables"]["items"] != page2["variables"]["items"]
            into = call("step", "into")
            assert into["location"]["line"] == 6 and values(into)["value"]["value"] == "41", into
            caller = call("inspect", "--frame", into["stack"]["frames"][1]["handle"])
            assert values(caller)["known"]["value"] == "41", caller
            call("variables", nested_handle, ok=False)
            over = call("step", "over")
            assert over["location"]["line"] == 7 and values(over)["answer"]["value"] == "42", over
            out = call("step", "out")
            assert out["location"]["line"] in (12, 13), out
            call("break", "remove", "walk.py")
            exited = call("continue")
            assert exited["state"] == "exited" and exited["exit_code"] == 0, exited
            output = call("output")["output"]["stdout"]["text"]
            assert "42" in output and "hello world" in output and "--flag" in output and "work dir" in output, output
            cleanup(pids)
            checks.append("separate CLI calls: breakpoint add/remove, nested values, frame selection, all steps, args/cwd/interpreter, exit, expired handles")
            print("PASS breakpoint, inspection, stepping, launch options, handle expiry", flush=True)

            # Keep the original failure independent of the repaired example.
            (workspace / "exception.py").write_text(
                'def average_price(orders):\n'
                '    prices = [order["price"] for order in orders if order["paid"]]\n'
                '    count = len(orders) - len(prices)\n'
                '    return sum(prices) / count\n'
                'orders = [{"price": 10, "paid": True}, {"price": 20, "paid": True}]\n'
                'print(average_price(orders))\n')
            call("launch", "--uncaught", "exception.py")
            failure = call("continue")
            assert failure["state"] == "stopped" and failure["reason"] == "exception", failure
            assert failure["location"]["line"] == 4 and len(failure["stack"]["frames"]) >= 2, failure
            assert values(failure)["count"]["value"] == "0" and values(failure)["prices"]["value"] == "[10, 20]", failure
            call("exceptions", "off")
            assert call("continue")["exit_code"] != 0
            cleanup(list(failure["processes"].values()))
            fixed = call("launch", "--run", "--uncaught", "fixed.py")
            assert fixed["state"] == "exited" and fixed["exit_code"] == 0, fixed
            assert "15.0" in call("output")["output"]["stdout"]["text"]
            cleanup(list(fixed["processes"].values()))
            checks.append("uncaught exception location/stack/values, exception toggle, fixed example exits with 15.0")
            print("PASS uncaught exception evidence and corrected example", flush=True)

            running = call("launch", "running.py")
            pids = list(running["processes"].values())
            start = time.monotonic()
            timed = call("continue", timeout=0.2)
            assert time.monotonic() - start < 3, "execution command ignored deadline"
            assert timed["state"] == "running" and timed["timed_out"], timed
            assert call("status")["state"] == "running"
            paused = call("pause")
            assert paused["state"] == "stopped" and paused["stack"]["frames"], paused
            old = paused["location"]["frame"]
            assert call("pause", ok=False)["error"]["code"] == "invalid_state"
            assert call("continue", timeout=0.2)["timed_out"]
            call("inspect", "--frame", old, ok=False)
            assert call("pause")["state"] == "stopped"
            assert call("continue", timeout=0.2)["timed_out"]
            call("step", "over", ok=False)
            text = call("output")["output"]["stdout"]["text"]
            child = int(text.split("child ")[1].split()[0])
            assert alive(child)
            cleanup(pids + [child])
            checks.append("running timeout preserves control; pause inspects and resumes; stop removes helper, adapter, target, and target child")
            print("PASS execution timeout and owned process tree cleanup", flush=True)

            for args in [("wat",), ("--timeout", "nan", "status"), ("break", "set", "walk.py", "0"),
                         ("launch", "missing.py"), ("variables", "fake", "--limit", "500"), ("status",)]:
                assert call(*args, ok=False)["error"]["code"] in ("invalid_request", "no_session")
            # Stale metadata is removed only after proving no helper holds the OS lock.
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            state_file = workspace / ".aidebug" / "session.json"
            state_file.write_text(json.dumps({"port": port, "token": "stale", "pid": 99999999}))
            call("stop")
            assert not state_file.exists()
            checks.append("invalid commands return JSON errors; stale state is safely removed")
            print("PASS invalid requests and stale session cleanup", flush=True)

            entry = call("launch", "walk.py")
            info = json.loads(state_file.read_text())
            with socket.create_connection(("127.0.0.1", info["port"]), timeout=2) as sock:
                sock.sendall(b'{"token":"wrong","argv":["stop"]}\n')
                result = json.loads(sock.makefile("rb").readline())
                assert result["error"]["code"] == "unauthorized", result
            assert call("status")["state"] == "stopped"
            os.kill(entry["processes"]["adapter"], signal.SIGTERM)
            until(lambda: not alive(entry["processes"]["adapter"]))
            failed = call("status", ok=False)
            assert failed["state"] == "failed" and failed["error"]["code"] == "adapter_failure", failed
            cleanup(list(entry["processes"].values()))
            checks.append("invalid authentication is rejected; adapter death reports failed and remains cleanable")
            print("PASS control authentication and adapter failure", flush=True)

            (workspace / "large.py").write_text("big = 'x' * 20000\nprint('z' * 30000)\nprint(len(big))\n")
            call("launch", "large.py")
            call("break", "set", "large.py", "3")
            large = call("continue")
            assert values(large)["big"]["truncated"], large
            output = call("output")["output"]["stdout"]
            assert output["truncated"] and len(output["text"]) <= 8192, output
            cleanup(list(large["processes"].values()))
            checks.append("large variable previews and target output are bounded with explicit truncation")
            print("PASS response and output bounds", flush=True)

            (workspace / "conditional.py").write_text("for i in range(500):\n    value = i * 2\nprint(value)\n")
            for arguments in [("launch", "--condition", "True", "conditional.py"),
                              ("break", "set", "conditional.py", "2", "--condition", ""),
                              ("break", "set", "conditional.py", "2", "--condition", "x" * 513),
                              ("break", "remove", "conditional.py", "2", "--condition", "True")]:
                assert call(*arguments, ok=False)["error"]["code"] == "invalid_request"
            conditional = call("launch", "--run", "--breakpoint", "conditional.py:2", "--condition", "i == 437", "conditional.py")
            assert values(conditional)["i"]["value"] == "437", conditional
            call("break", "set", "conditional.py", "2", "--condition", "i == 439")
            preserved = call("break", "set", "conditional.py", "3")["breakpoints"]
            assert preserved[0]["condition"] == "i == 439" and preserved[1]["condition"] is None, preserved
            assert values(call("continue"))["i"]["value"] == "439"
            call("break", "remove", "conditional.py", "3")
            assert call("break", "set", "conditional.py", "2")["breakpoints"][0]["condition"] is None
            assert values(call("continue"))["i"]["value"] == "440"
            call("break", "remove", "conditional.py")
            assert call("continue")["exit_code"] == 0
            cleanup(list(conditional["processes"].values()))
            checks.append("conditional launch skips to iteration 437; conditions update, persist, clear and remove; invalid conditions are JSON errors")
            print("PASS conditional breakpoints", flush=True)

            early = call("launch", "running.py", timeout=0.01)
            assert early["state"] == "running" and early["timed_out"], early
            until(lambda: call("status")["state"] == "stopped", seconds=10)
            entry = call("status")
            if os.name == "nt":
                os.kill(entry["processes"]["helper"], signal.SIGTERM)
                until(lambda: all(not alive(pid) for pid in entry["processes"].values() if pid))
                call("stop")
                assert not state_file.exists()
            else:
                cleanup(list(entry["processes"].values()))
            checks.append("launch timeout completes asynchronously; Windows helper death kills its job and stale metadata is removed")
            print("PASS launch deadline and helper crash cleanup", flush=True)
            if os.name == "nt":
                console = call("launch", "--run", "console.py")
                assert call("output")["output"]["stdout"]["text"].strip() == "False", "debuggee console became visible"
                cleanup(list(console["processes"].values()))
                checks.append("Windows debuggee inherits no visible console window")
                print("PASS hidden Windows console", flush=True)
        finally:
            subprocess.run([cli, "--workspace", temp, "stop"], cwd=temp, capture_output=True, timeout=15)

    report = {"passed": len(checks), "failed": 0, "seconds": round(time.monotonic() - started, 2),
              "python": sys.version, "debugpy": version("debugpy"), "aidebug": version("aidebug-cli"),
              "platform": sys.platform, "cli": cli, "checks": checks,
              "transcript": transcript}
    destination = root / "checks" / "results.json"
    report = json.dumps(report, indent=2)
    report = redact(report, [(temp, "<workspace>"), (str(root), "<repo>"), (str(Path.home()), "<home>")])
    write_report(destination, report)
    print(f"PASS {len(checks)} integration groups; results: {destination}")


if __name__ == "__main__":
    main()
