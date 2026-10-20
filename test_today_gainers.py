import unittest
import pandas as pd

from filter_today_gainers import average_volume, extract_quote


class TodayGainersTests(unittest.TestCase):
    def test_today_actual_trade_and_reference(self):
        row = dict(c="2330", n="台積電", ex="tse", d="20261019", z="104", y="100", t="12:00:00", v="250")
        quote, error = extract_quote(row, "2026-10-19")
        self.assertIsNone(error)
        self.assertEqual(quote["change_percent"], 4)
        self.assertEqual(quote["market"], "上市")
        self.assertEqual(quote["today_volume_shares"], 250000)

    def test_volume_average_excludes_today_and_uses_twenty_sessions(self):
        dates = pd.bdate_range("2026-09-01", periods=25)
        history = pd.DataFrame(dict(date=dates.strftime("%Y-%m-%d"), Trading_Volume=[1000] * 24 + [999999]))
        average, first, last = average_volume(history.to_dict("records"), str(dates[-1].date()), 20)
        self.assertEqual(average, 1000)
        self.assertEqual(first, str(dates[4].date()))
        self.assertEqual(last, str(dates[23].date()))
        self.assertEqual(2000 / average, 2)
        with self.assertRaises(ValueError):
            average_volume(history.iloc[:19].to_dict("records"), "2026-10-20", 20)
        history.Trading_Volume = 0
        with self.assertRaises(ValueError):
            average_volume(history.to_dict("records"), "2026-10-20", 20)

    def test_missing_quote_uses_actual_trade_not_bid(self):
        row = dict(c="5274", ex="otc", d="20261019", z="-", y="100", a="110_", b="109_",
                   trade=dict(z="103", t="11:59:00"))
        quote, error = extract_quote(row, "2026-10-19")
        self.assertEqual(quote["change_percent"], 3)
        row.pop("trade")
        self.assertIsNone(extract_quote(row, "2026-10-19")[0])
        row["d"] = "20261016"
        self.assertEqual(extract_quote(row, "2026-10-19")[1], "非今日資料")


if __name__ == "__main__":
    unittest.main()
