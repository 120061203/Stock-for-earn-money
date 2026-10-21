#!/bin/sh
# 先啟動伺服器把 port 打開，Cloudflare Container 冷啟動時才能儘快偵測到 port ready；
# 名單下載（data/all_listed_stocks.csv）改放到背景跑，避免卡住啟動導致平台判定逾時。
python app.py --host "${APP_HOST:-0.0.0.0}" --port "${PORT:-8765}" &
APP_PID=$!

(
  echo "下載上市櫃普通股名單…"
  attempt=1
  until python -c "from market_universe import top_market_cap; top_market_cap(count=None, refresh=True)"; do
    if [ "$attempt" -ge 3 ]; then
      echo "名單下載失敗 ${attempt} 次，略過；畫面上的『更新行情』在名單抓到之前會失敗，需重啟容器重試。"
      break
    fi
    echo "名單下載失敗，10 秒後重試（第 $((attempt + 1)) 次）…"
    attempt=$((attempt + 1))
    sleep 10
  done
) &

wait "$APP_PID"
