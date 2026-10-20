"""驗證 RSI 暖機期、Wilder 初始值與平滑，以及多股票資料隔離。"""

import unittest

import pandas as pd

from technicals import add_technicals, calculate_rsi, demo_prices


class TechnicalTests(unittest.TestCase):
    def test_slope_warmup_previous_rsi_and_no_future_data(self):
        dates = pd.bdate_range("2025-01-01", periods=70)
        prices = pd.DataFrame({"symbol": "2330", "date": dates, "close": range(1, 71)})
        result = add_technicals(prices)
        self.assertTrue(result.ma60_slope.iloc[:60].isna().all())
        self.assertTrue(result.ma60_slope.iloc[60:].eq(1).all())
        self.assertTrue(result.previous_rsi.iloc[:15].isna().all())
        self.assertEqual(result.previous_rsi.iloc[15], result.rsi14.iloc[14])
        pd.testing.assert_frame_equal(add_technicals(prices.iloc[:61]), result.iloc[:61])

    def test_wilder_seed_and_smoothing(self):
        rsi = calculate_rsi(pd.Series([10, 12, 11, 14]), period=2)
        self.assertTrue(rsi.iloc[:2].isna().all())
        self.assertAlmostEqual(rsi.iloc[2], 100 * 1 / 1.5)
        self.assertAlmostEqual(rsi.iloc[3], 100 * 2 / 2.25)

    def test_direction_and_flat_prices(self):
        for prices, expected in ((range(1, 21), 100), (range(20, 0, -1), 0), ([10] * 20, 50)):
            rsi = calculate_rsi(pd.Series(prices))
            self.assertTrue(rsi.iloc[:14].isna().all())
            self.assertTrue(rsi.iloc[14:].eq(expected).all())
        self.assertTrue(calculate_rsi(pd.Series([10] * 14)).isna().all())

    def test_grouping_sorting_and_moving_averages(self):
        prices = demo_prices()
        combined = add_technicals(prices.sample(frac=1, random_state=10))
        for symbol in prices.symbol.unique():
            alone = add_technicals(prices[prices.symbol == symbol])
            group = combined[combined.symbol == symbol].reset_index(drop=True)
            pd.testing.assert_frame_equal(group, alone)
            self.assertAlmostEqual(group.ma20.iloc[-1], group.close.iloc[-20:].mean())
            self.assertAlmostEqual(group.ma60.iloc[-1], group.close.iloc[-60:].mean())

    def test_invalid_prices_and_duplicates(self):
        for invalid in (float("nan"), float("inf"), 0, -1):
            with self.assertRaises(ValueError):
                calculate_rsi(pd.Series([10, invalid, 11]))
        prices = demo_prices()
        with self.assertRaises(ValueError):
            add_technicals(pd.concat([prices, prices.iloc[:1]]))


if __name__ == "__main__":
    unittest.main()
