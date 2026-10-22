import unittest
from unittest.mock import Mock, patch
import pandas as pd
from api_errors import ApiLimitError
from quote_fallback import FugleSupplement, fugle_quote, repair_quotes


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



if __name__ == '__main__':
    unittest.main()
