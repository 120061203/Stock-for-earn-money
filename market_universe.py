"""上市櫃普通股市值名單：官方股數 × 未還原收盤價，單位新台幣元。

排除 ETF、特別股、權證、興櫃。市值為全部已發行普通股，非自由流通市值。
"""

import json
import math
from datetime import datetime
from pathlib import Path
from time import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent
SOURCES = {
    "companies": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "prices": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "tpex": "https://www.tpex.org.tw/openapi/v1/tpex_daily_market_value",
}


def roc_date(value):
    value = str(value).replace("/", "").strip()
    if len(value) != 7 or not value.isdigit():
        raise ValueError("官方市值資料日期格式不符")
    return datetime(int(value[:3]) + 1911, int(value[3:5]), int(value[5:7])).date().isoformat()


def positive(value):
    number = float(str(value).replace(",", "").strip())
    if not math.isfinite(number) or number <= 0:
        raise ValueError("市值所需價格／股數必須為有限正數")
    return number


def build_universe(companies, prices, tpex, include_missing=False):
    quotes = {row["Code"]: row for row in prices}
    records, missing = [], []
    for row in companies:
        symbol = row["公司代號"].strip()
        if not (len(symbol) == 4 and symbol.isdigit()):
            continue
        quote = quotes.get(symbol)
        if quote is None:
            missing.append(symbol)
            if include_missing:
                records.append(dict(symbol=symbol, name=row["公司簡稱"].strip(), market="TWSE", market_cap=None,
                                    market_cap_date=roc_date(row["出表日期"])))
            continue
        try:
            close = positive(quote["ClosingPrice"])
            shares = positive(row["已發行普通股數或TDR原股發行股數"])
        except ValueError:
            missing.append(symbol)
            if include_missing:
                records.append(dict(symbol=symbol, name=row["公司簡稱"].strip(), market="TWSE", market_cap=None,
                                    market_cap_date=roc_date(row["出表日期"])))
            continue
        share_date, price_date = roc_date(row["出表日期"]), roc_date(quote["Date"])
        if share_date != price_date:
            raise ValueError("上市公司股數與收盤價日期不同，請稍後重試")
        records.append(dict(symbol=symbol, name=row["公司簡稱"].strip(), market="TWSE",
                            market_cap=shares * close, market_cap_date=price_date,
                            shares=shares, market_cap_close=close))
    for row in tpex:
        symbol = row["SecuritiesCompanyCode"].strip()
        if not (len(symbol) == 4 and symbol.isdigit()):
            continue
        try:
            close, shares = positive(row["ClosePrice"]), positive(row["Capitals"])
        except ValueError:
            missing.append(symbol)
            if include_missing:
                records.append(dict(symbol=symbol, name=row["CompanyName"].strip(), market="TPEx", market_cap=None,
                                    market_cap_date=roc_date(row["Date"])))
            continue
        records.append(dict(symbol=symbol, name=row["CompanyName"].strip(), market="TPEx",
                            market_cap=shares * close, market_cap_date=roc_date(row["Date"]),
                            shares=shares, market_cap_close=close))
    frame = pd.DataFrame(records)
    if frame.empty or frame.symbol.duplicated().any():
        raise ValueError("官方市值名單為空或股票代號重複")
    if frame.market_cap_date.nunique() != 1:
        raise ValueError("上市與上櫃資料日期不同，不能合併市值排名，請稍後重試")
    if set(frame.market) != {"TWSE", "TPEx"}:
        raise ValueError("缺少上市或上櫃市場資料")
    frame = frame.sort_values(["market_cap", "symbol"], ascending=[False, True]).reset_index(drop=True)
    frame.insert(0, "market_cap_rank", range(1, len(frame) + 1))
    frame["market_cap_rank"] = frame["market_cap_rank"].astype("Int64").mask(frame.market_cap.isna())
    return frame, missing


def top_market_cap(count=50, refresh=False):
    # count=None 表示全部上市櫃普通股，保留缺少市值的公司。
    if count is not None and (not isinstance(count, int) or count < 1):
        raise ValueError("市值檔數必須為正整數")
    cache = ROOT / ".cache" / "market_universe"
    cache.mkdir(parents=True, exist_ok=True)
    payloads = {}
    for name, url in SOURCES.items():
        path = cache / f"{name}.json"
        data = None
        if not refresh and path.exists() and time() - path.stat().st_mtime < 21600:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        if data is None:
            print(f"讀取官方市值資料：{name}…", flush=True)
            try:
                response = requests.get(url, timeout=(5, 20))
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError):
                raise ValueError(f"官方市值資料 {name} 下載失敗") from None
            if not isinstance(data, list) or not data:
                raise ValueError(f"官方市值資料 {name} 為空或格式錯誤")
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        payloads[name] = data
    universe, missing = build_universe(**payloads, include_missing=count is None)
    as_of = datetime.fromisoformat(universe.market_cap_date.iloc[0]).date()
    age = (datetime.now(ZoneInfo("Asia/Taipei")).date() - as_of).days
    if not 0 <= age <= 10:
        raise ValueError("官方市值資料日期過舊或為未來日期")
    if count is not None and len(universe) < count:
        raise ValueError(f"有效普通股僅 {len(universe)} 檔，無法提供前 {count} 名")
    if missing:
        print(f"注意：{len(missing)} 檔缺少有效收盤價或股數，未納入市值排序：{', '.join(missing)}", flush=True)
    selected = universe.copy() if count is None else universe.head(count).copy()
    output = ROOT / "data" / ("all_listed_stocks.csv" if count is None else f"top{count}_market_cap.csv")
    output.parent.mkdir(exist_ok=True)
    selected.to_csv(output, index=False, encoding="utf-8-sig")
    scope = f"全部上市櫃普通股 {len(selected)} 檔" if count is None else f"上市櫃普通股前 {count} 名"
    print(f"名單日期 {as_of}，{scope}；市值單位為新台幣元。", flush=True)
    return selected
