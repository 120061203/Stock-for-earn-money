import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from strategy2 import evaluate, indicators, rebound_analysis, is_rebound_candidate, score_indicators, WEIGHTS
from chip_data import chip_values, fetch_chips, MARGIN, INSTITUTION
from api_errors import ApiLimitError
from app import ROOT, ScanJob
from unittest.mock import Mock


class Strategy2Tests(unittest.TestCase):
    def row(self):
        return dict(close=90,open=91,low=85,ma5=85,ma10=88,ma20=100,ma60=110,rsi14=20,
                    k=20,d=19,prev_k=10,prev_d=11,volume=100,prev_volume=200,avg_volume20=100,
                    prev_low=84,margin_balance_today=100,margin_balance_5d_ago=150,
                    foreign_net_buy_today=1,foreign_net_buy_yesterday=0,trust_net_buy_today=2,trust_net_buy_yesterday=-1)

    def prices(self):
        dates=pd.bdate_range(end='2026-10-20',periods=90)
        closes=[100]*75+list(range(99,84,-1))
        return pd.DataFrame(dict(date=dates.strftime('%Y-%m-%d'),close=closes,open=[v+1 for v in closes],
                                 low=[v-2 for v in closes],high=[v+2 for v in closes],volume=[1000]*90))

    def test_full_score_and_candidate(self):
        result=rebound_analysis(self.row())
        self.assertTrue(is_rebound_candidate(self.row()))
        self.assertEqual(result['score'],17)
        self.assertEqual(sum(WEIGHTS.values()),17)
        self.assertTrue(result['score_complete'])

    def test_each_score_rule_boundaries(self):
        for updates,key in [({'ma20':90/.920001},'bias20_oversold'),({'ma60':90/.880001},'bias60_oversold'),
                            ({'rsi14':30.001},'rsi_oversold'),({'k':25},'kd_golden_cross'),
                            ({'prev_k':12},'kd_golden_cross'),({'k':19},'kd_golden_cross'),
                            ({'ma5':90},'above_ma5'),({'ma10':90},'above_ma10'),
                            ({'low':89.5},'long_lower_shadow'),({'prev_volume':180},'volume_shrink_after_panic'),
                            ({'volume':200},'volume_shrink_after_panic'),({'prev_low':85},'higher_low'),
                            ({'margin_balance_today':150},'margin_balance_down'),
                            ({'foreign_net_buy_today':0},'foreign_turn_buy'),
                            ({'foreign_net_buy_yesterday':1},'foreign_turn_buy'),
                            ({'trust_net_buy_today':0},'trust_turn_buy')]:
            with self.subTest(key=key,updates=updates):
                self.assertEqual(rebound_analysis({**self.row(),**updates})['score_'+key],0)
        row={**self.row(), 'ma20':90/.92,'ma60':90/.88,'rsi14':30}
        self.assertEqual(rebound_analysis(row)['score'],17)

    def test_four_groups_are_and_with_or_inside(self):
        row=self.row()
        for updates,group in [({'ma20':90,'ma60':90,'rsi14':50},'oversold'),
                              ({'prev_k':12,'ma5':90,'ma10':90},'technical_reversal'),
                              ({'prev_volume':180,'low':90,'prev_low':90},'price_stabilizing'),
                              ({'margin_balance_today':150,'foreign_net_buy_today':0,'trust_net_buy_today':0},'chip_improving')]:
            result=rebound_analysis({**row,**updates})
            self.assertFalse(result[group]);self.assertFalse(result['candidate'])
        # 只有一個籌碼分支成立已足夠。
        self.assertTrue(is_rebound_candidate({**row,'margin_balance_today':None,'trust_net_buy_today':None}))

    def test_missing_chips_are_unknown_with_score_range(self):
        row={key:value for key,value in self.row().items() if not key.startswith(('margin_','foreign_','trust_'))}
        result=rebound_analysis(row)
        self.assertIsNone(result['chip_improving'])
        self.assertIsNone(result['score_margin_balance_down'])
        self.assertFalse(result['candidate'])
        self.assertEqual(result['score'],12)
        self.assertEqual(result['score_ceiling'],17)
        self.assertFalse(result['score_complete'])
        with self.assertRaises(ValueError): rebound_analysis({**row,'ma60':float('nan')})

    def test_kd_seed_flat_prices_and_no_lookahead(self):
        prices=self.prices();prices.loc[:,'close']=100;prices.loc[:,'open']=100;prices.loc[:,'low']=100;prices.loc[:,'high']=100
        frame=indicators(prices)
        self.assertTrue(frame.k.iloc[:8].isna().all())
        self.assertEqual(frame.k.iloc[8],50);self.assertEqual(frame.d.iloc[8],50)
        self.assertEqual(frame.ma60.iloc[-1],100)
        result=evaluate(prices)
        self.assertEqual(result['margin_compare_date'],prices.date.iloc[-6])
        self.assertEqual(result['avg_volume20'],1000)
        prices.loc[89,'volume']=99999
        self.assertEqual(evaluate(prices)['avg_volume20'],1000)
        pd.testing.assert_frame_equal(indicators(prices.iloc[:80]),indicators(prices).iloc[:80].reset_index(drop=True))
        with self.assertRaises(ValueError): evaluate(prices.tail(59))
        prices.loc[0,'low']=-1
        with self.assertRaises(ValueError): evaluate(prices)

    def test_chip_alignment_duplicates_and_zero_values(self):
        margin=[dict(date=day,stock_id='2330',MarginPurchaseTodayBalance=value) for day,value in [('2026-10-20',0),('2026-10-13',10)]]
        inst=[dict(date=day,stock_id='2330',name=name,buy=0,sell=0) for day in ('2026-10-20','2026-10-19') for name in ('Foreign_Investor','Investment_Trust')]
        row,errors=chip_values(margin,inst,'2026-10-20','2026-10-19','2026-10-13','2330')
        self.assertEqual(errors,[]);self.assertEqual(row['foreign_net_buy_today'],0)
        self.assertEqual(row['margin_balance_today'],0)
        row,errors=chip_values(margin,inst[:-1],'2026-10-20','2026-10-19','2026-10-13','2330')
        self.assertIsNone(row['trust_net_buy_yesterday'])
        row,errors=chip_values(margin+[margin[0]],inst,'2026-10-20','2026-10-19','2026-10-13','2330')
        self.assertIsNone(row['margin_balance_today'])
        row,errors=chip_values(margin,inst,'2026-10-21','2026-10-20','2026-10-13','2330')
        self.assertIsNone(row['foreign_net_buy_today'])

    def test_quota_stops_chip_requests_without_zero_fill(self):
        client=Mock();client.fetch.side_effect=ApiLimitError('quota')
        blocked={}
        for symbol in ('2330','2317'):
            row,errors=fetch_chips(client,symbol,'2026-10-20','2026-10-19','2026-10-13',blocked)
            self.assertTrue(all(value is None for value in row.values()))
        self.assertEqual(client.fetch.call_count,2)

    def test_scan_integration_new_rules_only(self):
        from strategy2 import scan
        with tempfile.TemporaryDirectory() as folder, patch('strategy2.ROOT',Path(folder)),patch('strategy2.sleep'),patch('strategy2.datetime') as clock,patch('live_data.FinMindClient') as finmind,patch('fugle_data.FugleClient') as fugle,patch('chip_data.fetch_chips') as chips:
            clock.now.return_value=datetime.fromisoformat('2026-10-21T08:00:00+08:00')
            data=Path(folder)/'data';data.mkdir()
            pd.DataFrame([dict(symbol='2330',name='台積電',market='TWSE')]).to_csv(data/'top50_market_cap.csv',index=False)
            fugle.return_value.candles.return_value=self.prices()
            chips.return_value=({key:self.row()[key] for key in self.row() if key.startswith(('margin_','foreign_','trust_'))},[])
            result=scan()
            self.assertEqual(result['summary']['rules_version'],2)
            self.assertEqual(result['summary']['score_max'],17)
            self.assertEqual(result,json.loads((data/'strategy2_top50.json').read_text()))
            finmind.return_value.fetch.assert_not_called()

    def test_job_and_frontend_ids(self):
        import re
        html=(ROOT/'webapp/strategy2.html').read_text();js=(ROOT/'webapp/strategy2.js').read_text()
        self.assertFalse(set(re.findall(r"\$\('([^']+)'\)",js))-set(re.findall(r'id="([^"]+)"',html)))
        with tempfile.TemporaryDirectory() as folder,patch('app.subprocess.Popen') as launch,patch('app.threading.Thread'):
            job=ScanJob();job.log=Path(folder)/'scan.log';job.start(dict(strategy='strategy2',scope='top50'))
            self.assertIn(str(ROOT/'strategy2.py'),launch.call_args.args[0])


if __name__=='__main__': unittest.main()
