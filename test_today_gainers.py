import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import requests
import json
from contextlib import redirect_stdout
from io import StringIO
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd

from filter_today_gainers import average_volume, extract_quote, filter_volume, load_universe, fetch_mis, main
from api_errors import ApiLimitError
from scan_errors import ScanFailure


class TodayGainersTests(unittest.TestCase):
    @patch('fugle_data.FugleClient', side_effect=AssertionError('篩選1不得使用富果'))
    @patch('live_data.FinMindClient')
    def test_history_missing_stays_unknown_without_fugle(self, primary, forbidden):
        primary.return_value.fetch.side_effect = [ValueError('歷史量不足'), [dict(date='2026-10-21', Trading_Volume=1000)]]
        quotes=[dict(symbol=s,name=s,today_volume_shares=2000) for s in ('2330','2317')]
        rows=filter_volume(quotes,'2026-10-22',1,2)
        self.assertEqual(rows[0]['volume_status'],'資料不足')
        self.assertIsNone(rows[0]['volume_ratio'])
        self.assertFalse(rows[0]['volume_pass'])
        self.assertTrue(rows[1]['volume_pass'])
        self.assertTrue(all(r['volume_history_source']=='FinMind' for r in rows))
        forbidden.assert_not_called()

    @patch('fugle_data.FugleClient', side_effect=AssertionError('篩選1不得使用富果'))
    @patch('live_data.FinMindClient')
    def test_finmind_quota_stops_remaining_requests(self, primary, forbidden):
        primary.return_value.fetch.side_effect=ApiLimitError('FinMind額度限制')
        rows=filter_volume([dict(symbol=s,name=s,today_volume_shares=2000) for s in ('2330','2317')],'2026-10-22',20,2)
        self.assertEqual(primary.return_value.fetch.call_count,1)
        self.assertEqual([r['volume_status'] for r in rows],['額度受限','尚未檢查'])
        self.assertTrue(all(r['volume_ratio'] is None for r in rows))
        forbidden.assert_not_called()

    def test_first_run_initializes_missing_universe(self):
        universe=pd.DataFrame([dict(symbol='2330',name='台積電',market='TWSE',market_cap_date='2026-10-21')])
        with tempfile.TemporaryDirectory() as folder,patch('filter_today_gainers.ROOT',Path(folder)),patch('market_universe.top_market_cap',return_value=universe) as fetch:
            self.assertEqual(load_universe().symbol.tolist(),['2330'])
            self.assertTrue((Path(folder)/'data/all_listed_stocks.csv').is_file())
            load_universe()
            fetch.assert_called_once_with(count=None)
        with tempfile.TemporaryDirectory() as folder,patch('filter_today_gainers.ROOT',Path(folder)),patch('market_universe.top_market_cap',side_effect=ValueError('官方資料下載失敗')):
            with self.assertRaises(ScanFailure) as error:load_universe()
            self.assertEqual(error.exception.code,'UNIVERSE_DOWNLOAD')

    def test_mis_failures_have_specific_safe_codes(self):
        for status,side_effect,code in [(403,None,'MIS_HTTP'),(200,ValueError('bad json'),'MIS_RESPONSE'),(200,requests.Timeout(),'MIS_TIMEOUT')]:
            client=Mock();client.get.return_value.status_code=status
            if isinstance(side_effect,requests.Timeout):client.get.side_effect=side_effect
            elif side_effect:client.get.return_value.json.side_effect=side_effect
            with self.subTest(code=code),self.assertRaises(ScanFailure) as error:fetch_mis(client,'tse_2330.tw')
            self.assertEqual(error.exception.code,code)

    def test_full_scan_never_uses_fugle_and_keeps_missing_quote(self):
        today=datetime.now(ZoneInfo('Asia/Taipei')).date()
        universe=pd.DataFrame([dict(symbol=s,name=s,market='TWSE',market_cap_date=today.isoformat()) for s in ('2330','2317')])
        payload=dict(rtcode='0000',msgArray=[dict(c='2330',n='台積電',ex='tse',d=today.strftime('%Y%m%d'),z='104',y='100',t='12:00:00',v='2'),dict(c='2317',ex='tse',d=today.strftime('%Y%m%d'),z='-',y='100')])
        history=[dict(date=d,Trading_Volume=1000) for d in pd.bdate_range(end=today-timedelta(days=1),periods=20).strftime('%Y-%m-%d')]
        with tempfile.TemporaryDirectory() as folder,patch('filter_today_gainers.ROOT',Path(folder)),patch('filter_today_gainers.load_universe',return_value=universe),patch('filter_today_gainers.fetch_mis',return_value=payload),patch('filter_today_gainers.sleep'),patch('sys.argv',['filter_today_gainers.py']),patch('live_data.FinMindClient') as primary,patch('fugle_data.FugleClient',side_effect=AssertionError('不得呼叫富果')) as forbidden,redirect_stdout(StringIO()):
            (Path(folder)/'data').mkdir()
            primary.return_value.fetch.return_value=history
            main()
            checked=pd.read_csv(next((Path(folder)/'data').glob('*.checked.csv')),dtype={'symbol':str})
            self.assertEqual(checked.symbol.tolist(),['2330'])
            self.assertEqual(checked.source.tolist(),['TWSE MIS'])
            self.assertEqual(checked.volume_history_source.tolist(),['FinMind'])
            missing=pd.read_csv(next((Path(folder)/'data').glob('*.missing.csv')),dtype={'symbol':str})
            self.assertEqual(missing.symbol.tolist(),['2317'])
            summary=json.loads(next((Path(folder)/'data').glob('*_vol2x_ma20.json')).read_text())
            self.assertEqual(summary['provider_policy'],'mis_finmind_only')
            self.assertFalse(any('fugle' in key for key in summary))
            forbidden.assert_not_called()

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
