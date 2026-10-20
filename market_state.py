"""以加權價格指數 TAIEX 日線判斷大盤，使用與股票相同的交易日期。"""

import math

import pandas as pd


def unknown_market(reason):
    return dict(taiex_close=None, taiex_ma60=None, market_ok=None, market_date="",
                market_source="FinMind/TAIEX", market_status=reason)


def market_context(taiex_close, taiex_ma60, market_date="", source="FinMind/TAIEX"):
    try:
        taiex_close, taiex_ma60 = float(taiex_close), float(taiex_ma60)
    except (TypeError, ValueError):
        return unknown_market("缺少大盤指標，暫停 Entry signal")
    if not all(math.isfinite(value) and value > 0 for value in (taiex_close, taiex_ma60)):
        return unknown_market("大盤指標無效，暫停 Entry signal")
    market_ok = taiex_close > taiex_ma60
    return dict(taiex_close=taiex_close, taiex_ma60=taiex_ma60, market_ok=market_ok,
                market_date=market_date, market_source=source,
                market_status="站上 MA60" if market_ok else "未站上 MA60")


def market_for_date(prices, price_date):
    """僅使用指定交易日及之前資料，不借用較晚日期的指數。"""
    frame = prices[["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="raise")
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")
    if frame.date.isna().any() or frame.date.duplicated().any():
        raise ValueError("加權指數日期缺漏或重複")
    if not frame.close.map(lambda x: math.isfinite(x) and x > 0).all():
        raise ValueError("加權指數收盤值無效")
    target = pd.Timestamp(price_date)
    frame = frame[frame.date <= target].sort_values("date")
    if frame.empty or frame.date.iloc[-1] != target:
        return unknown_market("缺少與股票同日的加權指數，暫停 Entry signal")
    if len(frame) < 60:
        return unknown_market("加權指數不足 60 筆，暫停 Entry signal")
    return market_context(frame.close.iloc[-1], frame.close.iloc[-60:].mean(), target.strftime("%Y-%m-%d"))
