"""pandas 技術指標教學。直接執行使用模擬資料；也可傳入日線 CSV。

日線欄位：symbol,date,close。每檔股票每天一筆，價格調整口徑須一致。
    .venv/bin/python technicals.py
    .venv/bin/python technicals.py daily_prices.csv
"""

import argparse
from pathlib import Path

try:
    import pandas as pd
except ModuleNotFoundError as exc:
    raise SystemExit("請先安裝 pandas：python -m pip install -r requirements.txt") from exc


def calculate_rsi(close, period=14):
    """Wilder RSI：首筆用 period 個漲跌的平均，再以 alpha=1/period 平滑。

    14 期 RSI 至少需要 15 筆收盤價。完全持平時本程式定義為 50。
    歷史不足維持 NaN，不把暖機期當成 0。
    """
    if not isinstance(period, int) or isinstance(period, bool) or period < 1:
        raise ValueError("RSI 期數必須是正整數")
    close = pd.to_numeric(close, errors="raise").astype(float)
    if close.isna().any() or not close.map(lambda x: 0 < x < float("inf")).all():
        raise ValueError("收盤價必須是有限的正數，不可缺值")
    # diff：每日價差；clip：把上漲與下跌分開。
    change = close.diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)
    result = pd.Series(float("nan"), index=close.index, name=f"rsi{period}")
    if len(close) <= period:
        return result

    def wilder_average(values):
        seeded = values.copy()
        seeded.iloc[:period] = float("nan")
        seeded.iloc[period] = values.iloc[1:period + 1].mean()
        return seeded.ewm(alpha=1 / period, adjust=False).mean()

    avg_gain = wilder_average(gain)
    avg_loss = wilder_average(loss)
    total = avg_gain + avg_loss
    # 等價於 100 - 100 / (1 + RS)，也可避免純上漲時除以零。
    result = 100 * avg_gain / total.where(total != 0)
    result = result.mask(total == 0, 50.0)
    return result.rename(f"rsi{period}")


def add_technicals(prices):
    """先依股票與日期排序，各股票獨立計算，禁止重複日期。"""
    missing = {"symbol", "date", "close"} - set(prices.columns)
    if missing:
        raise ValueError("日線缺少欄位：" + ", ".join(sorted(missing)))
    frame = prices.copy()
    if frame["symbol"].isna().any():
        raise ValueError("日線缺少股票代號")
    frame["symbol"] = frame["symbol"].astype(str).str.strip()
    if frame["symbol"].eq("").any():
        raise ValueError("日線缺少股票代號")
    frame["date"] = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="raise")
    if frame["date"].isna().any():
        raise ValueError("日線日期不可缺值")
    if frame.duplicated(["symbol", "date"]).any():
        raise ValueError("同一股票、日期有重複日線")
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")
    frame = frame.sort_values(["symbol", "date"]).reset_index(drop=True)
    grouped = frame.groupby("symbol")["close"]
    frame["ma20"] = grouped.transform(lambda close: close.rolling(20, min_periods=20).mean())
    frame["ma60"] = grouped.transform(lambda close: close.rolling(60, min_periods=60).mean())
    frame["rsi14"] = grouped.transform(calculate_rsi)
    # shift / diff 都在各股票內計算，前一期指前一筆交易日線。
    by_symbol = frame.groupby("symbol")
    frame["previous_rsi"] = by_symbol["rsi14"].shift(1)
    frame["ma60_slope"] = by_symbol["ma60"].diff()
    return frame


def latest_indicators(csv_path):
    prices = pd.read_csv(csv_path, dtype={"symbol": str}, encoding="utf-8-sig")
    return add_technicals(prices).groupby("symbol", sort=False).tail(1).set_index("symbol")


def demo_prices():
    # 教學用虛構價格與工作日日期；不是台股交易日曆或歷史行情。
    dates = pd.bdate_range("2025-01-01", periods=90)
    records = []
    for symbol, step in (("2330", 0.3), ("2317", -0.2), ("2454", 0.05)):
        for i, date in enumerate(dates):
            close = round(100 + step * i + (i % 7) * 0.8, 2)
            records.append({"symbol": symbol, "date": date, "close": close})
    return pd.DataFrame(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", nargs="?", type=Path)
    args = parser.parse_args()
    try:
        if args.csv_path:
            latest = latest_indicators(args.csv_path)
        else:
            print("以下全部是模擬資料，不代表股票實際指標。")
            latest = add_technicals(demo_prices()).groupby("symbol").tail(1).set_index("symbol")
        display = latest[["date", "close", "ma20", "ma60", "rsi14", "previous_rsi", "ma60_slope"]].copy()
        numeric = ["close", "ma20", "ma60", "rsi14", "previous_rsi", "ma60_slope"]
        display[numeric] = display[numeric].round(2)
        print(display.to_string())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
