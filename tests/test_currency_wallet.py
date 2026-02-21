import unittest
from banking.currency_wallet import CurrencyWallet


class TestCurrencyWallet(unittest.TestCase):
    def setUp(self):
        self.cw = CurrencyWallet()

    def test_to_base(self):
        self.assertAlmostEqual(self.cw.to_base(100, "USD"), 100)
        self.assertAlmostEqual(self.cw.to_base(85, "EUR"), 100)  # 85 / 0.85 = 100

    def test_from_base(self):
        self.assertAlmostEqual(self.cw.from_base(100, "USD"), 100)
        self.assertAlmostEqual(self.cw.from_base(100, "EUR"), 85)

    def test_convert(self):
        # 100 EUR to GBP: 100 / 0.85 * 0.73 ≈ 85.882
        self.assertAlmostEqual(self.cw.convert(100, "EUR", "GBP"), 100 / 0.85 * 0.73, places=2)

    def test_liquidity_present(self):
        self.assertTrue(self.cw.check_liquidity("USD", 5000))

    def test_liquidity_insufficient(self):
        self.assertFalse(self.cw.check_liquidity("USD", 20000))

    def test_liquidity_unknown_currency_returns_false(self):
        # check_liquidity uses .get(currency, 0), so an unknown currency
        # returns False (0 >= amount is False for any positive amount).
        self.assertFalse(self.cw.check_liquidity("XYZ", 100))

    def test_to_base_unsupported_currency(self):
        with self.assertRaises(ValueError):
            self.cw.to_base(100, "XYZ")

    def test_adjust_liquidity(self):
        self.cw.adjust_liquidity("USD", 500)
        self.assertEqual(self.cw.liquidity["USD"], 10500)
        self.cw.adjust_liquidity("USD", -200)
        self.assertEqual(self.cw.liquidity["USD"], 10300)

    def test_adjust_liquidity_goes_negative_raises(self):
        with self.assertRaises(RuntimeError):
            self.cw.adjust_liquidity("USD", -20000)  # would go negative


if __name__ == "__main__":
    unittest.main()
