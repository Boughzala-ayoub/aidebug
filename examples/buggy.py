def average_price(orders):
    prices = [order["price"] for order in orders if order["paid"]]
    count = len(prices)
    if not count:
        raise ValueError("At least one paid order is required")
    return sum(prices) / count


if __name__ == "__main__":
    orders = [{"price": 10, "paid": False}, {"price": 20, "paid": False}]
    print(average_price(orders))
