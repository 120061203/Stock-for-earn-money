"""強勢股拉回：日線Watchlist、11分評分及MA5／昨日高點買點。"""
import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo
import pandas as pd
from dotenv import load_dotenv
from api_errors import ApiLimitError
from strategy2 import indicators

ROOT=Path(__file__).resolve().parent
TAIPEI=ZoneInfo('Asia/Taipei')
WEIGHTS=dict(ma60_above_ma120=1,above_ma60=1,near_high60=1,pullback_ma20=2,
             rsi_pullback=1,volume_shrink=1,higher_low=1,above_ma5=1,above_prev_high=2)


def analyze(data):
    fields=('close','low','ma5','ma20','ma60','ma120','high_60d','distance_ma20','rsi14','volume','avg_volume20','prev_low','prev_high')
    try:
        row={key:float(data[key]) for key in fields}
        if not all(math.isfinite(v) for v in row.values()):raise ValueError()
        if any(row[key]<=0 for key in ('close','low','ma5','ma20','ma60','ma120','high_60d','prev_low','prev_high','avg_volume20')):raise ValueError()
        if not 0<=row['rsi14']<=100 or row['volume']<0:raise ValueError()
    except (KeyError,TypeError,ValueError):
        raise ValueError('強勢拉回指標缺漏或無效；至少需120筆完整日線') from None
    checks=dict(ma60_above_ma120=row['ma60']>row['ma120'],above_ma60=row['close']>row['ma60'],
                near_high60=row['close']>=row['high_60d']*.85,
                pullback_ma20=-8<=row['distance_ma20']<=2,rsi_pullback=40<=row['rsi14']<=60,
                volume_shrink=row['volume']<=row['avg_volume20']*1.1,higher_low=row['low']>=row['prev_low'],
                above_ma5=row['close']>row['ma5'],above_prev_high=row['close']>row['prev_high'])
    points={'score_'+key:WEIGHTS[key] if value else 0 for key,value in checks.items()}
    watchlist=all(checks[key] for key in ('ma60_above_ma120','above_ma60','near_high60','pullback_ma20','rsi_pullback','volume_shrink','higher_low'))
    daily_buy=watchlist and (checks['above_ma5'] or checks['above_prev_high'])
    return dict(**checks,**points,score=sum(points.values()),watchlist=watchlist,daily_buy=daily_buy,
                status='日線買點' if daily_buy else 'Watchlist' if watchlist else '未符合')


def evaluate(prices):
    frame=indicators(prices)
    if len(frame)<120:raise ValueError('至少需120筆完整日線以計算MA120')
    frame['ma120']=frame.close.rolling(120).mean()
    current,previous=frame.iloc[-1],frame.iloc[-2]
    row={key:float(current[key]) for key in ('close','low','ma5','ma20','ma60','ma120','volume','avg_volume20')}
    row.update(high_60d=float(frame.high.tail(60).max()),last4_close_sum=float(frame.close.tail(4).sum()),
               distance_ma20=round((current.close/current.ma20-1)*100,10),rsi14=float(current.rsi),
               prev_low=float(previous.low),prev_high=float(previous.high),day_high=float(current.high),
               live_avg_volume20=float(frame.volume.tail(20).mean()),price_date=current.date.strftime('%Y-%m-%d'))
    row.update(analyze(row))
    row.update(buy_signal=False,live_status='尚未觀察',live_error='')
    return row


def intraday(row,payload,previous=None,now=None):
    now=now or datetime.now(TAIPEI)
    today=now.date().isoformat()
    if not row['watchlist']:raise ValueError('僅觀察Watchlist個股')
    if payload.get('symbol')!=row['symbol'] or payload.get('date')!=today:raise ValueError('盤中報價代號不符或非今日')
    if row['price_date']>=today:raise ValueError('Watchlist須使用今天以前的已完成日線')
    if (now.date()-datetime.fromisoformat(row['price_date']).date()).days>7:raise ValueError('Watchlist已過期，請更新日線')
    trade=payload.get('lastTrade') or {}
    def number(value):
        try:
            result=float(value)
            if not math.isfinite(result) or result<=0:raise ValueError()
            return result
        except (TypeError,ValueError):raise ValueError('缺有效成交價、盤中低點、MA5資料或時間') from None
    price=number(trade.get('price',payload.get('closePrice')))
    stamp=number(trade.get('time',payload.get('closeTime')))
    trade_time=datetime.fromtimestamp(stamp/1_000_000,TAIPEI)
    age=(now-trade_time).total_seconds()
    if trade_time.date()!=now.date() or age<0 or (age>180 and not payload.get('isClose')):
        raise ValueError('成交時間非今日、為未來或已超過3分鐘')
    # 以最後四個已完成收盤加即時成交價估算當日MA5。
    live_ma5=(number(row['last4_close_sum'])+price)/5
    low=number(payload.get('lowPrice'))
    if low>price:raise ValueError('盤中最低價高於實際成交價，資料矛盾')
    above_ma5=price>live_ma5
    breakout=price>row['day_high']
    low_holds=low>=row['low']
    active=not (payload.get('isClose') or payload.get('isTrial') or (payload.get('tradingHalt') or {}).get('isHalted'))
    signal=bool((above_ma5 or breakout) and low_holds and active)
    values=dict(live_price=price,live_ma5=live_ma5,live_low=low,trade_time=trade_time.isoformat(),
                live_above_ma5=above_ma5,breakout_prev_high=breakout,live_low_holds=low_holds,
                buy_signal=signal,live_error='',live_status='BUY Signal' if signal else '觀察中' if active else '已收盤' if payload.get('isClose') else '非正常盤中交易')
    state={}
    return values,state


def save(scope,result):
    folder=ROOT/'data';folder.mkdir(exist_ok=True)
    target=folder/f'strategy3_{scope}.json';temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
    temporary.replace(target)
    pd.DataFrame(result['rows']).to_csv(target.with_suffix('.csv'),index=False,encoding='utf-8-sig')


def scan(scope='top50'):
    from fugle_data import FugleClient
    from live_data import FinMindClient
    if scope not in {'top50','all'}:raise ValueError('範圍無效')
    load_dotenv(ROOT/'.env',override=False)
    now=datetime.now(TAIPEI);end_day=now.date()-timedelta(days=int((now.hour,now.minute)<(16,30)))
    end=end_day.isoformat();start=(end_day-timedelta(days=300)).isoformat()
    path=ROOT/'data'/('top50_market_cap.csv' if scope=='top50' else 'all_listed_stocks.csv')
    if path.is_file():universe=pd.read_csv(path,dtype={'symbol':str})
    else:
        from market_universe import top_market_cap
        universe=top_market_cap(count=50 if scope=='top50' else None)
    finmind=FinMindClient()
    try:fugle=FugleClient()
    except ValueError:fugle=None
    rows,errors=[],[];stocks=universe.to_dict('records');fugle_blocked=False
    for index,stock in enumerate(stocks,1):
        symbol=str(stock['symbol']);print(f'策略3 [{index}/{len(stocks)}] {symbol}',flush=True)
        try:
            prices=None;source='Fugle'
            if fugle and not fugle_blocked:
                try:prices=fugle.candles(symbol,start,end,include_ohlc=True)
                except ApiLimitError:fugle_blocked=True
                except (ValueError,KeyError,TypeError):pass
            if prices is None:
                prices=pd.DataFrame(finmind.fetch('TaiwanStockPrice',symbol,start,end)).rename(columns={'stock_id':'symbol','max':'high','min':'low','Trading_Volume':'volume'});source='FinMind'
            row=evaluate(prices)
            day=datetime.fromisoformat(row['price_date']).date()
            if not 0<=(end_day-day).days<=7:raise ValueError('日線日期超出範圍或過舊')
            row.update(symbol=symbol,name=stock.get('name',symbol),market=stock.get('market',''),source=source)
            rows.append(row)
        except ApiLimitError as exc:
            errors.append(dict(symbol=symbol,reason=str(exc)))
            errors.extend(dict(symbol=str(s['symbol']),reason='API受限，尚未掃描') for s in stocks[index:]);break
        except (ValueError,KeyError,TypeError,IndexError) as exc:errors.append(dict(symbol=symbol,reason=str(exc)))
        sleep(1.1)
    latest=max((row['price_date'] for row in rows),default=None)
    for row in rows:
        if row['watchlist'] and row['price_date']!=latest:row.update(watchlist=False,daily_buy=False,status='資料日期落後')
    rows.sort(key=lambda row:(-row['score'],row['symbol']))
    summary=dict(scope=scope,target_date=end,completed_at=datetime.now(TAIPEI).isoformat(),universe=len(stocks),
                 scanned=len(rows),missing=len(errors),watchlist=sum(r['watchlist'] for r in rows),
                 daily_buy=sum(r['daily_buy'] for r in rows),buy=0,score_max=11,rules_version=2)
    result=dict(rows=rows,missing_rows=errors,summary=summary,available=True);save(scope,result);return result


def refresh_live(scope='top50'):
    from fugle_data import FugleClient
    path=ROOT/'data'/f'strategy3_{scope}.json'
    if not path.is_file():raise ValueError('請先更新日線Watchlist')
    result=json.loads(path.read_text())
    if result['summary'].get('rules_version')!=2:raise ValueError('策略3已改版，請先重新更新日線')
    watch=[row for row in result['rows'] if row['watchlist']]
    yesterday=(datetime.now(TAIPEI).date()-timedelta(days=1)).isoformat()
    if watch and result['summary']['target_date']!=yesterday:
        raise ValueError('請先更新日線Watchlist，查詢截止日須為昨日；盤後不執行盤中訊號')
    blocked='';checked=0
    try:client=FugleClient() if watch else None
    except ValueError as exc:client=None;blocked=str(exc)
    for index,row in enumerate(watch,1):
        print(f'策略3 [{index}/{len(watch)}] 盤中 {row["symbol"]}',flush=True)
        row.update(buy_signal=False,live_status='待查詢',live_error='',live_price=None,live_ma5=None,live_low=None,trade_time=None,live_above_ma5=False,breakout_prev_high=False,live_low_holds=False)
        try:
            if blocked:raise ValueError(blocked)
            values,_=intraday(row,client.quote(row['symbol']))
            row.update(values);checked+=1
        except ApiLimitError as exc:
            blocked=str(exc);row.update(live_status='額度或權限受限',live_error=str(exc))
        except (ValueError,KeyError,TypeError,OverflowError,OSError) as exc:
            row.update(live_status='資料待確認',live_error=str(exc))
        sleep(1.1)
    result['summary'].update(live_updated_at=datetime.now(TAIPEI).isoformat(),live_checked=checked,live_missing=len(watch)-checked,
                             buy=sum(row.get('buy_signal',False) for row in result['rows']))
    save(scope,result);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--scope',choices=['top50','all'],default='top50')
    parser.add_argument('--live',action='store_true');args=parser.parse_args()
    (refresh_live if args.live else scan)(args.scope)
