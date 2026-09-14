"""Real installed-CLI checks for Node, TypeScript source maps, browser UI, and Rust."""

import argparse
import ctypes
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import threading
import time
import urllib.request

from integration import alive, redact, until


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True)
    parser.add_argument("--typescript-compiler", required=True, help="path to the installed TypeScript lib/typescript.js")
    parser.add_argument("--js-adapter", default=str(root / ".tools/js-debug/src/dapDebugServer.js"))
    parser.add_argument("--rust-adapter", default=str(root / ".tools/codelldb/extension/adapter" / ("codelldb.exe" if os.name == "nt" else "codelldb")))
    options = parser.parse_args()
    cli = str(Path(options.cli).resolve())
    transcript, groups = [], []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="aidebug-languages-") as temporary:
        workspace = Path(temporary)
        shutil.copy(root / "examples/javascript/walk.js", workspace / "walk.js")
        shutil.copy(root / "examples/typescript/walk.ts", workspace / "ts-walk.ts")
        shutil.copy(root / "examples/rust/walk.rs", workspace / "walk.rs")
        compiler = str(Path(options.typescript_compiler).resolve())
        compile_js = """
const fs = require('node:fs');
const ts = require(process.argv[1]);
for (const input of process.argv.slice(2)) {
  const output = ts.transpileModule(fs.readFileSync(input, 'utf8'), {
    fileName: input, compilerOptions: { sourceMap: true, inlineSources: true, target: ts.ScriptTarget.ES2020 }
  });
  fs.writeFileSync(input.replace(/\.ts$/, '.js'), output.outputText);
  fs.writeFileSync(input.replace(/\.ts$/, '.js.map'), output.sourceMapText);
}
"""
        (workspace / "ui.ts").write_text(
            'function save() {\n'
            '  const quantity: number = Number((document.getElementById("quantity") as HTMLInputElement).value);\n'
            '  const price: number = 21;\n'
            '  const total: number = quantity * price;\n'
            '  document.getElementById("result")!.textContent = String(total);\n'
            '  console.log("UI total", total);\n'
            '}\n'
            'document.getElementById("save")!.addEventListener("click", save);\n'
            'document.getElementById("save")!.click();\n')
        (workspace / "index.html").write_text(
            '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Debugger UI fixture</title><label>Quantity <input id="quantity" value="2" size="3"></label>'
            '<button id="save">Save</button><output id="result"></output><script src="ui.js"></script>')
        (workspace / "conditional.ts").write_text('for (let i: number = 0; i < 500; i++) {\n  console.log(i);\n}\n')
        (workspace / "conditional.html").write_text('<script src="conditional.js"></script>')
        subprocess.run(["node", "-e", compile_js, compiler, str(workspace / "ts-walk.ts"), str(workspace / "ui.ts"), str(workspace / "conditional.ts")], check=True, timeout=30)
        binary = workspace / ("walk.exe" if os.name == "nt" else "walk")
        subprocess.run(["rustc", "-g", "-C", "opt-level=0", str(workspace / "walk.rs"), "-o", str(binary)], check=True, timeout=60)
        panic_binary = workspace / ("panic.exe" if os.name == "nt" else "panic")
        (workspace / "panic.rs").write_text('fn main() {\n    let divisor = 0;\n    assert!(divisor > 0, "bad divisor");\n}\n')
        subprocess.run(["rustc", "-g", "-C", "opt-level=0", str(workspace / "panic.rs"), "-o", str(panic_binary)], check=True, timeout=60)

        def call(*args, ok=True, timeout=15):
            process = subprocess.run([cli, "--workspace", temporary, "--timeout", str(timeout), *args],
                                     cwd=temporary, capture_output=True, timeout=timeout + 12)
            assert len(process.stdout) <= 65536
            result = json.loads(process.stdout)
            transcript.append({"command": list(args), "response": result})
            assert (ok is None or result["ok"] == ok) and process.returncode == (0 if result["ok"] else 1), (args, result, process.stderr)
            assert not process.stderr, process.stderr
            return result

        def values(result, key="locals"):
            return {v["name"]: v for v in result[key]["items"]}

        def stop(result):
            # A transient OS profile lock is a reported, retryable cleanup failure.
            # Require that retry actually removes both metadata and the profile.
            for attempt in range(3):
                closed = call("stop", ok=None)
                if closed["ok"]:
                    break
                assert closed["error"]["code"] == "cleanup_failure", closed
                assert (workspace / ".aidebug/session.json").exists(), closed
                time.sleep(0.2)
            assert closed["ok"], closed
            until(lambda: all(not alive(pid) for pid in result["processes"].values() if pid))
            assert not (workspace / ".aidebug/session.json").exists()
            assert not list((workspace / ".aidebug").glob("browser-*")), "Browser profile leaked"

        server = None
        try:
            for arguments in [
                ("launch", "--language", "python", "--target", "browser", "http://localhost:1"),
                ("launch", "--target", "browser", "https://example.com"),
                ("launch", "--language", "rust", "walk.rs"),
                ("launch", "--headed", "walk.js"),
                ("launch", "--adapter", "missing.js", "walk.js"),
                ("launch", "--adapter", options.js_adapter, "--breakpoint", "walk.js:0", "walk.js"),
            ]:
                result = call(*arguments, ok=False)
                assert result["error"]["code"] in ("invalid_request", "missing_dependency"), result
                assert not (workspace / ".aidebug/session.json").exists()
            groups.append("invalid language/target combinations, remote browser URL, source binary, adapter and breakpoint return JSON without a session")
            print("PASS launch validation", flush=True)
            for language, target, source in [("javascript", "walk.js", "walk.js"), ("typescript", "ts-walk.js", "ts-walk.ts")]:
                result = call("launch", "--language", language, "--adapter", options.js_adapter, "--run", "--breakpoint", f"{source}:8", target)
                assert result["state"] == "stopped" and result["reason"] == "breakpoint", result
                assert Path(result["location"]["file"]).name == source and result["location"]["line"] == 8, result
                assert values(result)["known"]["value"] == "41", result
                assert result["location"]["frame"] is not None, result  # DAP frame 0 is valid.
                old = result["location"]["frame"]
                nested = call("variables", values(result)["nested"]["handle"])
                assert "items" in values(nested, "variables"), nested
                into = call("step", "into")
                assert values(into)["value"]["value"] == "41", into
                call("inspect", "--frame", old, ok=False)
                over = call("step", "over")
                assert values(over)["answer"]["value"] == "42", over
                call("step", "out")
                call("break", "remove", source)
                assert call("continue")["state"] == "exited"
                assert "42" in call("output")["output"]["stdout"]["text"]
                stop(result)
                groups.append(f"{language}: real breakpoint, nested values, all steps, handle expiry, output and cleanup")
                print("PASS", language, flush=True)

            (workspace / "conditional-node.js").write_text('for (let i = 0; i < 500; i++) {\n  console.log(i);\n}\n')
            result = call("launch", "--adapter", options.js_adapter, "--run", "--breakpoint", "conditional-node.js:2", "--condition", "i === 437", "conditional-node.js")
            assert values(result)["i"]["value"] == "437", result
            call("break", "set", "conditional-node.js", "2", "--condition", "i === 439")
            assert values(call("continue"))["i"]["value"] == "439"
            call("break", "set", "conditional-node.js", "2")
            assert values(call("continue"))["i"]["value"] == "440"
            call("break", "remove", "conditional-node.js")
            assert call("continue")["state"] == "exited"
            stop(result)
            groups.append("Node conditional launch skips to iteration 437, updates to 439 and clears to unconditional")
            print("PASS Node conditional breakpoints", flush=True)

            (workspace / "error.js").write_text('function fail() {\n  const divisor = 0;\n  throw new Error("bad divisor");\n}\nfail();\n')
            result = call("launch", "--adapter", options.js_adapter, "--run", "--uncaught", "error.js")
            assert result["state"] == "stopped" and result["reason"] == "exception", result
            assert values(result)["divisor"]["value"] == "0", result
            call("exceptions", "off")
            assert call("continue")["state"] == "exited"
            stop(result)
            groups.append("JavaScript uncaught exception and exception toggle")
            print("PASS JavaScript exception", flush=True)

            (workspace / "running.js").write_text('console.log("pid", process.pid);\nsetInterval(() => {}, 1000);\n')
            result = call("launch", "--adapter", options.js_adapter, "--run", "running.js", timeout=2)
            assert result["state"] == "running" and result["timed_out"], result
            until(lambda: "pid " in call("output")["output"]["stdout"]["text"], seconds=20)
            output = call("output")["output"]["stdout"]["text"]
            pid = int(output.split("pid ")[1].split()[0])
            paused = call("pause")
            assert paused["state"] == "stopped" and paused["stack"]["frames"], paused
            assert call("continue", timeout=0.2)["timed_out"]
            stop(result)
            until(lambda: not alive(pid))
            groups.append("Node deadline, pause/inspection/resume and actual target PID cleanup")
            print("PASS Node timeout and target cleanup", flush=True)

            class Quiet(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=temporary))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}/index.html"
            with urllib.request.urlopen(url) as response:
                assert response.status == 200
            result = call("launch", "--language", "typescript", "--target", "browser", "--adapter", options.js_adapter,
                          "--breakpoint", "ui.ts:4", url)
            assert result["state"] == "stopped" and Path(result["location"]["file"]).name == "ui.ts", result
            assert result["location"]["line"] == 4, result
            assert values(result)["quantity"]["value"] == "2" and values(result)["price"]["value"] == "21", result
            step = call("step", "over")
            assert values(step)["total"]["value"] == "42", step
            call("continue", timeout=0.2)
            assert "UI total 42" in call("output")["output"]["stdout"]["text"]
            paused = call("pause", timeout=2)
            # An idle browser may defer suspension until the next JavaScript task.
            assert paused["state"] in ("stopped", "running"), paused
            if paused["state"] == "running":
                assert paused["timed_out"], paused
            else:
                assert paused["stack"]["frames"], paused
                assert call("continue", timeout=0.2)["timed_out"]
            browser_pids = set()
            browser_handles = []
            if os.name == "nt":
                processes = json.loads(subprocess.check_output([
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"
                ], creationflags=subprocess.CREATE_NO_WINDOW, timeout=15))
                parents = {result["processes"]["adapter"]}
                while children := {p["ProcessId"] for p in processes if p["ParentProcessId"] in parents} - parents:
                    browser_pids.update(children)
                    parents.update(children)
                assert browser_pids, "Expected owned Chrome descendants"
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                # Retain identities across stop: a recycled PID is not a leaked child.
                browser_handles = [handle for pid in browser_pids if (handle := kernel.OpenProcess(0x100000, False, pid))]
            try:
                stop(result)
                until(lambda: all(not alive(pid) for pid in browser_pids))
            finally:
                for handle in browser_handles:
                    kernel.CloseHandle(handle)
            groups.append("headless browser clicks Save and stops in original TypeScript handler with quantity=2, price=21, total=42")
            print("PASS browser UI and TypeScript source maps", flush=True)

            (workspace / "heartbeat.html").write_text('<script>setInterval(function tick() { const known = 41; document.title = String(known); }, 50);</script>')
            result = call("launch", "--target", "browser", "--adapter", options.js_adapter, "--run",
                          f"http://127.0.0.1:{server.server_port}/heartbeat.html", timeout=2)
            assert result["state"] == "running" and result["timed_out"], result
            until(lambda: call("status")["phase"] == "ready", seconds=20)
            paused = call("pause")
            assert paused["state"] == "stopped" and paused["stack"]["frames"], paused
            assert call("continue", timeout=0.2)["timed_out"]
            stop(result)
            groups.append("Browser with recurring JavaScript pauses, inspects, resumes and cleans up")
            print("PASS browser pause", flush=True)

            result = call("launch", "--language", "typescript", "--target", "browser", "--adapter", options.js_adapter,
                          "--breakpoint", "conditional.ts:2", "--condition", "i === 437",
                          f"http://127.0.0.1:{server.server_port}/conditional.html")
            assert values(result)["i"]["value"] == "437" and Path(result["location"]["file"]).name == "conditional.ts", result
            call("break", "set", "conditional.ts", "2", "--condition", "i === 439")
            assert values(call("continue"))["i"]["value"] == "439"
            call("break", "remove", "conditional.ts")
            assert call("continue", timeout=0.2)["state"] == "running"
            stop(result)
            groups.append("Browser TypeScript conditional breakpoint skips to 437 and updates through source maps")
            print("PASS browser conditional breakpoints", flush=True)

            result = call("launch", "--language", "rust", "--adapter", options.rust_adapter, "--run", "--breakpoint", "walk.rs:10", str(binary))
            assert result["state"] == "stopped" and result["location"]["line"] == 10, result
            assert values(result)["known"]["value"] == "41", result
            nested = call("variables", values(result)["nested"]["handle"])
            assert any(v["value"] == "8" for v in nested["variables"]["items"]), nested
            into = call("step", "into")
            assert values(into)["value"]["value"] == "41", into
            call("step", "over")
            call("step", "out")
            call("break", "remove", "walk.rs")
            exited = call("continue")
            assert exited["state"] == "exited" and exited["exit_code"] == 0, exited
            assert "42" in call("output")["output"]["stdout"]["text"]
            stop(result)
            groups.append("Rust debug-symbol breakpoint, nested array, all steps, output, exit and cleanup")
            print("PASS Rust", flush=True)

            result = call("launch", "--language", "rust", "--adapter", options.rust_adapter, "--run", "--uncaught", str(panic_binary))
            assert result["state"] == "stopped", result
            frame = next(f for f in result["stack"]["frames"] if Path(f["file"]).name == "panic.rs")
            inspected = call("inspect", "--frame", frame["handle"])
            assert values(inspected)["divisor"]["value"] == "0", inspected
            call("exceptions", "off")
            exited = call("continue")
            assert exited["state"] == "exited" and exited["exit_code"] != 0, exited
            assert "bad divisor" in call("output")["output"]["stderr"]["text"]
            stop(result)
            groups.append("Rust panic filter, failing caller locals, stderr and nonzero exit")
            print("PASS Rust panic", flush=True)

            (workspace / "loop.rs").write_text('fn main() {\n    loop { std::thread::sleep(std::time::Duration::from_millis(10)); }\n}\n')
            loop_binary = workspace / ("loop.exe" if os.name == "nt" else "loop")
            subprocess.run(["rustc", "-g", str(workspace / "loop.rs"), "-o", str(loop_binary)], check=True, timeout=60)
            result = call("launch", "--language", "rust", "--adapter", options.rust_adapter, "--run", str(loop_binary), timeout=2)
            assert result["state"] == "running" and result["timed_out"], result
            paused = call("pause")
            assert paused["state"] == "stopped" and paused["stack"]["frames"], paused
            assert call("continue", timeout=0.2)["timed_out"]
            stop(result)
            groups.append("Rust running deadline, pause/inspection/resume and cleanup")
            print("PASS Rust pause", flush=True)

            (workspace / "conditional.rs").write_text('fn main() {\n    for i in 0..500 {\n        println!("{}", i);\n    }\n}\n')
            conditional_binary = workspace / ("conditional.exe" if os.name == "nt" else "conditional")
            subprocess.run(["rustc", "-g", str(workspace / "conditional.rs"), "-o", str(conditional_binary)], check=True, timeout=60)
            result = call("launch", "--language", "rust", "--adapter", options.rust_adapter, "--run",
                          "--breakpoint", "conditional.rs:3", "--condition", "i == 437", str(conditional_binary))
            assert values(result)["i"]["value"] == "437", result
            call("break", "set", "conditional.rs", "3", "--condition", "i == 439")
            assert values(call("continue"))["i"]["value"] == "439"
            call("break", "set", "conditional.rs", "3")
            # println! maps this source line to several native breakpoint locations.
            for _ in range(4):
                next_iteration = values(call("continue"))["i"]["value"]
                assert next_iteration in ("439", "440"), next_iteration
                if next_iteration == "440":
                    break
            assert next_iteration == "440", "Clearing the condition must allow the next iteration"
            call("break", "remove", "conditional.rs")
            assert call("continue")["state"] == "exited"
            stop(result)
            groups.append("Rust conditional launch skips to iteration 437, updates to 439 and clears to unconditional")
            print("PASS Rust conditional breakpoints", flush=True)
        finally:
            subprocess.run([cli, "--workspace", temporary, "stop"], cwd=temporary, capture_output=True, timeout=15)
            if server:
                server.shutdown()
                server.server_close()
            report = json.dumps({
                "passed": len(groups), "failed": int(sys.exc_info()[0] is not None),
                "seconds": round(time.monotonic() - started, 2),
                "groups": groups, "transcript": transcript}, indent=2)
            report = redact(report, [(temporary, "<workspace>"), (str(root), "<repo>"), (str(Path.home()), "<home>")])
            (root / "checks/multilanguage-results.json").write_text(report, encoding="utf-8", newline="\n")
    print(f"PASS {len(groups)} multi-language groups")


if __name__ == "__main__":
    main()
