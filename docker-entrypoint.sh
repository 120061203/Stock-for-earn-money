#!/bin/sh
# 上市櫃名單（data/all_listed_stocks.csv）只在這裡下載一次；畫面上的「更新行情」
# 只會重跑篩選，不會重抓名單。官方資料來源偶爾上市/上櫃日期未同步，故重試數次，
# 仍失敗則略過啟動伺服器，讓使用者至少能打開畫面，之後需重啟容器才能重新取得名單。
echo "下載上市櫃普通股名單…"
attempt=1
until python -c "from market_universe import top_market_cap; top_market_cap(count=None, refresh=True)"; do
  if [ "$attempt" -ge 3 ]; then
    echo "名單下載失敗 ${attempt} 次，略過；伺服器仍會啟動，但掃描會失敗，需重啟容器重試。"
    break
  fi
  echo "名單下載失敗，10 秒後重試（第 $((attempt + 1)) 次）…"
  attempt=$((attempt + 1))
  sleep 10
done

exec python app.py --host "${APP_HOST:-0.0.0.0}" --port "${PORT:-8765}"
