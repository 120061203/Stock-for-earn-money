import unittest

import pandas as pd

from market_state import market_context, market_for_date


class MarketStateTests(unittest.TestCase):
    def test_strict_comparison_and_missing_values(self):
        self.assertTrue(market_context(101, 100)["market_ok"])
        self.assertFalse(market_context(100, 100)["market_ok"])
        self.assertFalse(market_context(99, 100)["market_ok"])
        for invalid in (None, "", float("nan"), float("inf"), 0):
            self.assertIsNone(market_context(invalid, 100)["market_ok"])

    def test_same_date_warmup_and_future_exclusion(self):
        prices = pd.DataFrame({"date": pd.bdate_range("2025-01-01", periods=61), "close": [100] * 59 + [160, 10000]})
        date = prices.date.iloc[59].strftime("%Y-%m-%d")
        state = market_for_date(prices, date)
        self.assertEqual(state["taiex_close"], 160)
        self.assertEqual(state["taiex_ma60"], 101)
        self.assertTrue(state["market_ok"])
        self.assertIsNone(market_for_date(prices.iloc[:59], date)["market_ok"])
        self.assertIsNone(market_for_date(prices, "2025-01-02")["market_ok"])
        with self.assertRaises(ValueError):
            market_for_date(pd.concat([prices, prices.iloc[:1]]), date)


if __name__ == "__main__":
    unittest.main()
