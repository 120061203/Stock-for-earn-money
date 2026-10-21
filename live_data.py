"""FinMind 真實資料介接：日線為未還原收盤價，適用目前篩選，不作歷史回測。

官方資料說明：
https://finmind.github.io/tutor/TaiwanMarket/Technical/
https://finmind.github.io/tutor/TaiwanMarket/Fundamental/
可選擇以環境變數 FINMIND_TOKEN 提供 API token，請勿把金鑰寫入程式。
"""

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from time import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

from technicals import add_technicals
from api_errors import ApiLimitError, DataError


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
API_URL = "https://api.finmindtrade.com/api/v4/data"
DEFAULT_SYMBOLS = ("2330", "2317", "2454")
NAMES = {"2330": "台積電", "2317": "鴻海", "2454": "聯發科"}


class FinMindClient:
    def __init__(self, cache_dir=None, refresh=False):
        self.cache_dir = Path(cache_dir) if cache_dir else ROOT / ".cache" / "finmind"
        self.refresh = refresh
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "TaiwanStockScanner/1.0"
        token = os.environ.get("FINMIND_TOKEN", "").strip()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def fetch(self, dataset, symbol, start, end):
        params = dict(dataset=dataset, data_id=symbol, start_date=start, end_date=end)
        path = self.cache_dir / f"{dataset}_{symbol}_{start}_{end}.json"
        if not self.refresh and path.exists() and time() - path.stat().st_mtime < 21600:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    return data
            except (OSError, ValueError):
                pass
        print(f"  下載 {dataset}…", flush=True)
        try:
            response = self.session.get(API_URL, params=params, timeout=(5, 20))
            if response.status_code in (401, 402, 403, 429):
                raise ApiLimitError(f"FinMind HTTP {response.status_code}：API 額度或權限受限，請檢查 FINMIND_TOKEN／方案")
            response.raise_for_status()
            payload = response.json()
        except ApiLimitError:
            raise
        except requests.RequestException:
            # 不輸出 HTTP 例外原文，避免不必要的認證資訊外洩。
            raise DataError(f"{dataset} 連線失敗或逾時，請檢查網路／API 額度") from None
        except ValueError:
            raise DataError(f"{dataset} 回應不是 JSON") from None
        if isinstance(payload, dict) and payload.get("status") in (401, 402, 403, 429):
            raise ApiLimitError("FinMind API 額度或權限受限，請檢查 FINMIND_TOKEN／方案")
        if not isinstance(payload, dict) or payload.get("status") != 200:
            raise DataError(f"{dataset} API 未成功；請檢查服務狀態或 FINMIND_TOKEN")
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise DataError(f"{dataset} 沒有資料")
        if any(str(row.get("stock_id")) != symbol for row in data):
            raise DataError(f"{dataset} 回傳股票代號不符")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data


def statement_series(rows, field):
    frame = pd.DataFrame(rows)
    if not {"type", "date", "value"} <= set(frame.columns):
        raise DataError("財報欄位不完整")
    frame = frame[frame["type"] == field].copy()
    if frame.empty:
        raise DataError(f"財報缺少 {field}")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    if frame["date"].duplicated().any():
        raise DataError(f"{field} 同期有重複資料")
    return pd.to_numeric(frame.set_index("date")["value"], errors="raise").sort_index()


def calculate_roe(income_rows, balance_rows):
    """近四季歸母淨利 / 同期首末歸母權益平均 × 100。

    FinMind 損益資料採單季值，僅接受 origin_name 明確標示為歸母淨利的資料。
    同一英文代碼在舊年度可能代表綜合損益，不可直接混用。
    """
    income = statement_series(income_rows, "EquityAttributableToOwnersOfParent")
    latest = income.index[-1]
    if latest != latest.to_period("Q").end_time.normalize():
        raise DataError("損益日期不是季末")
    quarters = pd.period_range(end=latest.to_period("Q"), periods=4, freq="Q")
    dates = quarters.to_timestamp(how="end").normalize()
    values = income.reindex(dates)
    if values.isna().any():
        raise DataError("ROE 需要連續四季歸母淨利")
    date_strings = set(dates.strftime("%Y-%m-%d"))
    for row in income_rows:
        if row.get("type") == "EquityAttributableToOwnersOfParent" and row["date"] in date_strings:
            name = row.get("origin_name", "")
            if "綜合損益" in name or "淨利" not in name or "母公司" not in name:
                raise DataError("歸母淨利欄位定義不明，無法安全計算 ROE")
    # 部分金融業僅提供總權益與非控制權益：兩者相減即母公司權益。
    # 不可把未提供的非控制權益直接視為零。
    fields = {row.get("type") for row in balance_rows}
    if "EquityAttributableToOwnersOfParent" in fields:
        equity = statement_series(balance_rows, "EquityAttributableToOwnersOfParent")
    elif {"Equity", "NoncontrollingInterests"} <= fields:
        equity = statement_series(balance_rows, "Equity") - statement_series(balance_rows, "NoncontrollingInterests")
    else:
        raise DataError("缺少母公司權益，且無法由總權益減非控制權益計算")
    beginning = (quarters[0] - 1).end_time.normalize()
    endpoints = equity.reindex([beginning, latest])
    if endpoints.isna().any() or not endpoints.map(lambda x: math.isfinite(x) and x > 0).all():
        raise DataError("ROE 缺少期初／期末正值母公司權益")
    if not values.map(math.isfinite).all():
        raise DataError("淨利不是有限值")
    return float(values.sum() / endpoints.mean() * 100), latest.strftime("%Y-%m-%d")


def calculate_revenue_growth(rows):
    frame = pd.DataFrame(rows)
    required = {"revenue_year", "revenue_month", "revenue"}
    if not required <= set(frame.columns):
        raise DataError("月營收欄位不完整")
    # 採營收所屬年月，不把 API 的 date 當成營收月份或公告日。
    frame["month"] = pd.PeriodIndex(
        [f"{int(y):04d}-{int(m):02d}" for y, m in zip(frame.revenue_year, frame.revenue_month)], freq="M"
    )
    if frame["month"].duplicated().any():
        raise DataError("同一營收月份有重複資料")
    revenue = pd.to_numeric(frame.set_index("month")["revenue"], errors="raise").sort_index()
    latest = revenue.index[-1]
    previous = revenue.get(latest - 12, float("nan"))
    current = revenue.iloc[-1]
    if not math.isfinite(previous) or previous <= 0 or not math.isfinite(current) or current < 0:
        raise DataError("缺少去年同月有效營收")
    return float((current / previous - 1) * 100), str(latest)


def download_stock(client, symbol, today, price_client=None):
    end = today.isoformat()
    start = lambda days: (today - timedelta(days=days)).isoformat()
    if price_client is None:
        prices = pd.DataFrame(client.fetch("TaiwanStockPrice", symbol, start(365), end))
        prices = prices.rename(columns={"stock_id": "symbol", "Trading_Volume": "volume"})
    else:
        prices = price_client.candles(symbol, start(300), end)
    technicals = add_technicals(prices[["symbol", "date", "close", "volume"]])
    daily = technicals.iloc[-1]
    if daily[["ma60", "rsi14", "previous_rsi", "ma60_slope"]].isna().any():
        raise DataError("指標尚未齊備，MA60 斜率至少需要 61 筆日線")
    if daily["date"].date() > today:
        raise DataError("來源回傳未來股價")
    if (today - daily["date"].date()).days > 10:
        raise DataError("最近股價距今超過 10 天，請確認停牌或來源更新狀態")
    per = pd.DataFrame(client.fetch("TaiwanStockPER", symbol, start(45), end))
    per = per[per["date"] == daily["date"].strftime("%Y-%m-%d")]
    if len(per) != 1:
        raise DataError("缺少與最新收盤日一致的本益比")
    income = client.fetch("TaiwanStockFinancialStatements", symbol, start(900), end)
    balance = client.fetch("TaiwanStockBalanceSheet", symbol, start(900), end)
    roe, roe_date = calculate_roe(income, balance)
    revenues = client.fetch("TaiwanStockMonthRevenue", symbol, start(800), end)
    growth, revenue_month = calculate_revenue_growth(revenues)
    pe = float(per.iloc[0]["PER"])
    if not math.isfinite(pe):
        raise DataError("本益比不是有效數值")
    if pd.Timestamp(roe_date).date() > today or pd.Period(revenue_month, freq="M") >= pd.Period(today, freq="M"):
        raise DataError("來源回傳尚未結束期間的財務資料")
    record = dict(
        symbol=symbol, name=NAMES.get(symbol, symbol), pe_ratio=pe, roe=roe,
        revenue_growth=growth, price=float(daily["close"]), ma20=float(daily["ma20"]),
        ma60=float(daily["ma60"]), rsi14=float(daily["rsi14"]), volume=int(daily["volume"]),
        previous_rsi=float(daily["previous_rsi"]), ma60_slope=float(daily["ma60_slope"]),
        price_date=daily["date"].strftime("%Y-%m-%d"), roe_period_end=roe_date,
        revenue_month=revenue_month, source="Fugle+FinMind" if price_client else "FinMind",
        price_source="Fugle" if price_client else "FinMind", fundamentals_source="FinMind",
        volume_unit="shares", price_basis="unadjusted",
        fetched_at=datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
    )
    return record, prices[["symbol", "date", "close", "volume"]]


def prepare_live_data(symbols=None, refresh=False, provider=None, names=None):
    symbols = list(dict.fromkeys(symbols or DEFAULT_SYMBOLS))
    names = names or NAMES
    if any(not symbol.isdigit() or len(symbol) > 6 for symbol in symbols):
        raise DataError("請使用純數字台股代號，例如 2330")
    provider = provider or os.environ.get("MARKET_DATA_PROVIDER", "fugle")
    if provider not in {"fugle", "finmind"}:
        raise DataError("MARKET_DATA_PROVIDER 必須為 fugle 或 finmind")
    price_client = None
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    today = now.date()
    if provider == "fugle":
        from fugle_data import FugleClient
        price_client = FugleClient(refresh=refresh)
        # 官方歷史日 K 於 16:30 前完成更新；之前僅查到前一天。
        if (now.hour, now.minute) < (16, 30):
            today -= timedelta(days=1)
    client = FinMindClient(refresh=refresh)
    print(f"真實資料模式：{'富果行情 + FinMind 基本面' if price_client else 'FinMind'}，未還原日線；非即時報價。", flush=True)
    from market_state import market_for_date, unknown_market
    market_prices = None
    market_error = "缺少大盤資料"
    try:
        market_prices = pd.DataFrame(client.fetch(
            "TaiwanStockPrice", "TAIEX", (today - timedelta(days=300)).isoformat(), today.isoformat()
        ))[["date", "close"]]
        # 驗證整份指數資料格式；實際狀態在每檔股票各自的交易日計算。
        market_for_date(market_prices, today.isoformat())
    except ApiLimitError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        market_prices = None
        market_error = str(exc)
        print(f"大盤狀態未知：{exc}；保留評分，暫停 Entry signal。", flush=True)
    records, prices, failures = [], [], []
    for i, symbol in enumerate(symbols, start=1):
        print(f"[{i}/{len(symbols)}] 讀取 {symbol} {names.get(symbol, '')}", flush=True)
        try:
            record, daily = download_stock(client, symbol, today, price_client=price_client)
            record["name"] = names.get(symbol, record["name"])
            record.update(market_for_date(market_prices, record["price_date"]) if market_prices is not None
                          else unknown_market(market_error))
            records.append(record)
            prices.append(daily)
            print(f"  股價日 {record['price_date']}；ROE 季末 {record['roe_period_end']}；營收月 {record['revenue_month']}", flush=True)
            print(f"  PE={record['pe_ratio']:.2f} ROE={record['roe']:.2f}% 營收年增={record['revenue_growth']:.2f}%", flush=True)
        except ApiLimitError as exc:
            failures.append(dict(symbol=symbol, error=str(exc), status="額度或權限受限"))
            failures.extend(dict(symbol=pending, error="API 額度／權限受限，本次尚未掃描", status="尚未掃描")
                            for pending in symbols[i:])
            print(f"停止下載：{exc}。已完成資料與快取會保留；稍後重跑可重用快取。", flush=True)
            break
        except (ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
            failures.append(dict(symbol=symbol, error=str(exc), status="資料不足"))
            print(f"  略過：{exc}", flush=True)
    output_dir = ROOT / "data"
    output_dir.mkdir(exist_ok=True)
    if market_prices is not None:
        market_prices.to_csv(output_dir / "taiex_prices.csv", index=False, encoding="utf-8-sig")
    # 即使全部失敗也覆寫成空結果，避免把前次成功誤認為本次下載。
    columns = ["symbol", "name", "pe_ratio", "roe", "revenue_growth", "price", "ma20", "ma60",
               "rsi14", "previous_rsi", "ma60_slope", "volume", "price_date", "roe_period_end", "revenue_month", "source", "price_source",
               "fundamentals_source", "volume_unit", "price_basis", "fetched_at",
               "taiex_close", "taiex_ma60", "market_ok", "market_date", "market_source", "market_status"]
    indicator_path = output_dir / "real_indicators.csv"
    prices_path = output_dir / "real_prices.csv"
    pd.DataFrame(records, columns=columns).to_csv(indicator_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(failures, columns=["symbol", "error", "status"]).to_csv(output_dir / "download_errors.csv", index=False, encoding="utf-8-sig")
    all_prices = pd.concat(prices, ignore_index=True) if prices else pd.DataFrame(columns=["symbol", "date", "close", "volume"])
    all_prices.to_csv(prices_path, index=False, encoding="utf-8-sig")
    if not records:
        raise DataError("全部股票下載失敗，請查看上方原因及 data/download_errors.csv")
    print(f"資料取得完成：{len(records)} 檔成功、{len(failures)} 檔失敗；已存入 data/。", flush=True)
    return indicator_path, prices_path
