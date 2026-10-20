import unittest

from market_universe import build_universe, roc_date


class MarketUniverseTests(unittest.TestCase):
    def inputs(self):
        companies = [{"公司代號": "2330", "公司簡稱": "台積電", "出表日期": "1151015",
                      "已發行普通股數或TDR原股發行股數": "1000"}]
        prices = [{"Code": "2330", "ClosingPrice": "100", "Date": "1151015"},
                  {"Code": "0050", "ClosingPrice": "100", "Date": "1151015"}]
        tpex = [{"SecuritiesCompanyCode": "5274", "CompanyName": "信驊", "Capitals": "2000",
                 "ClosePrice": "60", "Date": "1151015", "MarketValue": "0"}]
        return companies, prices, tpex

    def test_ordinary_shares_and_consistent_units(self):
        result, missing = build_universe(*self.inputs())
        self.assertEqual(result.symbol.tolist(), ["5274", "2330"])
        self.assertEqual(result.market_cap.tolist(), [120000, 100000])
        self.assertEqual(result.market_cap_rank.tolist(), [1, 2])
        self.assertFalse(missing)
        self.assertEqual(roc_date("1151015"), "2026-10-15")

    def test_market_date_mismatch_rejected(self):
        companies, prices, tpex = self.inputs()
        tpex[0]["Date"] = "1151014"
        with self.assertRaises(ValueError):
            build_universe(companies, prices, tpex)

    def test_all_stocks_preserves_company_without_quote(self):
        companies, prices, tpex = self.inputs()
        companies.append({"公司代號": "1516", "公司簡稱": "缺報價公司", "出表日期": "1151015",
                          "已發行普通股數或TDR原股發行股數": "100"})
        result, missing = build_universe(companies, prices, tpex, include_missing=True)
        self.assertIn("1516", result.symbol.tolist())
        self.assertIn("1516", missing)
        self.assertTrue(result.set_index("symbol").loc["1516", ["market_cap", "market_cap_rank"]].isna().all())


if __name__ == "__main__":
    unittest.main()
