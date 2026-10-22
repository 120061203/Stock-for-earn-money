"""掃描程式與本機App共用的安全錯誤訊息，不傳送原始Traceback或憑證。"""
import json

PREFIX = 'SCAN_ERROR_JSON '
MESSAGES = {
    'UNIVERSE_DOWNLOAD': '無法下載上市櫃股票名單，請確認執行環境可連線證交所與櫃買中心。',
    'UNIVERSE_INVALID': '上市櫃股票名單為空或欄位無效，請重新下載名單。',
    'DATA_DIRECTORY': '資料目錄無法讀寫，請確認 data/ 的寫入權限與部署儲存空間。',
    'MIS_TIMEOUT': '證交所 MIS 行情查詢逾時，請稍後重試。',
    'MIS_CONNECTION': '無法連線證交所 MIS 行情，請檢查執行環境的網路。',
    'MIS_HTTP': '證交所 MIS 拒絕或無法完成行情查詢。',
    'MIS_RESPONSE': '證交所 MIS 回傳無效或未成功的行情資料，未覆寫上次結果。',
    'NO_TODAY_QUOTES': '沒有今日有效成交價，可能尚未開盤、休市或資料來源尚未更新；已保留上次結果。',
    'DEPENDENCIES': '掃描缺少 Python 套件，請先安裝 requirements.txt 的相依套件。',
}


class ScanFailure(ValueError):
    def __init__(self, code, status=None):
        self.code, self.status = code, status
        super().__init__(failure_message(code, status))


def failure_message(code, status=None):
    message = MESSAGES.get(code, '掃描失敗，請查看執行環境的掃描紀錄。')
    if code == 'MIS_HTTP' and isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        message += f' HTTP {status}。'
    return message


def emit_failure(error):
    print(PREFIX + json.dumps(dict(code=error.code, status=error.status), ensure_ascii=False), flush=True)


def read_failure(path):
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if line.startswith(PREFIX):
            try:
                payload = json.loads(line[len(PREFIX):])
                if isinstance(payload, dict) and isinstance(payload.get('code'), str) and payload['code'] in MESSAGES:
                    return dict(error_code=payload['code'], message=failure_message(payload['code'], payload.get('status')))
            except (ValueError, TypeError):
                pass
    if any('ModuleNotFoundError:' in line for line in lines):
        return dict(error_code='DEPENDENCIES', message=MESSAGES['DEPENDENCIES'])
    return None
