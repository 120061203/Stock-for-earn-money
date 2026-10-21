import {exportCSV} from '/view-model.mjs';
const $ = id => document.getElementById(id);
const state = {rows: [], missing_rows: [], summary: {}, available: false, visible: [], busy: false, loading: false, request: 0, timer: null};
const number = (value, digits=2) => value == null ? '未查詢' : Number(value).toLocaleString('zh-TW', {maximumFractionDigits:digits});
const labels = {oversold:'超賣：乖離20 / 乖離60 / RSI 任一',technical_reversal:'反轉：低檔KD金叉 / 站上MA5或MA10 任一',price_stabilizing:'止跌：恐慌後量縮 / 長下影 / 低點抬高 任一',chip_improving:'籌碼：融資下降 / 外資轉買 / 投信轉買 任一'};
const scoreLabels = {score_bias20_oversold:'乖離20 ≤ −8%（2分）',score_bias60_oversold:'乖離60 ≤ −12%（1分）',score_rsi_oversold:'RSI ≤ 30（2分）',score_kd_golden_cross:'低檔KD黃金交叉（2分）',score_above_ma5:'收盤 > MA5（1分）',score_above_ma10:'收盤 > MA10（1分）',score_long_lower_shadow:'下影線 > 實體1.5倍（1分）',score_volume_shrink_after_panic:'前日爆量後當日量縮（1分）',score_higher_low:'最低價高於前日（1分）',score_margin_balance_down:'融資餘額較5交易日前下降（1分）',score_foreign_turn_buy:'外資由非買超轉買超（2分）',score_trust_turn_buy:'投信由非買超轉買超（2分）'};
const fields = ['symbol','name','score','score_complete','score_ceiling','candidate','status','price_date','previous_date','source','chip_source',...Object.keys(scoreLabels),...Object.keys(labels),'bias20','bias60','rsi14','k','d','prev_k','prev_d','close','open','low','ma5','ma10','ma20','ma60','body','lower_shadow','volume','prev_volume','avg_volume20','volume_ratio','prev_low','margin_compare_date','margin_balance_today','margin_balance_5d_ago','foreign_net_buy_today','foreign_net_buy_yesterday','trust_net_buy_today','trust_net_buy_yesterday','error'];
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const value = await response.json(); if (!response.ok) throw new Error(value.error || '操作失敗'); return value;
}
function message(text) { $('s2Message').hidden = !text; $('s2Message').textContent = text; }
function busy(value) {
  state.busy = value; $('s2Scan').disabled = value; $('s2Scope').disabled = value;
  $('s2Stop').hidden = !value; $('s2Progress').hidden = !value;
}
async function load() {
  const request = ++state.request;
  state.loading = true; state.rows = []; state.missing_rows = []; state.summary = {}; state.available = false; render();
  try {
    const data = await api('/api/strategy2?scope='+$('s2Scope').value);
    if (request !== state.request) return;
    if (data.available && data.summary.rules_version !== 2) {data.rows=[];data.available=false;data.summary={};data.message='策略已改版，請按「更新策略 2」重新計算17分策略。';}
    Object.assign(state, data); message(data.message || '');
    if (data.available && data.summary.target_date !== data.today) message(`目前結果的查詢截止日為 ${data.summary.target_date}。16:30 前使用前一日以前的已完成日線；請依各檔訊號日期判讀。`);
  } catch (error) {if (request === state.request) message(error.message);}
  finally {if (request === state.request) {state.loading = false; render();}}
}
function render() {
  const meta = state.summary;
  const term = $('s2Search').value.trim().toLocaleLowerCase();
  const status = $('s2Status').value;
  state.visible = state.rows.filter(row => (status === 'all' || row.status === status) && `${row.symbol} ${row.name}`.toLocaleLowerCase().includes(term))
    .sort((a,b) => (b.score ?? -1)-(a.score ?? -1) || b.volume_ratio-a.volume_ratio || String(a.symbol).localeCompare(String(b.symbol)));
  $('s2Date').textContent = meta.target_date ? `查詢截止 ${meta.target_date}` : '尚無掃描資料';
  $('s2Updated').textContent = meta.completed_at ? `更新 ${new Date(meta.completed_at).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false})}` : '使用已完成日 K';
  $('s2Buy').textContent = state.available ? meta.buy : '—'; $('s2Scanned').textContent = state.available ? meta.scanned : '—';
  $('s2Watch').textContent = state.available ? state.rows.filter(row=>row.oversold && !row.candidate).length : '—';
  $('s2Missing').textContent = state.available ? meta.missing : '—';
  $('s2Universe').textContent = meta.universe ? `${meta.universe} 檔 · 名單 ${(meta.universe_date || []).join('、') || '未知'}` : '選擇範圍後按更新';
  $('s2Count').textContent = `${state.visible.length} / ${state.rows.length} 檔`;
  $('s2Empty').hidden = state.visible.length > 0;
  $('s2Download').disabled = !state.visible.length || state.loading;
  const tbody = $('s2Rows'); tbody.replaceChildren();
  for (const row of state.visible) {
    const tr = document.createElement('tr'); tr.tabIndex=0; tr.setAttribute('aria-label',`查看 ${row.symbol} ${row.name} 條件`);
    for (const [index, value] of [`${row.symbol} ${row.name}`,row.score == null ? '待更新' : `${row.score} / 17${row.score_complete ? '' : '（部分）'}`,row.status,row.price_date,number(row.price),number(row.rsi14),`${number(row.volume_ratio)}×`,`${number(row.k)} / ${number(row.d)}`].entries()) {
      const td = document.createElement('td'); td.textContent = value; if(index === 1) td.className = 'score-cell'; if(index === 2) td.dataset.state = row.status === '候選' ? 'signal' : row.status.includes('待確認') ? 'pending' : row.status === '超賣觀察' ? 'watch' : 'muted'; tr.append(td);
    }
    tr.addEventListener('click',()=>detail(row)); tr.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();detail(row);}}); tbody.append(tr);
  }
  const errors = $('s2Errors'); errors.replaceChildren();
  for (const row of state.missing_rows) {const p=document.createElement('p');p.textContent=`${row.symbol}：${row.reason}`; errors.append(p);}
  if(!state.missing_rows.length) errors.textContent=state.available?'沒有資料缺失。':'尚未掃描。';
}
function detail(row) {
  $('s2DetailTitle').textContent=`${row.symbol} ${row.name} · ${row.status}`;
  const content = $('s2Detail'); content.replaceChildren();
  const note = document.createElement('p'); note.textContent=`訊號日 ${row.price_date}；前日 ${row.previous_date}；融資比較日 ${row.margin_compare_date}。日線 ${row.source}、籌碼 ${row.chip_source}。`; content.append(note);
  const title=document.createElement('h3');title.textContent=`已確認得分 ${row.score} / 17${row.score_complete ? '' : '（籌碼未齊，可能最高'+row.score_ceiling+'分）'}`;content.append(title);
  for (const [key,label] of Object.entries(scoreLabels)) {const p=document.createElement('p');p.textContent=`${row[key] == null ? '待確認' : '+'+row[key]} ${label}`;content.append(p);}
  const groupTitle=document.createElement('h3');groupTitle.textContent='反彈候選：四組均須通過';content.append(groupTitle);
  for (const [key,label] of Object.entries(labels)) {const p=document.createElement('p');p.textContent=`${row[key] == null ? '待確認' : row[key] ? '✓' : '✕'} ${label}`;content.append(p);}
  for (const [label,value] of [['乖離20 / 60',`${number(row.bias20)}% / ${number(row.bias60)}%`],['KD 前日 → 當日',`${number(row.prev_k)} / ${number(row.prev_d)} → ${number(row.k)} / ${number(row.d)}`],['收盤 / MA5 / MA10',`${number(row.close)} / ${number(row.ma5)} / ${number(row.ma10)}`],['下影線 / 實體',`${number(row.lower_shadow)} / ${number(row.body)}`],['當日量 / 前日量 / 20日均量',`${number(row.volume)} / ${number(row.prev_volume)} / ${number(row.avg_volume20)} 股`],['當日低點 / 前日低點',`${number(row.low)} / ${number(row.prev_low)}`],['融資餘額：5交易日前 → 當日',`${number(row.margin_balance_5d_ago)} → ${number(row.margin_balance_today)}`],['外資淨買超：前日 → 當日',`${number(row.foreign_net_buy_yesterday)} → ${number(row.foreign_net_buy_today)} 股`],['投信淨買超：前日 → 當日',`${number(row.trust_net_buy_yesterday)} → ${number(row.trust_net_buy_today)} 股`]]) {const p=document.createElement('p');p.textContent=`${label}：${value}`;content.append(p);}
  if(row.error) {const p=document.createElement('p');p.textContent=row.error;content.append(p);}
  $('s2Dialog').showModal();
}
async function poll() {
  clearTimeout(state.timer);
  try {
    const job = await api('/api/state');
    if (job.status === 'running' && job.params?.strategy === 'strategy2' && $('s2Scope').value !== job.params.scope) {
      $('s2Scope').value = job.params.scope; await load();
    }
    busy(job.status==='running');
    $('s2ProgressText').textContent=job.message; $('s2ProgressBar').value=job.progress;
    if(job.status==='running') {state.timer=setTimeout(poll,1200);return;}
    if(job.status==='complete' && job.params?.strategy==='strategy2') await load();
    else if(['error','cancelled'].includes(job.status)) message(job.message);
  } catch(error) {busy(false);message(error.message);}
}
$('s2Form').addEventListener('submit',async event=>{
  event.preventDefault(); if(state.busy)return;
  try {message('');busy(true);await api('/api/strategy2/scan',{scope:$('s2Scope').value});poll();}
  catch(error){busy(false);message(error.message);}
});
$('s2Stop').addEventListener('click',async()=>{try{await api('/api/scan/stop',{});}catch(error){message(error.message);}});
$('s2Scope').addEventListener('change',load); $('s2Search').addEventListener('input',render); $('s2Status').addEventListener('change',render);
$('s2Close').addEventListener('click',()=>$('s2Dialog').close());
$('s2Download').addEventListener('click',()=>{
  if(state.loading || !state.visible.length)return;
  const url=URL.createObjectURL(new Blob([exportCSV(state.visible,fields)],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=`篩選股2_${state.summary.target_date}_${$('s2Scope').value}.csv`; document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
load();poll();
