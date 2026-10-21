import re
import json
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from strategy3 import analyze,evaluate,intraday,refresh_live,WEIGHTS
from app import ROOT,ScanJob


class Strategy3Tests(unittest.TestCase):
    def row(self):
        return dict(close=100,low=95,ma5=98,ma20=105,ma60=80,ma120=70,high_60d=110,
                    distance_ma20=-4.76,rsi14=50,volume=110,avg_volume20=100,prev_low=95,prev_high=99)

    def watch(self):
        return dict(symbol='2330',watchlist=True,price_date='2026-10-20',day_high=102,low=95,last4_close_sum=392)

    def quote(self,price=101,low=96,second=0):
        now=datetime.fromisoformat('2026-10-21T10:00:00+08:00')+timedelta(seconds=second)
        return dict(symbol='2330',date='2026-10-21',lowPrice=low,lastTrade=dict(price=price,time=now.timestamp()*1_000_000))

    def test_all_scores_and_watch_boundaries(self):
        self.assertEqual(sum(WEIGHTS.values()),11)
        self.assertEqual(analyze(self.row())['score'],11)
        self.assertTrue(analyze(self.row())['daily_buy'])
        for key,value in [('ma60',100),('ma120',80),('high_60d',118),('distance_ma20',2.001),('distance_ma20',-8.001),('rsi14',39.999),('rsi14',60.001),('volume',110.001),('low',94.99)]:
            with self.subTest(key=key,value=value):self.assertFalse(analyze({**self.row(),key:value})['watchlist'])
        for key,value in [('distance_ma20',-8),('distance_ma20',2),('rsi14',40),('rsi14',60),('high_60d',100/.85),('volume',110),('low',95)]:
            with self.subTest(key=key,value=value):self.assertTrue(analyze({**self.row(),key:value})['watchlist'])

    def test_daily_entry_uses_or_without_a_score_threshold(self):
        for changes,entry,score in [({'ma5':100},True,10),({'prev_high':100},True,9),({'ma5':100,'prev_high':100},False,8)]:
            result=analyze({**self.row(),**changes})
            self.assertTrue(result['watchlist']);self.assertEqual(result['daily_buy'],entry);self.assertEqual(result['score'],score)
        with self.assertRaises(ValueError):analyze({**self.row(),'ma120':float('nan')})

    def test_daily_ma120_high60_and_volume_windows(self):
        prices=pd.DataFrame(dict(date=pd.bdate_range(end='2026-10-20',periods=150).strftime('%Y-%m-%d'),
            close=list(range(100,250)),open=list(range(100,250)),low=list(range(99,249)),high=list(range(101,251)),volume=[1000]*149+[500]))
        prices.loc[0,'high']=9999
        result=evaluate(prices)
        self.assertEqual(result['avg_volume20'],1000)
        self.assertEqual(result['prev_high'],249)
        self.assertEqual(result['day_high'],250)
        self.assertEqual(result['high_60d'],250)
        self.assertEqual(result['ma120'],sum(range(130,250))/120)
        self.assertEqual(result['last4_close_sum'],sum(range(246,250)))
        with self.assertRaises(ValueError):evaluate(prices.tail(119))
        self.assertTrue(evaluate(prices.tail(120)))

    def test_live_entry_ma5_or_yesterday_high_and_low_hold(self):
        now=datetime.fromisoformat('2026-10-21T10:00:00+08:00')
        values,_=intraday(self.watch(),self.quote(),now=now)
        self.assertTrue(values['buy_signal']);self.assertTrue(values['live_above_ma5']);self.assertFalse(values['breakout_prev_high'])
        row={**self.watch(),'last4_close_sum':500}
        values,_=intraday(row,self.quote(price=103),now=now)
        self.assertTrue(values['buy_signal']);self.assertFalse(values['live_above_ma5']);self.assertTrue(values['breakout_prev_high'])
        values,_=intraday(row,self.quote(price=102),now=now);self.assertFalse(values['buy_signal'])
        values,_=intraday(self.watch(),self.quote(low=94.9),now=now);self.assertFalse(values['buy_signal'])
        values,_=intraday(self.watch(),self.quote(low=95),now=now);self.assertTrue(values['buy_signal'])

    def test_staleness_market_flags_and_no_trial_price(self):
        now=datetime.fromisoformat('2026-10-21T10:00:00+08:00')
        for change in (dict(isClose=True),dict(isTrial=True),dict(tradingHalt={'isHalted':True})):
            values,_=intraday(self.watch(),{**self.quote(),**change},now=now);self.assertFalse(values['buy_signal'])
        for change in (dict(date='2026-10-20'),dict(lastTrade={'price':None}),dict(lowPrice=None),dict(lowPrice=999)):
            with self.assertRaises(ValueError):intraday(self.watch(),{**self.quote(),**change},now=now)
        with self.assertRaises(ValueError):intraday(self.watch(),self.quote(),now=now+timedelta(minutes=4))

    def test_live_persistence_failure_clears_signal(self):
        class Frozen(datetime):
            @classmethod
            def now(cls,tz=None):return datetime.fromisoformat('2026-10-21T10:00:00+08:00')
        with tempfile.TemporaryDirectory() as folder,patch('strategy3.ROOT',Path(folder)),patch('strategy3.sleep'),patch('strategy3.datetime',Frozen),patch('fugle_data.FugleClient') as client:
            data=Path(folder)/'data';data.mkdir()
            path=data/'strategy3_top50.json'
            path.write_text(json.dumps(dict(rows=[self.watch()],missing_rows=[],summary={'target_date':'2026-10-20','rules_version':2})))
            client.return_value.quote.return_value=self.quote()
            first=refresh_live();self.assertTrue(first['rows'][0]['buy_signal'])
            client.return_value.quote.side_effect=ValueError('連線失敗')
            third=refresh_live();self.assertFalse(third['rows'][0]['buy_signal']);self.assertIsNone(third['rows'][0]['live_price'])
            self.assertFalse((Path(folder)/'.cache/strategy3_live_top50.json').exists())
            result=json.loads(path.read_text());result['summary']['rules_version']=1;path.write_text(json.dumps(result))
            with self.assertRaises(ValueError):refresh_live()

    def test_frontend_ids_and_jobs(self):
        html=(ROOT/'webapp/strategy3.html').read_text();js=(ROOT/'webapp/strategy3.js').read_text()
        self.assertFalse(set(re.findall(r"\$\('([^']+)'\)",js))-set(re.findall(r'id="([^"]+)"',html)))
        self.assertIn('MA60 > MA120',js);self.assertNotIn('vwap_reclaimed',js)
        with tempfile.TemporaryDirectory() as folder,patch('app.subprocess.Popen') as launch,patch('app.threading.Thread'):
            job=ScanJob();job.log=Path(folder)/'scan.log';job.start(dict(strategy='strategy3',scope='top50',live=True))
            self.assertIn(str(ROOT/'strategy3.py'),launch.call_args.args[0]);self.assertEqual(launch.call_args.args[0][-1],'--live')


if __name__=='__main__':unittest.main()
