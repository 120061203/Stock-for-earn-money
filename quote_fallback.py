"""富果補查：僅補缺報價或缺日均量，不用試撮／委買賣價填補實際成交價。"""

import math
from datetime import datetime
from decimal import Decimal
from time import monotonic, sleep
from zoneinfo import ZoneInfo

from api_errors import ApiLimitError


def fugle_quote(payload, today):
    if payload.get("date") != today:
        raise ValueError("富果沒有今天的報價，可能停牌或尚無交易")
    if payload.get("market") not in {"TSE", "OTC"}:
        raise ValueError("富果回傳非上市櫃普通股行情")
    trade = payload.get("lastTrade") or {}
    price = trade.get("price") if trade.get("price") is not None else payload.get("closePrice")
    reference = payload.get("referencePrice")
    try:
        price, reference = float(price), float(reference)
        if not all(math.isfinite(v) and v > 0 for v in (price, reference)):
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("富果沒有有效的實際成交價或參考價，不以試撮價補值") from None
    volume = (payload.get("total") or {}).get("tradeVolume")
    if volume is not None:
        volume = float(volume)
        if not math.isfinite(volume) or volume < 0:
            raise ValueError("富果成交量無效")
        volume *= 1000  # TSE / OTC 整股行情累計量為張，歷史日 K 為股。
    raw_time = trade.get("time") if trade.get("time") is not None else payload.get("closeTime")
    trade_time = "未知"
    if raw_time is not None:
        trade_time = datetime.fromtimestamp(float(raw_time) / 1_000_000, ZoneInfo("Asia/Taipei")).strftime("%H:%M:%S")
    return dict(symbol=payload["symbol"], name=payload.get("name", payload["symbol"]),
                market="上市" if payload["market"] == "TSE" else "上櫃", price=price,
                reference_price=reference, change_percent=float((Decimal(str(price)) / Decimal(str(reference)) - 1) * 100),
                trade_date=today, trade_time=trade_time, source="Fugle",
                today_volume_shares=volume, today_volume_source="Fugle total.tradeVolume (整股張數×1000)")


class FugleSupplement:
    def __init__(self, client=None, interval=1.1, max_calls=120):
        self.client = client
        self.interval, self.max_calls = interval, max_calls
        self.last_call, self.calls = None, 0
        self.recovered, self.history_recovered = 0, 0
        self.blocked, self.error = False, ""
        if self.client is None:
            try:
                from fugle_data import FugleClient
                self.client = FugleClient()
            except ValueError:
                self.blocked, self.error = True, "未設定有效 FUGLE_API_KEY"

    def call(self, method, *args):
        if self.blocked:
            raise ValueError(self.error or "富果補查已停止")
        if self.calls >= self.max_calls:
            self.blocked, self.error = True, "已達單次富果補查上限，請稍後再更新"
            raise ValueError(self.error)
        if self.last_call is not None:
            sleep(max(0, self.interval - (monotonic() - self.last_call)))
        self.calls += 1
        self.last_call = monotonic()
        try:
            return getattr(self.client, method)(*args)
        except ApiLimitError as exc:
            self.blocked, self.error = True, str(exc)
            raise

    def quote(self, symbol, today):
        return fugle_quote(self.call("quote", symbol), today)

    def history(self, symbol, start, end):
        # 歷史 API 單次需小於一年；本篩選器最多要求 120 個交易日。
        from datetime import date, timedelta
        start = max(date.fromisoformat(start), date.fromisoformat(end) - timedelta(days=364)).isoformat()
        frame = self.call("candles", symbol, start, end)
        return frame.rename(columns={"volume": "Trading_Volume"})[["date", "Trading_Volume"]].to_dict("records")

    def summary(self):
        return dict(fugle_calls=self.calls, fugle_recovered=self.recovered, fugle_history_recovered=self.history_recovered,
                    fugle_status=self.error if self.blocked else "已啟用，僅補查缺失資料")


def repair_quotes(valid, missing, today, supplement):
    quotes = {row["symbol"]: row for row in valid}
    errors = {row["symbol"]: dict(row) for row in missing}
    targets = list(errors) + [symbol for symbol, row in quotes.items() if row.get("today_volume_shares") is None and symbol not in errors]
    for index, symbol in enumerate(targets, start=1):
        print(f"富果補查 [{index}/{len(targets)}] {symbol}", flush=True)
        try:
            quote = supplement.quote(symbol, today)
            if quote["today_volume_shares"] is None:
                raise ValueError("富果也沒有有效累計成交量")
            quotes[symbol] = quote
            errors.pop(symbol, None)
            supplement.recovered += 1
        except (ValueError, KeyError, TypeError, OverflowError, OSError) as exc:
            if symbol in errors:
                errors[symbol]["fugle_error"] = str(exc)
            elif symbol in quotes:
                quotes[symbol]["fugle_error"] = str(exc)
    return list(quotes.values()), list(errors.values())
