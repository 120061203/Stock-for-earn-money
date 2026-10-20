import unittest
from unittest.mock import Mock, patch
import pandas as pd
from api_errors import ApiLimitError
from quote_fallback import FugleSupplement, fugle_quote, repair_quotes
from filter_today_gainers import filter_volume


class SupplementTests(unittest.TestCase):
    def payload(self):
        return dict(symbol='2330', name='台積電', date='2026-10-20', market='TSE',
                    lastTrade=dict(price=104), referencePrice=100, total=dict(tradeVolume=250))

    def test_actual_quote_units_and_repair(self):
        client = Mock(); client.quote.return_value = self.payload()
        supplement = FugleSupplement(client, interval=0)
        quotes, missing = repair_quotes([], [dict(symbol='2330', reason='API 未回傳')], '2026-10-20', supplement)
        self.assertEqual(missing, [])
        self.assertEqual(quotes[0]['today_volume_shares'], 250000)
        self.assertAlmostEqual(quotes[0]['change_percent'], 4)
        self.assertEqual(quotes[0]['source'], 'Fugle')
        self.assertEqual(supplement.recovered, 1)
        payload = self.payload()
        payload['lastTrade']['price'] = 103
        self.assertEqual(fugle_quote(payload, '2026-10-20')['change_percent'], 3)
        payload['lastTrade']['price'] = 105
        self.assertEqual(fugle_quote(payload, '2026-10-20')['change_percent'], 5)

    def test_reject_stale_trial_only_or_invalid_volume(self):
        for change in (dict(date='2026-10-19'), dict(lastTrade={}, lastPrice=104),
                       dict(lastTrade=dict(price=0)), dict(total=dict(tradeVolume=-1))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                fugle_quote({**self.payload(), **change}, '2026-10-20')

    def test_quota_stops_requests_and_retains_reasons(self):
        client = Mock(); client.quote.side_effect = ApiLimitError('額度限制')
        supplement = FugleSupplement(client, interval=0)
        quotes, missing = repair_quotes([], [dict(symbol=s, reason='缺報價') for s in ['2330', '2317']], '2026-10-20', supplement)
        self.assertEqual(quotes, [])
        self.assertEqual(client.quote.call_count, 1)
        self.assertTrue(all(row['fugle_error'] == '額度限制' for row in missing))

    @patch('live_data.FinMindClient')
    def test_history_fallback_and_primary_recovery(self, primary):
        history = [dict(date='2026-10-19', Trading_Volume=1000)]
        primary.return_value.fetch.side_effect = [ValueError('資料缺失'), history]
        client = Mock()
        client.candles.return_value = pd.DataFrame([dict(date='2026-10-19', volume=1000)])
        supplement = FugleSupplement(client, interval=0)
        quotes = [dict(symbol=s, name=s, today_volume_shares=2000) for s in ['2330', '2317']]
        rows = filter_volume(quotes, '2026-10-20', 1, 2, supplement)
        self.assertEqual([r['volume_history_source'] for r in rows], ['Fugle', 'FinMind'])
        self.assertTrue(all(r['volume_pass'] for r in rows))
        self.assertEqual(client.candles.call_count, 1)

    @patch('live_data.FinMindClient')
    def test_fugle_limit_does_not_stop_finmind(self, primary):
        primary.return_value.fetch.side_effect = [ValueError('缺資料'), [dict(date='2026-10-19', Trading_Volume=1000)]]
        client = Mock(); client.candles.side_effect = ApiLimitError('富果額度限制')
        supplement = FugleSupplement(client, interval=0)
        rows = filter_volume([dict(symbol=s, name=s, today_volume_shares=2000) for s in ['2330', '2317']], '2026-10-20', 1, 2, supplement)
        self.assertEqual(rows[0]['volume_status'], '額度受限')
        self.assertTrue(rows[1]['volume_pass'])


if __name__ == '__main__':
    unittest.main()
