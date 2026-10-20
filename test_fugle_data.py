import copy
import tempfile
import unittest
from datetime import date
from unittest.mock import Mock

from fugle_data import FugleClient
from live_data import download_stock
from technicals import demo_prices


class FugleTests(unittest.TestCase):
    def payload(self):
        return {"symbol": "2330", "timeframe": "D", "data": [
            {"date": "2026-10-15", "close": 101, "volume": 2000},
            {"date": "2026-10-14", "close": 100, "volume": 1000},
        ]}

    def test_request_auth_sort_volume_and_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            client = FugleClient(api_key="test-only-key", cache_dir=folder)
            client.session.get = Mock()
            client.session.get.return_value.status_code = 200
            client.session.get.return_value.json.return_value = self.payload()
            result = client.candles("2330", "2026-01-01", "2026-10-15")
            self.assertEqual(result.date.tolist(), ["2026-10-14", "2026-10-15"])
            self.assertEqual(result.volume.tolist(), [1000, 2000])
            self.assertEqual(client.session.headers["X-API-KEY"], "test-only-key")
            params = client.session.get.call_args.kwargs["params"]
            self.assertEqual(params["timeframe"], "D")
            self.assertEqual(params["adjusted"], "false")
            client.candles("2330", "2026-01-01", "2026-10-15")
            self.assertEqual(client.session.get.call_count, 1)

    def test_api_errors_do_not_expose_key(self):
        for status in (401, 403, 404, 429):
            with tempfile.TemporaryDirectory() as folder:
                client = FugleClient(api_key="test-only-key", cache_dir=folder)
                client.session.get = Mock()
                client.session.get.return_value.status_code = status
                with self.assertRaises(ValueError) as raised:
                    client.candles("2330", "2026-01-01", "2026-10-15")
                self.assertNotIn("test-only-key", str(raised.exception))

    def test_missing_key_and_invalid_range(self):
        with self.assertRaisesRegex(ValueError, "FUGLE_API_KEY"):
            FugleClient(api_key="")
        client = FugleClient(api_key="test-only-key")
        client.session.get = Mock()
        with self.assertRaises(ValueError):
            client.candles("2330", "2025-10-15", "2026-10-15")
        client.session.get.assert_not_called()

    def test_malformed_prices_are_rejected(self):
        for change in ("duplicate", "symbol", "negative-volume", "nan-close", "adjusted"):
            payload = copy.deepcopy(self.payload())
            if change == "duplicate":
                payload["data"].append(payload["data"][0])
            elif change == "symbol":
                payload["symbol"] = "2317"
            elif change == "negative-volume":
                payload["data"][0]["volume"] = -1
            elif change == "nan-close":
                payload["data"][0]["close"] = float("nan")
            else:
                payload["adjusted"] = True
            with self.assertRaises(ValueError):
                FugleClient.normalize(payload, "2330", date(2026, 1, 1), date(2026, 10, 15))

    def test_screener_uses_fugle_prices_and_finmind_fundamentals(self):
        prices = demo_prices().query("symbol == '2330'").copy()
        prices["date"] = prices["date"].dt.strftime("%Y-%m-%d")
        prices["volume"] = 1000
        latest = prices.date.iloc[-1]
        price_client = Mock()
        price_client.candles.return_value = prices
        client = Mock()
        def fetch(dataset, *args):
            if dataset == "TaiwanStockPrice":
                self.fail("富果模式不應取得 FinMind 股價")
            if dataset == "TaiwanStockPER":
                return [dict(date=latest, PER=18)]
            if dataset == "TaiwanStockFinancialStatements":
                return [dict(date=d, type="EquityAttributableToOwnersOfParent", value=10,
                             origin_name="淨利歸屬於母公司業主") for d in
                        ("2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31")]
            if dataset == "TaiwanStockBalanceSheet":
                return [dict(date=d, type="EquityAttributableToOwnersOfParent", value=100)
                        for d in ("2024-03-31", "2025-03-31")]
            return [dict(revenue_year=y, revenue_month=4, revenue=r) for y, r in ((2024, 100), (2025, 120))]
        client.fetch.side_effect = fetch
        record, _ = download_stock(client, "2330", date(2025, 5, 7), price_client=price_client)
        self.assertEqual(record["price_source"], "Fugle")
        self.assertEqual(record["fundamentals_source"], "FinMind")
        self.assertEqual(record["volume_unit"], "shares")
        self.assertEqual(record["volume"], 1000)
        self.assertEqual(record["roe"], 40)


if __name__ == "__main__":
    unittest.main()
