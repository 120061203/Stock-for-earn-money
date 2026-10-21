"""篩選股 2：超賣、反轉、止跌、籌碼改善，12項評分最高17分。"""
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
from technicals import calculate_rsi

ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo('Asia/Taipei')


WEIGHTS = dict(bias20_oversold=2, bias60_oversold=1, rsi_oversold=2,
               kd_golden_cross=2, above_ma5=1, above_ma10=1, long_lower_shadow=1,
               volume_shrink_after_panic=1, higher_low=1, margin_balance_down=1,
               foreign_turn_buy=2, trust_turn_buy=2)
CHIP_FIELDS = ('margin_balance_today', 'margin_balance_5d_ago',
               'foreign_net_buy_today', 'foreign_net_buy_yesterday',
               'trust_net_buy_today', 'trust_net_buy_yesterday')


def any_known(values):
    values = list(values)
    return True if any(value is True for value in values) else None if any(value is None for value in values) else False


def rebound_analysis(data):
    fields = ('close', 'open', 'low', 'ma5', 'ma10', 'ma20', 'ma60', 'rsi14',
              'k', 'd', 'prev_k', 'prev_d', 'volume', 'prev_volume', 'avg_volume20', 'prev_low')
    try:
        row = {key: float(data[key]) for key in fields}
        if not all(math.isfinite(value) for value in row.values()):
            raise ValueError()
        if any(row[key] <= 0 for key in ('close','open','low','ma5','ma10','ma20','ma60','prev_low','avg_volume20')):
            raise ValueError()
        if any(not 0 <= row[key] <= 100 for key in ('rsi14','k','d','prev_k','prev_d')):
            raise ValueError()
        if row['volume'] < 0 or row['prev_volume'] < 0 or row['low'] > min(row['open'], row['close']):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('反彈策略指標不足或無效，至少需要60筆完整日線') from None
    bias20 = round((row['close'] / row['ma20'] - 1) * 100, 10)
    bias60 = round((row['close'] / row['ma60'] - 1) * 100, 10)
    body = abs(row['close'] - row['open'])
    shadow = min(row['open'], row['close']) - row['low']
    checks = dict(bias20_oversold=bias20 <= -8, bias60_oversold=bias60 <= -12,
                  rsi_oversold=row['rsi14'] <= 30,
                  kd_golden_cross=row['prev_k'] <= row['prev_d'] and row['k'] > row['d'] and row['k'] < 25 and row['d'] < 25,
                  above_ma5=row['close'] > row['ma5'], above_ma10=row['close'] > row['ma10'],
                  long_lower_shadow=shadow > body * 1.5,
                  volume_shrink_after_panic=row['prev_volume'] > row['avg_volume20'] * 1.8 and row['volume'] < row['prev_volume'],
                  higher_low=row['low'] > row['prev_low'])
    def pair(today, previous, compare):
        try:
            current, before = float(data[today]), float(data[previous])
            if not all(math.isfinite(value) for value in (current, before)):
                return None
            if today.startswith('margin_') and min(current, before) < 0:
                return None
            return bool(compare(current, before))
        except (KeyError, TypeError, ValueError):
            return None
    checks.update(margin_balance_down=pair('margin_balance_today','margin_balance_5d_ago',lambda a,b:a<b),
                  foreign_turn_buy=pair('foreign_net_buy_today','foreign_net_buy_yesterday',lambda a,b:a>0 and b<=0),
                  trust_turn_buy=pair('trust_net_buy_today','trust_net_buy_yesterday',lambda a,b:a>0 and b<=0))
    groups = dict(oversold=any_known(checks[key] for key in ('bias20_oversold','bias60_oversold','rsi_oversold')),
                  technical_reversal=any_known(checks[key] for key in ('kd_golden_cross','above_ma5','above_ma10')),
                  price_stabilizing=any_known(checks[key] for key in ('volume_shrink_after_panic','long_lower_shadow','higher_low')),
                  chip_improving=any_known(checks[key] for key in ('margin_balance_down','foreign_turn_buy','trust_turn_buy')))
    candidate = all(value is True for value in groups.values())
    points = {'score_'+key: None if checks[key] is None else WEIGHTS[key] if checks[key] else 0 for key in WEIGHTS}
    score = sum(value for value in points.values() if value is not None)
    unknown = sum(WEIGHTS[key] for key in WEIGHTS if checks[key] is None)
    return dict(**checks, **groups, **points, bias20=bias20, bias60=bias60, body=body, lower_shadow=shadow,
                score=score, score_complete=unknown == 0, score_ceiling=score+unknown,
                candidate=candidate, buy=candidate, technical_ok=all(groups[key] is True for key in ('oversold','technical_reversal','price_stabilizing')),
                status='候選' if candidate else '籌碼待確認' if groups['chip_improving'] is None else '超賣觀察' if groups['oversold'] else '未符合')


def is_rebound_candidate(data):
    return rebound_analysis(data)['candidate']


def score_indicators(data):
    result = rebound_analysis(data)
    return {key:value for key,value in result.items() if key.startswith('score')}


def indicators(prices):
    required = {'date', 'open', 'high', 'low', 'close', 'volume'}
    if not required <= set(prices):
        raise ValueError('需要完整 OHLCV 日線，不能用只有收盤價的 CSV 替代')
    frame = prices.copy()
    frame['date'] = pd.to_datetime(frame.date, format='%Y-%m-%d', errors='raise')
    if frame.date.isna().any() or frame.date.duplicated().any():
        raise ValueError('日線日期缺失或重複')
    if 'symbol' in frame and frame.symbol.nunique() != 1:
        raise ValueError('每次只能計算一檔股票')
    frame = frame.sort_values('date').reset_index(drop=True)
    for field in ('open', 'high', 'low', 'close', 'volume'):
        frame[field] = pd.to_numeric(frame[field], errors='raise')
        if not frame[field].map(lambda v: math.isfinite(v) and (v >= 0 if field == 'volume' else v > 0)).all():
            raise ValueError('OHLCV 需為有限有效數值')
    if ((frame.high < frame[['open', 'close', 'low']].max(axis=1)) |
            (frame.low > frame[['open', 'close', 'high']].min(axis=1))).any():
        raise ValueError('OHLC 高低價矛盾')
    frame['ma5'] = frame.close.rolling(5).mean()
    frame['ma10'] = frame.close.rolling(10).mean()
    frame['ma60'] = frame.close.rolling(60).mean()
    frame['ma20'] = frame.close.rolling(20).mean()
    frame['rsi'] = calculate_rsi(frame.close)
    frame['avg_volume20'] = frame.volume.shift(1).rolling(20).mean()
    frame['volume_ratio'] = frame.volume / frame.avg_volume20.where(frame.avg_volume20 > 0)
    highest = frame.high.rolling(9).max()
    lowest = frame.low.rolling(9).min()
    rsv = (100 * (frame.close - lowest) / (highest - lowest).where(highest != lowest)).mask((highest == lowest) & highest.notna(), 50)
    k, d, ks, ds = 50.0, 50.0, [], []
    for value in rsv:
        if pd.isna(value):
            ks.append(float('nan')); ds.append(float('nan')); continue
        k = 2 / 3 * k + value / 3
        d = 2 / 3 * d + k / 3
        ks.append(k); ds.append(d)
    frame['k'], frame['d'] = ks, ds
    return frame


def evaluate(prices, chips=None):
    frame = indicators(prices)
    if len(frame) < 60:
        raise ValueError('至少需要60筆完整日線以計算MA60')
    previous, current = frame.iloc[-2], frame.iloc[-1]
    row = {key:float(current[key]) for key in ('close','open','low','ma5','ma10','ma20','ma60','k','d','volume','avg_volume20','volume_ratio')}
    row.update(rsi14=float(current.rsi), prev_k=float(previous.k), prev_d=float(previous.d),
               prev_volume=float(previous.volume), prev_low=float(previous.low))
    row.update({key:None for key in CHIP_FIELDS})
    row.update(chips or {})
    row.update(rebound_analysis(row))
    row.update(price_date=current.date.strftime('%Y-%m-%d'), previous_date=previous.date.strftime('%Y-%m-%d'),
               margin_compare_date=frame.iloc[-6].date.strftime('%Y-%m-%d'),
               price=row['close'], rsi=row['rsi14'])
    return row


def scan(scope='top50'):
    if scope not in {'top50', 'all'}:
        raise ValueError('掃描範圍無效')
    from live_data import FinMindClient
    from chip_data import fetch_chips
    from fugle_data import FugleClient
    load_dotenv(ROOT / '.env', override=False)
    now = datetime.now(TAIPEI)
    end_day = now.date() - timedelta(days=int((now.hour, now.minute) < (16, 30)))
    end = end_day.isoformat()
    start = (end_day - timedelta(days=300)).isoformat()
    path = ROOT / 'data' / ('top50_market_cap.csv' if scope == 'top50' else 'all_listed_stocks.csv')
    if not path.is_file():
        from market_universe import top_market_cap
        universe = top_market_cap(count=50 if scope == 'top50' else None)
    else:
        universe = pd.read_csv(path, dtype={'symbol': str})
    finmind = FinMindClient()
    try:
        fugle = FugleClient()
    except ValueError:
        fugle = None
    fugle_blocked = False
    chip_blocked = {}
    rows, errors = [], []
    stocks = universe.to_dict('records')
    for index, stock in enumerate(stocks, 1):
        symbol = str(stock['symbol'])
        print(f'策略2 [{index}/{len(stocks)}] {symbol}', flush=True)
        try:
            price_source = 'Fugle'
            prices = None
            if fugle and not fugle_blocked:
                try:
                    prices = fugle.candles(symbol, start, end, include_ohlc=True)
                except ApiLimitError:
                    fugle_blocked = True
                except (ValueError, KeyError, TypeError):
                    pass
            if prices is None:
                prices = pd.DataFrame(finmind.fetch('TaiwanStockPrice', symbol, start, end)).rename(
                    columns={'stock_id': 'symbol', 'max': 'high', 'min': 'low', 'Trading_Volume': 'volume'})
                price_source = 'FinMind'
            if pd.to_datetime(prices.date).max().date() > end_day:
                raise ValueError('日線含未完成或未來日期')
            if (end_day - pd.to_datetime(prices.date).max().date()).days > 7:
                raise ValueError('日線距目標日超過 7 天，可能停牌或來源未更新')
            result = evaluate(prices)
            chips, chip_errors = fetch_chips(finmind, symbol, result['price_date'], result['previous_date'],
                                            result['margin_compare_date'], chip_blocked)
            result = evaluate(prices, chips)
            result.update(symbol=symbol, name=stock.get('name', symbol), market=stock.get('market', ''),
                          source=price_source, chip_source='FinMind', error='；'.join(chip_errors))
            if chip_errors:
                errors.append(dict(symbol=symbol, reason='；'.join(chip_errors)))
            rows.append(result)
        except ApiLimitError as exc:
            errors.append(dict(symbol=symbol, reason=str(exc)))
            errors.extend(dict(symbol=str(s['symbol']), reason='API 額度或權限受限，尚未掃描') for s in stocks[index:])
            break
        except (ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
            errors.append(dict(symbol=symbol, reason=str(exc)))
        sleep(1.1)
    latest_date = max((r['price_date'] for r in rows), default=None)
    for row in rows:
        if row['buy'] and row['price_date'] != latest_date:
            row.update(buy=False, candidate=False, status='資料日期落後', error='日線落後本次其他股票的最近交易日')
    rows.sort(key=lambda row: (-row['score'], -row['volume_ratio'], row['symbol']))
    summary = dict(scope=scope, target_date=end, completed_at=datetime.now(TAIPEI).isoformat(),
                   universe=len(stocks), scanned=len(rows), missing=len(errors), buy=sum(r['buy'] for r in rows),
                   universe_date=sorted(universe.market_cap_date.dropna().astype(str).unique().tolist()) if 'market_cap_date' in universe else [],
                   date_policy='16:30 前查至昨日；依各檔最近已完成日 K 判斷，訊號日期請見個股日期',
                   latest_price_date=latest_date, rules_version=2, score_version=2, score_max=17,
                   incomplete_scores=sum(not row['score_complete'] for row in rows))
    output = dict(rows=rows, missing_rows=errors, summary=summary, available=True)
    folder = ROOT / 'data'; folder.mkdir(exist_ok=True)
    target = folder / f'strategy2_{scope}.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(output, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    pd.DataFrame(rows).to_csv(folder / f'strategy2_{scope}.csv', index=False, encoding='utf-8-sig')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['top50', 'all'], default='top50')
    args = parser.parse_args()
    scan(args.scope)
