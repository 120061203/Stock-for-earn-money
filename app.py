"""台股篩選本機 App：.venv/bin/python app.py，開啟 http://127.0.0.1:8765。

僅監聽本機；瀏覽器無法讀取 .env 或 API Key。Ctrl+C 停止伺服器。
"""

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import r2_store


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "webapp"
DATA = ROOT / "data"
TAIPEI = ZoneInfo("Asia/Taipei")
PATTERN = re.compile(r"^gainers_(\d{4}-\d{2}-\d{2})_([\d.]+)_([\d.]+)_vol([\d.]+)x_ma(\d+)\.checked\.csv$")
NUMBERS = {"price", "reference_price", "change_percent", "today_volume_shares", "avg_daily_volume_shares",
           "volume_ratio", "volume_average_days"}
# 部署在本機以外（例如容器）時，反向代理轉送的 Host/Origin 不會是 127.0.0.1／localhost，
# 需透過這兩個環境變數白名單實際的對外網域，預設為空集合＝維持原本僅限本機的行為。
ALLOWED_HOSTS = {h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
ALLOWED_ORIGINS = {o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}


def parameters(payload):
    result = {}
    for key, default in (("min_change", 3), ("max_change", 5), ("min_ratio", 2), ("days", 20)):
        value = payload.get(key, default)
        if isinstance(value, bool):
            raise ValueError("篩選參數必須是數值")
        try:
            result[key] = float(value)
        except (TypeError, ValueError):
            raise ValueError("篩選參數必須是數值") from None
        if not math.isfinite(result[key]):
            raise ValueError("篩選參數必須是有限數值")
    if not 0 <= result["min_change"] <= result["max_change"] <= 10:
        raise ValueError("漲幅範圍需介於 0%～10%，且下限不可大於上限")
    if not 0.1 <= result["min_ratio"] <= 50:
        raise ValueError("量能倍數需介於 0.1～50")
    if result["days"] != int(result["days"]) or not 1 <= result["days"] <= 120:
        raise ValueError("日均量需使用 1～120 個交易日")
    result["days"] = int(result["days"])
    return result


def load_snapshot(days=20, data_dir=DATA):
    candidates = []
    for path in data_dir.glob("gainers_*_vol*x_ma*.checked.csv"):
        match = PATTERN.match(path.name)
        if match and int(match[5]) == days:
            candidates.append((match[1], path.stat().st_mtime, path, match))
    if not candidates:
        return dict(rows=[], summary={}, available=False, today=datetime.now(TAIPEI).date().isoformat(),
                    message=f"尚未下載 {days} 日均量資料，請按「更新行情」。")
    _, _, path, match = max(candidates, key=lambda item: (item[0], item[1]))
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    for row in rows:
        for field in NUMBERS:
            try:
                value = float(row.get(field, ""))
                row[field] = value if math.isfinite(value) else None
            except (ValueError, TypeError):
                row[field] = None
        row["volume_pass"] = row.get("volume_pass") == "True"
    metadata = path.with_name(path.name.replace(".checked.csv", ".json"))
    summary = json.loads(metadata.read_text(encoding="utf-8")) if metadata.is_file() else {}
    summary.update(trade_date=match[1], min_change=float(match[2]), max_change=float(match[3]),
                   volume_average_days=int(match[5]))
    missing_path = data_dir / f"gainers_{match[1]}_{match[2]}_{match[3]}.missing.csv"
    missing_rows = []
    if missing_path.is_file():
        with missing_path.open(encoding="utf-8-sig", newline="") as source:
            missing_rows = list(csv.DictReader(source))
    missing_rows += [dict(symbol=row["symbol"], reason=row.get("volume_error") or "量能未確認")
                     for row in rows if row.get("volume_status") not in (None, "已檢查")]
    return dict(rows=rows, missing_rows=missing_rows, summary=summary, available=True, today=datetime.now(TAIPEI).date().isoformat())


class ScanJob:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.state = dict(status="idle", message="尚未開始更新", progress=0, params=None)
        self.cancelled = False
        self.log = ROOT / ".cache/app_scan.log"

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
        if result["status"] == "running" and self.log.exists():
            lines = self.log.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                strategy = re.search(r"策略([23]) \[(\d+)/(\d+)\]", line)
                if strategy:
                    number, i, total = map(int, strategy.groups())
                    result.update(progress=round(99 * i / max(1, total)), message=f"篩選股 {number}：{i} / {total} 檔")
                    break
                volume = re.search(r"量能 \[(\d+)/(\d+)\]", line)
                quotes = re.search(r"已查詢 (\d+)/(\d+) 檔", line)
                supplement = re.search(r"富果補查 \[(\d+)/(\d+)\]", line)
                if volume:
                    i, total = map(int, volume.groups())
                    result.update(progress=round(80 + 19 * i / max(1, total)), message=f"核對歷史量能 {i} / {total} 檔")
                    break
                if supplement:
                    i, total = map(int, supplement.groups())
                    result.update(progress=round(60 + 20 * i / max(1, total)), message=f"富果補查缺失 {i} / {total} 檔")
                    break
                if quotes:
                    i, total = map(int, quotes.groups())
                    result.update(progress=round(60 * i / max(1, total)), message=f"查詢上市櫃行情 {i} / {total} 檔")
                    break
        return result

    def start(self, params):
        with self.lock:
            if self.state["status"] == "running":
                raise RuntimeError("已有一筆掃描進行中，請等候完成或先停止。")
            self.cancelled = False
            self.state = dict(status="running", message="正在連線取得行情…", progress=1, params=params)
            self.log.parent.mkdir(exist_ok=True)
            try:
                with self.log.open("w", encoding="utf-8") as output:
                    if params.get("strategy") in {"strategy2", "strategy3"}:
                        command = [sys.executable, "-u", str(ROOT / (params["strategy"] + ".py")), "--scope", params["scope"]]
                        if params.get("live"):
                            command.append("--live")
                    else:
                        command = [sys.executable, "-u", str(ROOT / "filter_today_gainers.py"),
                                   "--min-change", str(params["min_change"]), "--max-change", str(params["max_change"]),
                                   "--min-volume-ratio", str(params["min_ratio"]), "--avg-volume-days", str(params["days"])]
                    self.process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
            except OSError:
                self.state.update(status="error", message="無法啟動掃描程式，請確認 Python 環境。")
                raise
            threading.Thread(target=self._finish, args=(self.process,), daemon=True).start()

    def _finish(self, process):
        code = process.wait()
        with self.lock:
            if self.cancelled:
                self.state.update(status="cancelled", message="已停止更新，保留上次完成的結果。", progress=0)
            elif code == 0:
                self.state.update(status="complete", message="更新完成", progress=100)
            else:
                self.state.update(status="error", message="本次更新失敗，已保留上次資料。請確認網路、API 額度或本機掃描紀錄。", progress=0)
            self.process = None
        if code == 0 and not self.cancelled:
            # 容器睡眠重啟後本機磁碟會清空，掃描結果另外備份到 R2 才能在下次醒來時復原。
            r2_store.sync_up_all(DATA)

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.cancelled = True
                self.process.terminate()
                self.state["message"] = "正在停止更新…"


JOB = ScanJob()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, value, status=200, content_type="application/json; charset=utf-8"):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode() if content_type.startswith("application/json") else value
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def valid_host(self):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"} | ALLOWED_HOSTS
        return self.headers.get("Host") in allowed

    def do_GET(self):
        if not self.valid_host():
            return self.respond(dict(error="僅允許本機存取"), 403)
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/data":
                days = int(parse_qs(parsed.query).get("days", [20])[0])
                if not 1 <= days <= 120:
                    raise ValueError("日均量天數無效")
                return self.respond(load_snapshot(days))
            if parsed.path in {"/api/strategy2", "/api/strategy3"}:
                scope = parse_qs(parsed.query).get("scope", ["top50"])[0]
                if not isinstance(scope, str) or scope not in {"top50", "all"}:
                    raise ValueError("掃描範圍無效")
                strategy = parsed.path.rsplit("/", 1)[-1]
                path = DATA / f"{strategy}_{scope}.json"
                result = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else dict(rows=[], missing_rows=[], summary={}, available=False)
                if strategy == "strategy3" and result.get("available"):
                    now = datetime.now(TAIPEI)
                    for row in result.get("rows", []):
                        if row.get("buy_signal"):
                            try:
                                age = (now - datetime.fromisoformat(row.get("trade_time", ""))).total_seconds()
                                fresh = 0 <= age <= 180
                            except (ValueError, TypeError):
                                fresh = False
                            if not fresh:
                                row.update(buy_signal=False, live_status="報價已過期")
                    result["summary"]["buy"] = sum(bool(row.get("buy_signal")) for row in result.get("rows", []))
                result["today"] = datetime.now(TAIPEI).date().isoformat()
                return self.respond(result)
            if parsed.path == "/api/state":
                return self.respond(JOB.snapshot())
            files = {"/": ("index.html", "text/html; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/strategy3": ("strategy3.html", "text/html; charset=utf-8"),
                     "/strategy3.js": ("strategy3.js", "text/javascript; charset=utf-8"),
                     "/strategy2": ("strategy2.html", "text/html; charset=utf-8"),
                     "/strategy2.js": ("strategy2.js", "text/javascript; charset=utf-8"),
                     "/view-model.mjs": ("view-model.mjs", "text/javascript; charset=utf-8")}
            if parsed.path in files:
                filename, mime = files[parsed.path]
                return self.respond((STATIC / filename).read_bytes(), content_type=mime)
            return self.respond(dict(error="找不到頁面"), 404)
        except (ValueError, OSError) as exc:
            return self.respond(dict(error="讀取資料失敗：" + str(exc)), 400)

    def read_body(self):
        # 反向代理（例如 Cloudflare container 的轉送）可能不帶 Content-Length，改用
        # chunked 傳輸；http.server 本身不會解 chunked，這裡手動解析，並維持原本
        # 2048 bytes 的上限。
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            body = bytearray()
            while True:
                line = self.rfile.readline(64)
                try:
                    size = int(line.split(b";")[0].strip(), 16)
                except ValueError:
                    raise ValueError("請求大小無效") from None
                if size == 0:
                    while self.rfile.readline(64) not in (b"\r\n", b"\n", b""):
                        pass
                    break
                if len(body) + size > 2048:
                    raise ValueError("請求大小無效")
                chunk = self.rfile.read(size)
                if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("請求大小無效")
                body += chunk
            return bytes(body)
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size <= 2048:
            raise ValueError("請求大小無效")
        return self.rfile.read(size)

    def do_POST(self):
        origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"} | ALLOWED_ORIGINS
        if not self.valid_host() or self.headers.get("Origin") not in origins:
            return self.respond(dict(error="僅接受本機 App 發出的操作"), 403)
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.respond(dict(error="需要 JSON"), 415)
        try:
            payload = json.loads(self.read_body())
            if not isinstance(payload, dict):
                raise ValueError("請求格式無效")
            if self.path in {"/api/strategy2/scan", "/api/strategy3/scan", "/api/strategy3/live"}:
                scope = payload.get("scope", "top50")
                if not isinstance(scope, str) or scope not in {"top50", "all"}:
                    raise ValueError("掃描範圍無效")
                strategy = self.path.split("/")[2]
                args = dict(strategy=strategy, scope=scope)
                if self.path.endswith("/live"):
                    args["live"] = True
                JOB.start(args)
                return self.respond(JOB.snapshot(), 202)
            if self.path == "/api/scan":
                JOB.start(parameters(payload))
                return self.respond(JOB.snapshot(), 202)
            if self.path == "/api/scan/stop":
                JOB.stop()
                return self.respond(JOB.snapshot())
            return self.respond(dict(error="找不到操作"), 404)
        except RuntimeError as exc:
            return self.respond(dict(error=str(exc)), 409)
        except (ValueError, OSError) as exc:
            return self.respond(dict(error=str(exc)), 400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port 需介於 1024～65535")
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        parser.exit(1, f"無法啟動：{exc}。若已在執行，直接開啟 http://{args.host}:{args.port}\n")
    print(f"台股篩選 App：http://{args.host}:{args.port}\n按 Ctrl+C 停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 App…", flush=True)
    finally:
        JOB.stop()
        server.server_close()


if __name__ == "__main__":
    main()
