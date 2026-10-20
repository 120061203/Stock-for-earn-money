# 台股量價篩選 App

在本機啟動 Python 伺服器，透過瀏覽器篩選上市櫃普通股。預設條件為漲幅 3～5%、今日累計成交量至少為前 20 個交易日日均量的 2 倍。

## 安裝與啟動

需要 Python 3.10 以上。以下為 macOS / Linux 指令：

```bash
git clone https://github.com/LynnChen1013/Stock-for-earn-money.git
cd Stock-for-earn-money
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

在本機 `.env` 填入富果行情 `FUGLE_API_KEY`；`FINMIND_TOKEN` 可選填。未設定富果金鑰仍可使用證交所行情與 FinMind，富果補查會停用。

首次使用須先下載上市櫃普通股名單，再啟動 App：

```bash
python -c "from market_universe import top_market_cap; top_market_cap(count=None, refresh=True)"
python app.py
```

開啟 http://127.0.0.1:8765，按「更新行情並篩選」。第一次尚無掃描資料時畫面為空；資料會產生在本機 `data/`。股票名單不會隨每次掃描更新，可重新執行上面的名單下載指令。

停止伺服器請在終端機按 Ctrl+C。`webapp/app.js` 是瀏覽器模組，請透過本機伺服器開啟頁面。

## 資料與功能

- 行情優先使用 TWSE MIS，缺失時透過富果個股報價補查。
- 歷史成交量優先使用 FinMind，缺失時改查富果歷史日 K。
- 今日整股成交張數換算成股，與不含今天的前 N 個交易日日均成交股數比較；盤中不推估全日量。
- 支援市場、代號／名稱搜尋、欄位排序、個股詳情及下載目前顯示結果 CSV。
- 顯示缺失原因與實際資料來源。資料非今日、無有效成交價或日均量不足的個股不會列入符合結果。
- 富果補查會節流，單次掃描最多 120 次補查，遇權限或額度限制停止富果查詢。可用範圍依 API 方案而定。
- 伺服器僅監聽本機，不提供遠端存取。API 金鑰留在後端；`.env`、`.venv`、快取及行情資料均不納入 Git。

股票名單不含 ETF、特別股、權證及興櫃。量價條件篩選尚未套用其他選股評分策略。

## 驗證

```bash
python -m unittest -q test_app test_today_gainers test_quote_fallback test_fugle_data test_live_data test_technicals test_market_state test_market_universe
node --test test_webapp.mjs
```

Node.js 僅用於前端邏輯測試，啟動 App 不需要 Node.js。自動化測試涵蓋資料解析、來源補查、量能計算、篩選、排序與 CSV 匯出；瀏覽器視覺及點擊驗證尚未完成。
