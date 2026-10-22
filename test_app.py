import csv
import json
import tempfile
import unittest
import re
from pathlib import Path

from app import ROOT, load_snapshot, parameters, ScanJob
from unittest.mock import Mock
from scan_errors import PREFIX


class StockAppTests(unittest.TestCase):
    def test_frontend_dom_ids_are_present(self):
        html = (ROOT / "webapp/index.html").read_text()
        script = (ROOT / "webapp/app.js").read_text()
        ids = set(re.findall(r'id="([^"]+)"', html))
        referenced = set(re.findall(r"\$\('([^']+)'\)", script))
        self.assertFalse(referenced - ids)
        self.assertIn('src="/app.js" type="module"', html)

    def test_scan_failure_surfaces_safe_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            job=ScanJob();job.log=Path(folder)/'scan.log'
            job.log.write_text(PREFIX+json.dumps(dict(code='MIS_HTTP',status=403,secret='do-not-display')))
            process=Mock();process.wait.return_value=1
            job._finish(process)
            self.assertEqual(job.state['error_code'],'MIS_HTTP')
            self.assertIn('403',job.state['message'])
            self.assertNotIn('do-not-display',job.state['message'])

    def test_old_fugle_results_require_a_fresh_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder)
            path=data/'gainers_2026-10-21_3_5_vol2x_ma20.checked.csv'
            path.write_text('symbol,source,volume_history_source\n2330,Fugle,FinMind\n')
            result=load_snapshot(20,data)
            self.assertFalse(result['available'])
            self.assertEqual(result['rows'],[])
            self.assertIn('TWSE MIS',result['message'])

    def test_defaults_and_parameter_bounds(self):
        self.assertEqual(parameters({}), dict(min_change=3, max_change=5, min_ratio=2, days=20))
        for payload in ({"min_change": 6, "max_change": 3}, {"min_ratio": 0}, {"days": 1.5},
                        {"days": 121}, {"min_ratio": float("nan")}, {"min_change": True}, {"days": "bad"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parameters(payload)

    def test_loads_latest_snapshot_for_requested_window(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            for date in ("2026-10-19", "2026-10-20"):
                path = data / f"gainers_{date}_3_5_vol2x_ma20.checked.csv"
                with path.open("w", encoding="utf-8-sig", newline="") as output:
                    writer = csv.DictWriter(output, fieldnames=["symbol", "price", "volume_ratio", "volume_pass", "volume_average_days"])
                    writer.writeheader()
                    writer.writerow(dict(symbol="2330", price="100", volume_ratio="2.5", volume_pass="True", volume_average_days="20"))
                    writer.writerow(dict(symbol="2317", price="nan", volume_ratio="", volume_pass="False", volume_average_days="20"))
                path.with_name(path.name.replace(".checked.csv", ".json")).write_text(json.dumps(dict(valid_today=10)), encoding="utf-8")
            result = load_snapshot(20, data)
            self.assertTrue(result["available"])
            self.assertEqual(result["summary"]["trade_date"], "2026-10-20")
            self.assertEqual(result["rows"][0]["volume_ratio"], 2.5)
            self.assertIsNone(result["rows"][1]["price"])
            self.assertFalse(load_snapshot(10, data)["available"])

    def test_missing_reasons_are_exposed(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / 'gainers_2026-10-20_3_5_vol2x_ma20.checked.csv').write_text(
                'symbol,volume_status,volume_error\n2330,資料不足,不足20日\n', encoding='utf-8')
            (data / 'gainers_2026-10-20_3_5.missing.csv').write_text(
                'symbol,reason,fugle_error\n2317,缺成交價,尚無交易\n', encoding='utf-8')
            result = load_snapshot(20, data)
            self.assertEqual(result['missing_rows'], [dict(symbol='2317', reason='缺成交價', fugle_error='尚無交易'),
                                                      dict(symbol='2330', reason='不足20日')])


if __name__ == "__main__":
    unittest.main()
