from orders import paid_prices

def average_price(orders):
    prices = paid_prices(orders)
    count = len(orders)
    return sum(prices) / count
