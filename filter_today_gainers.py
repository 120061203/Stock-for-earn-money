"""篩選今日上市櫃普通股漲幅 3%~5%，累計量至少前 20 交易日均量 2 倍。

名單使用 data/all_listed_stocks.csv；缺成交價或非今日資料不納入，並在摘要揭露。
    .venv/bin/python filter_today_gainers.py --min-change 3 --max-change 5
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from scan_errors import ScanFailure, emit_failure


ROOT = Path(__file__).resolve().parent
TAIPEI = ZoneInfo("Asia/Taipei")
API_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


def load_universe():
    """全新部署不含 data/，首次掃描自行建立普通股名單。"""
    path = ROOT / 'data' / 'all_listed_stocks.csv'
    if not path.is_file():
        print('首次掃描：下載上市櫃普通股名單…', flush=True)
        from market_universe import top_market_cap
        try:
            universe = top_market_cap(count=None)
        except requests.RequestException:
            raise ScanFailure('UNIVERSE_DOWNLOAD') from None
        except OSError:
            raise ScanFailure('DATA_DIRECTORY') from None
        except (ValueError, KeyError, TypeError):
            raise ScanFailure('UNIVERSE_DOWNLOAD') from None
    else:
        try:
            universe = pd.read_csv(path, dtype={'symbol': str})
        except (ValueError, pd.errors.EmptyDataError):
            raise ScanFailure('UNIVERSE_INVALID') from None
        except OSError:
            raise ScanFailure('DATA_DIRECTORY') from None
    required = {'symbol', 'name', 'market', 'market_cap_date'}
    if universe.empty or not required <= set(universe):
        raise ScanFailure('UNIVERSE_INVALID')
    universe = universe.copy()
    if universe[list(required)].isna().any().any():
        raise ScanFailure('UNIVERSE_INVALID')
    universe['symbol'] = universe.symbol.astype(str)
    if universe.symbol.duplicated().any() or not universe.symbol.str.fullmatch(r'[0-9]{4}').all() or not universe.market.isin(['TWSE', 'TPEx']).all():
        raise ScanFailure('UNIVERSE_INVALID')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            universe.to_csv(path, index=False, encoding='utf-8-sig')
    except OSError:
        raise ScanFailure('DATA_DIRECTORY') from None
    return universe


def fetch_mis(session, channels):
    try:
        response = session.get(API_URL, params={'ex_ch': channels, 'json': '1', 'delay': '0'}, timeout=(5, 20))
        if response.status_code != 200:
            raise ScanFailure('MIS_HTTP', response.status_code)
        payload = response.json()
    except requests.Timeout:
        raise ScanFailure('MIS_TIMEOUT') from None
    except requests.exceptions.JSONDecodeError:
        raise ScanFailure('MIS_RESPONSE') from None
    except requests.RequestException:
        raise ScanFailure('MIS_CONNECTION') from None
    except ScanFailure:
        raise
    except ValueError:
        raise ScanFailure('MIS_RESPONSE') from None
    if not isinstance(payload, dict) or payload.get('rtcode') != '0000' or not isinstance(payload.get('msgArray'), list):
        raise ScanFailure('MIS_RESPONSE')
    return payload


def average_volume(history, today, days=20):
    frame = pd.DataFrame(history)
    if not {"date", "Trading_Volume"} <= set(frame):
        raise ValueError("歷史資料缺少日期或成交股數")
    frame["date"] = pd.to_datetime(frame.date, format="%Y-%m-%d", errors="raise")
    if frame.date.isna().any() or frame.date.duplicated().any():
        raise ValueError("歷史量日期缺漏或重複")
    frame = frame[frame.date < pd.Timestamp(today)].sort_values("date").tail(days)
    if len(frame) != days:
        raise ValueError(f"不足 {days} 個交易日成交量")
    frame["Trading_Volume"] = pd.to_numeric(frame.Trading_Volume, errors="raise")
    if not frame.Trading_Volume.map(lambda x: math.isfinite(x) and x >= 0).all():
        raise ValueError("歷史量無效")
    average = float(frame.Trading_Volume.mean())
    if average <= 0:
        raise ValueError("日均量不可為零")
    return average, frame.date.iloc[0].strftime("%Y-%m-%d"), frame.date.iloc[-1].strftime("%Y-%m-%d")


def filter_volume(quotes, today, days, minimum_ratio):
    from live_data import FinMindClient
    from api_errors import ApiLimitError
    client = FinMindClient()
    end = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
    start = (datetime.fromisoformat(today) - timedelta(days=max(days * 4, 120))).date().isoformat()
    results, stopped = [], False
    for index, quote in enumerate(quotes, start=1):
        result = {**quote, "volume_average_days": days, "volume_history_source": "FinMind",
                  "volume_ratio": None, "avg_daily_volume_shares": None, "volume_pass": False,
                  "volume_status": "尚未查詢", "volume_error": ""}
        print(f"量能 [{index}/{len(quotes)}] {quote['symbol']} {quote['name']}", flush=True)
        try:
            current = quote.get("today_volume_shares")
            if current is None or not math.isfinite(current) or current < 0:
                raise ValueError("缺少今日累計成交量")
            if stopped:
                raise ValueError("FinMind API 額度受限，本次尚未檢查")
            history = client.fetch("TaiwanStockPrice", quote["symbol"], start, end)
            average, first, last = average_volume(history, today, days)
            if (datetime.fromisoformat(today) - datetime.fromisoformat(last)).days > 10:
                raise ValueError("日均量資料距今超過 10 日")
            ratio = current / average
            result.update(avg_daily_volume_shares=average, volume_ratio=ratio, volume_pass=ratio >= minimum_ratio,
                          volume_average_start=first, volume_average_end=last, volume_status="已檢查")
        except ApiLimitError as exc:
            stopped = True
            result.update(volume_status="額度受限", volume_error=str(exc))
        except (ValueError, KeyError, TypeError) as exc:
            result.update(volume_status="資料不足" if not stopped else "尚未檢查", volume_error=str(exc))
        results.append(result)
    return results


def extract_quote(row, today):
    if row.get("d") != today.replace("-", ""):
        return None, "非今日資料"
    trade = row.get("trade") or {}
    raw_price, trade_time = row.get("z"), row.get("t")
    if raw_price in (None, "", "-"):
        raw_price, trade_time = trade.get("z"), trade.get("t")
    try:
        price, reference = Decimal(str(raw_price)), Decimal(str(row.get("y")))
        if not price.is_finite() or not reference.is_finite() or price <= 0 or reference <= 0:
            return None, "缺有效成交價或參考價"
    except (InvalidOperation, ValueError):
        return None, "缺有效成交價或參考價"
    pct = (price / reference - 1) * 100
    # MIS v 為整股累計張數；換成股，再與歷史日 K 的成交股數比較。
    try:
        lots = Decimal(str(row.get("v")))
        volume = float(lots * 1000) if lots.is_finite() and lots >= 0 else None
    except (InvalidOperation, ValueError):
        volume = None
    return dict(symbol=row["c"], name=row.get("n", row["c"]),
                market="上市" if row.get("ex") == "tse" else "上櫃",
                price=float(price), reference_price=float(reference), change_percent=float(pct),
                trade_date=today, trade_time=trade_time or "未知", source="TWSE MIS",
                today_volume_shares=volume, today_volume_source="TWSE MIS v (整股張數×1000)"), None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-change", type=float, default=3)
    parser.add_argument("--max-change", type=float, default=5)
    parser.add_argument("--min-volume-ratio", type=float, default=2, help="今日累計量／歷史日均量最低倍數，預設 2")
    parser.add_argument("--avg-volume-days", type=int, default=20, help="日均量交易日數，預設 20，不含今天")
    args = parser.parse_args()
    if not (0 <= args.min_change <= args.max_change < 100):
        parser.error("漲幅範圍無效")
    if not math.isfinite(args.min_volume_ratio) or args.min_volume_ratio <= 0 or args.avg_volume_days < 1:
        parser.error("量能倍數與日均量天數須為正數")
    today = datetime.now(TAIPEI).date().isoformat()
    universe = load_universe()
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    seen, valid, missing, selected = set(), [], [], []
    records = universe.to_dict("records")
    allowed_symbols = set(universe.symbol)
    for offset in range(0, len(records), 80):
        batch = records[offset:offset + 80]
        channels = "|".join(f"{'tse' if row['market'] == 'TWSE' else 'otc'}_{row['symbol']}.tw" for row in batch)
        payload = fetch_mis(session, channels)
        for row in payload.get("msgArray", []):
            symbol = row.get("c")
            if symbol not in allowed_symbols or symbol in seen:
                continue
            seen.add(symbol)
            quote, reason = extract_quote(row, today)
            if reason:
                missing.append(dict(symbol=symbol, reason=reason))
                continue
            valid.append(quote)
            if args.min_change <= quote["change_percent"] <= args.max_change:
                selected.append(quote)
        print(f"已查詢 {min(offset + 80, len(records))}/{len(records)} 檔", flush=True)
        sleep(0.15)
    missing.extend(dict(symbol=symbol, reason="API 未回傳") for symbol in set(universe.symbol) - seen)
    if not valid:
        raise ScanFailure('NO_TODAY_QUOTES')
    selected = [quote for quote in valid if args.min_change <= quote["change_percent"] <= args.max_change]
    selected.sort(key=lambda row: (-row["change_percent"], row["symbol"]))
    columns = ["symbol", "name", "market", "price", "reference_price", "change_percent", "trade_date", "trade_time", "source",
               "today_volume_shares", "today_volume_source"]
    output = ROOT / "data" / f"gainers_{today}_{args.min_change:g}_{args.max_change:g}.csv"
    pd.DataFrame(selected, columns=columns).to_csv(output, index=False, encoding="utf-8-sig")
    pd.DataFrame(missing, columns=["symbol", "reason"]).to_csv(output.with_suffix(".missing.csv"), index=False, encoding="utf-8-sig")
    summary = dict(trade_date=today, fetched_at=datetime.now(TAIPEI).isoformat(), universe=len(records), valid_today=len(valid),
                   selected=len(selected), missing=len(missing), quote_source="TWSE MIS", history_source="FinMind",
                   provider_policy="mis_finmind_only",
                   universe_date=sorted(universe.market_cap_date.dropna().unique().tolist()))
    output.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    checked = filter_volume(selected, today, args.avg_volume_days, args.min_volume_ratio)
    matched = [row for row in checked if row["volume_pass"]]
    matched.sort(key=lambda row: (-row["volume_ratio"], -row["change_percent"], row["symbol"]))
    volume_columns = columns + ["volume_average_days", "avg_daily_volume_shares", "volume_ratio", "volume_pass",
                                "volume_average_start", "volume_average_end", "volume_history_source", "volume_status", "volume_error"]
    volume_output = output.with_name(output.stem + f"_vol{args.min_volume_ratio:g}x_ma{args.avg_volume_days}.csv")
    pd.DataFrame(matched, columns=volume_columns).to_csv(volume_output, index=False, encoding="utf-8-sig")
    pd.DataFrame(checked, columns=volume_columns).to_csv(volume_output.with_suffix(".checked.csv"), index=False, encoding="utf-8-sig")
    summary.update(volume_ratio_min=args.min_volume_ratio, volume_average_days=args.avg_volume_days,
                   volume_checked=sum(row["volume_status"] == "已檢查" for row in checked), volume_selected=len(matched),
                   volume_unknown=sum(row["volume_status"] != "已檢查" for row in checked),
                   volume_comparison="今日整股累計量 / 前N個交易日日均成交股數；不推估全日量",
                   completed_at=datetime.now(TAIPEI).isoformat())
    volume_output.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(pd.DataFrame(matched, columns=volume_columns).to_string(index=False))
    print(json.dumps(summary, ensure_ascii=False))
    print(f"漲幅與量能篩選結果：{volume_output}")


if __name__ == "__main__":
    try:
        main()
    except ScanFailure as exc:
        emit_failure(exc)
        sys.exit(1)
    except OSError:
        emit_failure(ScanFailure('DATA_DIRECTORY'))
        sys.exit(1)
