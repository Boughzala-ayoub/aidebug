def average_price(orders):
    prices = [order["price"] for order in orders if order["paid"]]
    count = len(prices)
    if not count:
        raise ValueError("At least one paid order is required")
    return sum(prices) / count


orders = [{"price": 10, "paid": True}, {"price": 20, "paid": True}]
print(average_price(orders))
