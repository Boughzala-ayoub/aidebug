def paid_prices(orders):
    return [order["price"] for order in orders if order["paid"]]
