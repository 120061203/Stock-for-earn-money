import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from live_data import ApiLimitError, DataError, FinMindClient, calculate_revenue_growth, calculate_roe, prepare_live_data
import pandas as pd


def statement(date, value, name="淨利（淨損）歸屬於母公司業主"):
    return dict(date=date, type="EquityAttributableToOwnersOfParent", value=value, origin_name=name)


class LiveDataTests(unittest.TestCase):
    def test_http_quota_remains_a_limit_error(self):
        with tempfile.TemporaryDirectory() as folder:
            client = FinMindClient(cache_dir=folder)
            client.session.get = Mock()
            client.session.get.return_value.status_code = 429
            with self.assertRaises(ApiLimitError):
                client.fetch('TaiwanStockPrice', '2330', '2026-01-01', '2026-10-20')

    def test_quota_stops_batch_and_preserves_pending_status(self):
        market = pd.DataFrame({"date": pd.bdate_range("2026-07-01", periods=60).strftime("%Y-%m-%d"), "close": 100})
        record = dict(symbol="2330", name="台積電", price_date=market.date.iloc[-1],
                      roe_period_end="2026-06-30", revenue_month="2026-09", pe_ratio=20, roe=20, revenue_growth=10)
        daily = pd.DataFrame({"symbol": ["2330"], "date": [record["price_date"]], "close": [100], "volume": [1000]})
        with tempfile.TemporaryDirectory() as folder, patch("live_data.ROOT", Path(folder)), \
                patch("live_data.FinMindClient") as client, patch("live_data.download_stock") as download:
            client.return_value.fetch.return_value = market.to_dict("records")
            download.side_effect = [(record, daily), ApiLimitError("quota")]
            indicators, _ = prepare_live_data(["2330", "2317", "2454"], provider="finmind")
            self.assertEqual(download.call_count, 2)
            self.assertEqual(len(pd.read_csv(indicators)), 1)
            errors = pd.read_csv(Path(folder) / "data" / "download_errors.csv", dtype={"symbol": str})
            self.assertEqual(errors.symbol.tolist(), ["2317", "2454"])
            self.assertEqual(errors.status.tolist(), ["額度或權限受限", "尚未掃描"])

    def test_financial_company_parent_equity(self):
        dates = ("2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30")
        income = [statement(date, 10) for date in dates]
        balance = [dict(date=d, type=field, value=value) for d in ("2025-06-30", "2026-06-30")
                   for field, value in (("Equity", 250), ("NoncontrollingInterests", 50))]
        self.assertEqual(calculate_roe(income, balance)[0], 20)
        with self.assertRaises(DataError):
            calculate_roe(income, [r for r in balance if r["type"] == "Equity"])

    def test_roe_four_quarters_and_average_equity(self):
        dates = ("2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30")
        income = [statement(date, 10) for date in dates]
        balance = [statement("2025-06-30", 100), statement("2026-06-30", 300)]
        roe, period = calculate_roe(income, balance)
        self.assertEqual(roe, 20)
        self.assertEqual(period, "2026-06-30")
        with self.assertRaises(DataError):
            calculate_roe(income[1:], balance)
        income[0]["origin_name"] = "綜合損益總額歸屬於母公司業主"
        with self.assertRaises(DataError):
            calculate_roe(income, balance)

    def test_monthly_yoy_uses_same_month_last_year(self):
        rows = [
            dict(revenue_year=2025, revenue_month=9, revenue=100),
            dict(revenue_year=2026, revenue_month=8, revenue=500),
            dict(revenue_year=2026, revenue_month=9, revenue=120),
        ]
        growth, month = calculate_revenue_growth(rows)
        self.assertAlmostEqual(growth, 20)
        self.assertEqual(month, "2026-09")
        with self.assertRaises(DataError):
            calculate_revenue_growth(rows[1:])

    def test_successful_response_cached_but_error_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            client = FinMindClient(cache_dir=folder)
            client.session.get = Mock()
            response = client.session.get.return_value
            response.json.return_value = {"status": 200, "data": [{"stock_id": "2330", "close": 100}]}
            args = ("TaiwanStockPrice", "2330", "2026-01-01", "2026-10-16")
            self.assertEqual(client.fetch(*args), client.fetch(*args))
            self.assertEqual(client.session.get.call_count, 1)
            client.refresh = True
            response.json.return_value = {"status": 402, "data": []}
            with self.assertRaises(DataError):
                client.fetch(*args)
            self.assertEqual(len(list(Path(folder).glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
