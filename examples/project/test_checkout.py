import unittest
from pricing import average_price

class CheckoutTest(unittest.TestCase):
    def test_paid_average(self):
        orders = [{"price": 10, "paid": True}, {"price": 20, "paid": True},
                  {"price": 100, "paid": False}]
        self.assertEqual(average_price(orders), 15.0)
