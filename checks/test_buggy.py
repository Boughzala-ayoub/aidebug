"""Run with: .venv/Scripts/python checks/test_buggy.py"""

from pathlib import Path
from runpy import run_path


average_price = run_path(str(Path(__file__).resolve().parents[1] / "examples/buggy.py"))["average_price"]
paid = [{"price": 10, "paid": True}, {"price": 20, "paid": True}]
assert average_price(paid) == 15.0
assert average_price(paid + [{"price": 100, "paid": False}]) == 15.0
assert average_price([{"price": 0, "paid": True}]) == 0.0
for orders in ([], [{"price": 100, "paid": False}]):
    try:
        average_price(orders)
    except ValueError as error:
        assert str(error) == "At least one paid order is required"
    else:
        raise AssertionError("Expected ValueError for no paid orders")
print("PASS paid, mixed, zero-price, empty, and unpaid-only orders")
