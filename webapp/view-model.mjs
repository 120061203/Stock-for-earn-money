export const DEFAULTS = Object.freeze({min_change: 3, max_change: 5, min_ratio: 2, days: 20});
export function numericInput(value) {
  return String(value).trim() === '' ? NaN : Number(value);
}
export function validParams(p) {
  return Object.values(p).every(Number.isFinite) && p.min_change >= 0 && p.max_change <= 10 &&
    p.min_change <= p.max_change && p.min_ratio >= 0.1 && p.min_ratio <= 50 && Number.isInteger(p.days) && p.days >= 1 && p.days <= 120;
}
export function filterRows(rows, p, market = 'all', search = '', sort = 'volume_ratio', direction = -1) {
  if (!validParams(p)) return {matched: [], visible: []};
  const matched = rows.filter(row => row.volume_status === '已檢查' && Number.isFinite(row.change_percent) &&
    row.change_percent >= p.min_change && row.change_percent <= p.max_change &&
    Number.isFinite(row.volume_ratio) && row.volume_ratio >= p.min_ratio);
  const term = search.trim().toLocaleLowerCase();
  const visible = matched.filter(row => (market === 'all' || row.market === market) &&
    (!term || `${row.symbol} ${row.name}`.toLocaleLowerCase().includes(term)));
  visible.sort((a, b) => {
    let compare;
    if (sort === 'symbol') compare = String(a.symbol).localeCompare(String(b.symbol));
    else {
      // 缺值永遠置底，切換升冪時也不把缺值排成第一名。
      const aValid = Number.isFinite(a[sort]), bValid = Number.isFinite(b[sort]);
      if (aValid !== bValid) return aValid ? -1 : 1;
      compare = aValid ? a[sort] - b[sort] : 0;
    }
    return compare * direction || String(a.symbol).localeCompare(String(b.symbol));
  });
  return {matched, visible};
}
export const CSV_FIELDS = ['symbol', 'name', 'market', 'price', 'change_percent', 'today_volume_shares', 'avg_daily_volume_shares', 'volume_ratio', 'trade_date', 'trade_time', 'volume_average_days', 'volume_average_start', 'volume_average_end', 'source', 'volume_history_source'];
export function exportCSV(rows, fields = CSV_FIELDS) {
  const escape = value => '"' + String(value ?? '').replaceAll('"', '""') + '"';
  return '\uFEFF' + [fields, ...rows.map(row => fields.map(field => row[field]))]
    .map(row => row.map(escape).join(',')).join('\r\n');
}

// 計時和掃描狀態分離，避免 API 回應慢或背景分頁恢復時重複送出。
export class AutoRefresh {
  constructor(run, {intervalMs = 60000, now = Date.now, onError = () => {}} = {}) {
    this.run = run; this.intervalMs = intervalMs; this.now = now; this.onError = onError;
    this.enabled = false; this.pending = false; this.due = 0;
  }
  enable() {this.enabled = true; this.due = this.now();}
  disable() {this.enabled = false;}
  defer() {this.due = this.now() + this.intervalMs;}
  seconds() {return Math.max(0, Math.ceil((this.due - this.now()) / 1000));}
  async tick({blocked = false, paused = false} = {}) {
    if (!this.enabled || this.pending || blocked || paused || this.now() < this.due) return false;
    this.pending = true;
    try {await this.run(); return true;}
    catch (error) {this.disable(); this.onError(error); return false;}
    finally {this.pending = false; this.defer();}
  }
}
