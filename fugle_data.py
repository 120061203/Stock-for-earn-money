"""富果行情 REST API：歷史日線與即時報價。

https://developer.fugle.tw/docs/data/http-api/historical/candles/
日 K 成交量單位為股；未還原價格；不混入盤中未完成 K 棒。
"""

import json
import math
import os
from datetime import date
from pathlib import Path
from time import time

import pandas as pd
import requests
from dotenv import load_dotenv
from api_errors import ApiLimitError


ROOT = Path(__file__).resolve().parent
API_URL = "https://api.fugle.tw/marketdata/v1.0/stock"


class FugleClient:
    def __init__(self, api_key=None, cache_dir=None, refresh=False):
        load_dotenv(ROOT / ".env", override=False)
        key = (api_key if api_key is not None else os.environ.get("FUGLE_API_KEY", "")).strip()
        if not key:
            raise ValueError("尚未設定 FUGLE_API_KEY。請在專案 .env 填入富果行情 API Key，儲存後重跑；或使用 --provider finmind。")
        self.session = requests.Session()
        self.session.headers.update({"X-API-KEY": key, "User-Agent": "TaiwanStockScanner/1.0"})
        self.cache_dir = Path(cache_dir) if cache_dir else ROOT / ".cache" / "fugle"
        self.refresh = refresh

    def quote(self, symbol):
        """每次直接查詢 API，不讀取或寫入日線快取。"""
        if not symbol.isdigit() or not 1 <= len(symbol) <= 6:
            raise ValueError("富果查詢需使用純數字台股代號")
        try:
            response = self.session.get(f"{API_URL}/intraday/quote/{symbol}", timeout=(5, 20))
            errors = {401: "富果 API Key 無效或已過期", 403: "富果方案無即時報價權限", 429: "富果 API 已達速率或額度限制"}
            if response.status_code in errors:
                raise ApiLimitError(errors[response.status_code])
            if response.status_code == 404:
                raise ValueError(f"富果找不到 {symbol} 報價")
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.JSONDecodeError:
            raise ValueError("富果報價回應不是有效 JSON") from None
        except requests.RequestException:
            raise ValueError("富果即時報價連線失敗或逾時") from None
        if not isinstance(payload, dict) or payload.get("symbol") != symbol:
            raise ValueError("富果報價股票代號不符或格式錯誤")
        return payload

    def candles(self, symbol, start, end, include_ohlc=False):
        if not symbol.isdigit() or not 1 <= len(symbol) <= 6:
            raise ValueError("富果查詢需使用純數字台股代號")
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if not 0 <= (last - first).days <= 364:
            raise ValueError("富果單次查詢區間須小於一年（本程式最多 364 天）")
        prefix = "daily_ohlc_unadjusted" if include_ohlc else "daily_unadjusted"
        path = self.cache_dir / f"{prefix}_{symbol}_{start}_{end}.json"
        payload = None
        if not self.refresh and path.exists() and time() - path.stat().st_mtime < 21600:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        downloaded = payload is None
        if downloaded:
            print(f"  下載 Fugle 日 K：{symbol}…", flush=True)
            try:
                response = self.session.get(
                    f"{API_URL}/historical/candles/{symbol}",
                    params={"from": start, "to": end, "timeframe": "D", "adjusted": "false",
                            "fields": "open,high,low,close,volume" if include_ohlc else "close,volume", "sort": "asc"},
                    timeout=(5, 20),
                )
                errors = {
                    401: "富果 API Key 無效或已過期，請檢查 .env。",
                    403: "富果 API 權限不足，請確認行情方案可使用歷史日 K。",
                    404: "富果查詢區間沒有此股票的歷史日 K。",
                    429: "富果 API 已達速率或額度限制，請稍後再試。",
                }
                if response.status_code in errors:
                    if response.status_code in (401, 403, 429):
                        raise ApiLimitError(errors[response.status_code])
                    raise ValueError(errors[response.status_code])
                response.raise_for_status()
                payload = response.json()
            except requests.exceptions.JSONDecodeError:
                raise ValueError("富果回應不是有效 JSON。") from None
            except requests.RequestException:
                raise ValueError("富果連線失敗或逾時，請檢查網路與服務狀態。") from None
        frame = self.normalize(payload, symbol, first, last, include_ohlc=include_ohlc)
        if downloaded:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return frame

    @staticmethod
    def normalize(payload, symbol, first, last, include_ohlc=False):
        if not isinstance(payload, dict) or payload.get("symbol") != symbol or payload.get("timeframe") != "D":
            raise ValueError("富果回應的股票代號或 K 線週期不符。")
        if payload.get("adjusted") not in (None, False):
            raise ValueError("富果回應價格調整口徑不符，預期未還原價格。")
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise ValueError("富果沒有回傳日線資料。")
        frame = pd.DataFrame(data)
        if not {"date", "close", "volume"} <= set(frame.columns):
            raise ValueError("富果日線缺少 date、close 或 volume。")
        dates = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="raise")
        if dates.isna().any() or dates.duplicated().any():
            raise ValueError("富果日線日期缺漏或重複。")
        if not dates.between(pd.Timestamp(first), pd.Timestamp(last)).all():
            raise ValueError("富果回傳日期超出查詢區間。")
        for column in ("close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
            if not frame[column].map(lambda x: math.isfinite(x) and (x > 0 if column == "close" else x >= 0 and x == int(x))).all():
                raise ValueError(f"富果日線 {column} 數值無效。")
        frame["symbol"] = symbol
        columns = ["symbol", "date", "close", "volume"]
        if include_ohlc:
            for column in ("open", "high", "low"):
                if column not in frame:
                    raise ValueError("富果日線缺少完整 OHLC")
                frame[column] = pd.to_numeric(frame[column], errors="raise")
                if not frame[column].map(lambda x: math.isfinite(x) and x > 0).all():
                    raise ValueError("富果 OHLC 數值無效")
                columns.append(column)
            if ((frame.high < frame[["open", "close", "low"]].max(axis=1)) |
                    (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
                raise ValueError("富果 OHLC 高低價矛盾")
        return frame[columns].sort_values("date").reset_index(drop=True)
