import {DEFAULTS, numericInput, validParams, filterRows, exportCSV} from '/view-model.mjs';
const $ = (id) => document.getElementById(id);
const state = {data: {rows: [], summary: {}}, visible: [], market: 'all', sort: 'volume_ratio', direction: -1, busy: false, loading: true, error: '', notice: '', pollTimer: null, request: 0, controller: null};
const format = (value, digits = 2) => value == null ? '—' : Number(value).toLocaleString('zh-TW', {minimumFractionDigits: digits, maximumFractionDigits: digits});
function params() { return {min_change: numericInput($('minChange').value), max_change: numericInput($('maxChange').value), min_ratio: numericInput($('minRatio').value), days: numericInput($('averageDays').value)}; }
function message(text, error = false) { $('message').hidden = !text; $('message').textContent = text; $('message').classList.toggle('error', error); }
async function api(path, body, signal) {
  const response = await fetch(path, body === undefined ? {signal} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作失敗，請稍後再試。');
  return data;
}
function timeLabel(iso) {
  if (!iso) return '時間未知';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString('zh-TW', {timeZone: 'Asia/Taipei', hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}
async function loadData() {
  const id = ++state.request;
  state.controller?.abort();
  state.controller = new AbortController();
  state.loading = true; state.error = ''; render();
  try {
    const data = await api('/api/data?days=' + params().days, undefined, state.controller.signal);
    if (id === state.request) state.data = data;
  } catch (error) {
    if (id === state.request && error.name !== 'AbortError') state.error = '資料載入失敗：' + error.message;
  } finally {
    if (id === state.request) {state.loading = false; render();}
  }
}
function render() {
  const p = params(), data = state.data, meta = data.summary || {};
  for (const button of document.querySelectorAll('[data-sort]')) {
    const active = state.sort === button.dataset.sort;
    const arrow = button.querySelector('span');
    if (arrow) arrow.textContent = active ? (state.direction === 1 ? '↑' : '↓') : '↕';
    if (active) button.closest('th').setAttribute('aria-sort', state.direction === 1 ? 'ascending' : 'descending');
    else button.closest('th').removeAttribute('aria-sort');
  }
  const issues = [];
  if (!validParams(p)) issues.push('請填寫有效條件：漲幅 0%～10%、量能 0.1～50 倍，且漲幅下限不得超過上限。');
  if (!data.available && !state.loading) issues.push(data.message || '尚無資料，請更新行情。');
  if (data.available && meta.trade_date !== data.today) issues.push(`目前顯示 ${meta.trade_date} 的歷史掃描，尚未取得今天資料。`);
  if (data.available && (p.min_change < meta.min_change || p.max_change > meta.max_change)) issues.push(`已下載資料只涵蓋漲幅 ${meta.min_change}%～${meta.max_change}%；完整套用新範圍請按「更新行情」。`);
  message([state.error, state.notice, ...issues].filter(Boolean).join(' '), Boolean(state.error));
  const search = $('search').value.trim();
  const {matched, visible} = filterRows(state.loading || meta.volume_average_days !== p.days ? [] : data.rows || [], p, state.market, search, state.sort, state.direction);
  state.visible = visible;
  $('clearSearch').hidden = !search;
  $('resultsCard').setAttribute('aria-busy', String(state.loading));
  $('loadingState').hidden = !state.loading;
  $('tradeDate').textContent = meta.trade_date ? meta.trade_date.replaceAll('-', ' / ') : '尚無掃描資料';
  $('updatedAt').textContent = '資料更新 ' + timeLabel(meta.completed_at || meta.fetched_at) + ' · 台灣時間';
  $('coverage').textContent = data.available ? `已下載漲幅 ${meta.min_change}%～${meta.max_change}% 的個股量能 · ${meta.volume_average_days} 日均量 · 名單日期 ${(meta.universe_date || []).join('、') || '未知'}` : '目前尚無這個日均量期間的資料。';
  if (meta.fugle_status) $('coverage').textContent += ` · 富果補回行情 ${meta.fugle_recovered || 0} 檔、日均量 ${meta.fugle_history_recovered || 0} 檔`;
  $('matchedCount').textContent = state.loading ? '—' : visible.length;
  $('avgRatio').textContent = visible.length ? format(visible.reduce((sum, row) => sum + row.volume_ratio, 0) / visible.length) : '—';
  $('validCount').textContent = meta.valid_today == null ? '—' : format(meta.valid_today, 0);
  $('universeNote').textContent = meta.universe ? `掃描範圍 ${format(meta.universe, 0)} 檔上市櫃普通股` : '上市與上櫃普通股';
  const unknown = (meta.missing || 0) + (meta.volume_unknown || 0);
  $('unknownCount').textContent = data.available ? unknown : '—';
  $('unknownNote').textContent = data.available ? `行情缺漏 ${meta.missing || 0} · 量能未確認 ${meta.volume_unknown || 0}` : '缺報價或量能未完成檢查';
  $('resultPill').textContent = state.visible.length;
  $('resultDescription').textContent = validParams(p) ? `漲幅 ${p.min_change}%～${p.max_change}% · 量能 ≥ ${p.min_ratio} 倍 · 點選查看詳情` : '請先完整填寫篩選條件';
  $('tableCount').textContent = state.loading ? '正在載入對應期間資料…' : `顯示 ${state.visible.length} / ${matched.length} 檔符合個股`;
  $('download').disabled = state.loading || !state.visible.length;
  $('emptyState').hidden = state.loading || state.visible.length > 0;
  $('emptyText').textContent = search ? '沒有符合搜尋的個股，試著輸入其他代號或名稱。' : !data.available ? '請按「更新行情」，取得這個期間的量能資料。' : '試著調整篩選條件，或更新行情。未確認的量能不會列入結果。';
  const tbody = $('stockRows'); tbody.replaceChildren();
  for (const [index, row] of state.visible.entries()) {
    const tr = document.createElement('tr'); tr.tabIndex = 0; tr.setAttribute('aria-label', `查看 ${row.symbol} ${row.name} 詳情`);
    const cell = (text, className = '') => { const td = document.createElement('td'); td.textContent = text; td.className = className; tr.appendChild(td); return td; };
    cell(String(index + 1).padStart(2, '0'), 'index-col');
    const nameCell = cell(''); const code = document.createElement('span'); code.className = 'stock-code'; code.textContent = row.symbol;
    const name = document.createElement('span'); name.className = 'stock-name'; name.textContent = row.name; nameCell.append(code, name);
    const marketCell = cell(''); const badge = document.createElement('span'); badge.className = 'market-tag' + (row.market === '上櫃' ? ' otc' : ''); badge.textContent = row.market; marketCell.appendChild(badge);
    cell(format(row.price), 'numeric price-cell');
    const gainCell = cell('', 'numeric'); const gain = document.createElement('span'); gain.className = 'gain'; gain.textContent = '+' + format(row.change_percent) + '%'; gainCell.appendChild(gain);
    cell(format(row.today_volume_shares / 1000, 0), 'numeric volume-cell');
    cell(format(row.avg_daily_volume_shares / 1000, 1), 'numeric volume-cell');
    const ratioCell = cell('', 'numeric ratio-cell'); const ratio = document.createElement('span'); ratio.className = 'ratio-value'; ratio.textContent = format(row.volume_ratio) + '×';
    const meter = document.createElement('meter'); meter.className = 'ratio-meter'; meter.min = 0; meter.max = Math.max(10, p.min_ratio); meter.value = row.volume_ratio; meter.setAttribute('aria-label', `量能 ${format(row.volume_ratio)} 倍`); ratioCell.append(ratio, meter);
    cell(row.trade_time || '未知', 'time-cell'); cell('↗', 'row-arrow');
    tr.addEventListener('click', () => detail(row)); tr.addEventListener('keydown', event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); detail(row);}});
    tbody.appendChild(tr);
  }
}
function detail(row) {
  $('detailCode').textContent = `${row.symbol} / ${row.market}`;
  $('detailName').textContent = row.name;
  const content = $('detailContent'); content.replaceChildren();
  const grid = document.createElement('div'); grid.className = 'detail-grid';
  for (const [label, value] of [['成交價', format(row.price)], ['當日漲幅', '+' + format(row.change_percent) + '%'], ['今日累計量（張）', format(row.today_volume_shares / 1000, 0)], ['歷史日均量（張）', format(row.avg_daily_volume_shares / 1000, 1)], ['量能倍數', format(row.volume_ratio) + ' 倍'], ['最後成交', row.trade_time || '未知']]) {
    const item = document.createElement('div'); item.className = 'detail-item'; const title = document.createElement('span'); title.textContent = label; const valueNode = document.createElement('strong'); valueNode.textContent = value; item.append(title, valueNode); grid.append(item);
  }
  const note = document.createElement('p'); note.className = 'detail-note'; note.textContent = `成交日期 ${row.trade_date}。日均量期間 ${row.volume_average_start} ～ ${row.volume_average_end}，共 ${row.volume_average_days} 個交易日，不含今天。來源：${row.source || '未知'} / ${row.volume_history_source || '未知'}。這是量價條件篩選，尚未套用策略評分。`;
  content.append(grid, note); $('detailDialog').showModal();
}
function setBusy(busy) {
  state.busy = busy;
  for (const id of ['scan', 'minChange', 'maxChange', 'minRatio', 'averageDays', 'reset']) $(id).disabled = busy;
  $('scanText').textContent = busy ? '更新行情中…' : '更新行情並篩選';
  $('scanIcon').classList.toggle('spinning', busy); $('scanProgress').hidden = !busy;
}
async function poll() {
  clearTimeout(state.pollTimer);
  try {
    const job = await api('/api/state');
    if (job.status === 'running' && !state.busy && job.params) {
      $('minChange').value = job.params.min_change; $('maxChange').value = job.params.max_change;
      $('minRatio').value = job.params.min_ratio; $('averageDays').value = job.params.days;
    }
    setBusy(job.status === 'running');
    $('progressBar').value = job.progress; $('progressText').textContent = job.message;
    if (job.status === 'running') { state.pollTimer = setTimeout(poll, 1200); }
    else if (job.status === 'complete') { await loadData(); }
    else if (['error', 'cancelled'].includes(job.status)) {
      if (job.status === 'error') state.error = job.message; else state.notice = job.message;
      render();
    }
  } catch (error) { setBusy(false); state.error = '無法連線本機 App，請確認 app.py 仍在執行。'; render(); }
}
$('filters').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy || !$('filters').reportValidity()) return;
  const p = params(); if (p.min_change > p.max_change) return message('最低漲幅不可大於最高漲幅。', true);
  if (!validParams(p)) return;
  try {state.error = ''; state.notice = ''; setBusy(true); message(''); await api('/api/scan', p); poll();} catch (error) {setBusy(false); state.error = error.message; render();}
});
$('stopScan').addEventListener('click', async () => { try {await api('/api/scan/stop', {}); $('progressText').textContent = '正在停止更新…';} catch (error) {message(error.message, true);} });
for (const id of ['minChange', 'maxChange', 'minRatio', 'search']) $(id).addEventListener('input', render);
$('averageDays').addEventListener('change', loadData);
$('reset').addEventListener('click', () => {
  $('minChange').value = DEFAULTS.min_change; $('maxChange').value = DEFAULTS.max_change;
  $('minRatio').value = DEFAULTS.min_ratio; $('averageDays').value = DEFAULTS.days; $('search').value = '';
  state.market = 'all'; state.sort = 'volume_ratio'; state.direction = -1; state.notice = '';
  for (const button of document.querySelectorAll('[data-market]')) {const active = button.dataset.market === 'all'; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));}
  loadData();
});
$('clearSearch').addEventListener('click', () => {$('search').value = ''; $('search').focus(); render();});
for (const button of document.querySelectorAll('[data-market]')) button.addEventListener('click', () => {
  state.market = button.dataset.market; for (const other of document.querySelectorAll('[data-market]')) {const active = button === other; other.classList.toggle('active', active); other.setAttribute('aria-pressed', String(active));} render();
});
for (const button of document.querySelectorAll('[data-sort]')) button.addEventListener('click', () => {const key = button.dataset.sort; state.direction = state.sort === key ? state.direction * -1 : -1; state.sort = key; render();});
$('showMissing').addEventListener('click', () => {
  const content = $('missingContent'); content.replaceChildren();
  const status = document.createElement('p'); status.textContent = '富果：' + (state.data.summary.fugle_status || '尚未執行補查'); content.append(status);
  const rows = state.data.missing_rows || [];
  if (!rows.length) {const p = document.createElement('p'); p.textContent = '目前沒有待確認資料。'; content.append(p);}
  for (const row of rows) {const p = document.createElement('p'); p.textContent = `${row.symbol}：${row.reason}${row.fugle_error ? '；富果：' + row.fugle_error : ''}`; content.append(p);}
  $('missingDialog').showModal();
});
for (const id of ['helpNav', 'helpFooter']) $(id).addEventListener('click', () => $('helpDialog').showModal());
for (const button of document.querySelectorAll('.close-dialog')) button.addEventListener('click', () => button.closest('dialog').close());
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click', event => { if (event.target === dialog && (event.offsetX < 0 || event.offsetX > dialog.clientWidth || event.offsetY < 0 || event.offsetY > dialog.clientHeight)) dialog.close(); });
$('download').addEventListener('click', () => {
  if (!state.visible.length || state.loading) return;
  const p = params(); const csv = exportCSV(state.visible);
  const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'})); const link = document.createElement('a'); link.href = url;
  link.download = `台股量價_${state.data.summary.trade_date || '篩選結果'}_${p.min_change}-${p.max_change}pct_${p.min_ratio}x.csv`;
  document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
loadData(); poll();
