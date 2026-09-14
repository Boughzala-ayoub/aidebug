"""Record a real diagnosis/fix/rerun, leaving the viewer and recordings available."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time


BUG = '''def average_price(orders):
    prices = [order["price"] for order in orders if order["paid"]]
    count = len(prices)
    count -= len(orders)
    return sum(prices) / count


orders = [{"price": 10, "paid": True}, {"price": 20, "paid": True}]
print(average_price(orders))
'''


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True)
    parser.add_argument("--workspace", default=str(root))
    parser.add_argument("--open", action="store_true", help="open the viewer to watch the demo live")
    args = parser.parse_args()
    cli, workspace = str(Path(args.cli).resolve()), Path(args.workspace).resolve()
    if (workspace / ".aidebug/session.json").exists():
        raise SystemExit("A session already exists in this workspace. Stop it or choose another --workspace for the demo.")

    def call(*command):
        process = subprocess.run([cli, "--workspace", str(workspace), "--timeout", "15", *command],
                                 capture_output=True, timeout=27)
        result = json.loads(process.stdout)
        assert result["ok"] and process.returncode == 0, result
        time.sleep(0.5)  # Make individual observations visible when watching the demo live.
        return result

    viewer = call("view", *(["--open"] if args.open else []))
    directory = workspace / ".aidebug/demos"
    directory.mkdir(mode=0o700, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="checkout-", dir=directory)) / "checkout.py"
    fixture.write_text(BUG, encoding="utf-8")
    records = []
    try:
        stop = call("launch", "--run", "--uncaught", "--breakpoint", f"{fixture}:4", str(fixture))
        records.append(stop["session"])
        call("note", "I am checking the denominator before the average is calculated. Both orders are paid.")
        prices = next(value for value in stop["locals"]["items"] if value["name"] == "prices")
        call("variables", prices["handle"])
        stop = call("step", "over")
        assert next(value for value in stop["locals"]["items"] if value["name"] == "count")["value"] == "0"
        call("note", "The count changed from 2 to 0. Subtracting all orders from the paid-order count makes the divisor zero.")
        failure = call("continue")
        assert failure["reason"] == "exception"
        fixture.write_text(BUG.replace("count -= len(orders)", "count = len(prices)"), encoding="utf-8")
        call("note", "Fix: use the number of paid orders as the divisor. Changes now contains the patch. A fresh launch will verify it.")
        call("continue")
        call("stop")
        fixed = call("launch", "--run", str(fixture))
        records.append(fixed["session"])
        assert fixed["state"] == "exited" and fixed["exit_code"] == 0
        assert "15.0" in call("output")["output"]["stdout"]["text"]
        call("note", "Verified: the average is 15.0 and the corrected program exits with code 0.")
    finally:
        subprocess.run([cli, "--workspace", str(workspace), "stop"], capture_output=True, timeout=15)
    print(json.dumps({"ok": True, "viewer_url": viewer["url"], "recordings": records,
                      "result": "Reproduced division by zero, inspected count=0, fixed the divisor, reran with average=15.0"}))


if __name__ == "__main__":
    main()
