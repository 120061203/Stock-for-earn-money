import test from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULTS, numericInput, validParams, filterRows, exportCSV, AutoRefresh} from './webapp/view-model.mjs';

const rows = [
  {symbol:'2330', name:'台積電', market:'上市', price:100, change_percent:3, volume_ratio:2, volume_status:'已檢查'},
  {symbol:'5274', name:'信驊', market:'上櫃', price:200, change_percent:5, volume_ratio:4, volume_status:'已檢查'},
  {symbol:'2317', name:'鴻海', market:'上市', price:null, change_percent:4, volume_ratio:3, volume_status:'已檢查'},
  {symbol:'2454', name:'未確認', market:'上市', price:300, change_percent:4, volume_ratio:null, volume_status:'資料不足'},
];
test('inclusive thresholds and unverified volume exclusion', () => {
  const view = filterRows(rows, DEFAULTS);
  assert.deepEqual(view.visible.map(row=>row.symbol), ['5274','2317','2330']);
  assert.equal(filterRows(rows, {...DEFAULTS,min_ratio:3}).visible.length, 2);
});
test('search and market operate together', () => {
  assert.deepEqual(filterRows(rows, DEFAULTS, '上櫃', ' 信驊 ').visible.map(row=>row.symbol), ['5274']);
  assert.equal(filterRows(rows, DEFAULTS, '上市', '5274').visible.length, 0);
});
test('missing prices stay at the bottom in both directions', () => {
  assert.deepEqual(filterRows(rows, DEFAULTS, 'all', '', 'price', 1).visible.map(row=>row.symbol), ['2330','5274','2317']);
  assert.deepEqual(filterRows(rows, DEFAULTS, 'all', '', 'price', -1).visible.map(row=>row.symbol), ['5274','2330','2317']);
});
test('blank and invalid inputs cannot be interpreted as zero', () => {
  assert.ok(Number.isNaN(numericInput('')));
  assert.ok(Number.isNaN(numericInput('  ')));
  assert.equal(numericInput('0'),0);
  assert.equal(validParams({...DEFAULTS,min_ratio:NaN}), false);
  assert.equal(validParams({...DEFAULTS,min_change:6,max_change:5}), false);
  assert.deepEqual(filterRows(rows, {...DEFAULTS,min_ratio:NaN}).visible, []);
});
test('CSV preserves quotes, unicode and only current visible rows', () => {
  const csv = exportCSV([{...rows[0],name:'名稱,"測試"'}]);
  assert.ok(csv.startsWith('\uFEFF'));
  assert.ok(csv.includes('"名稱,""測試"""'));
  assert.ok(csv.includes('2330'));
  assert.ok(!csv.includes('5274'));
});
test('strategy 2 CSV includes the selected strategy fields and checks', () => {
  const csv = exportCSV([{symbol:'2330',buy:true,roe:6,rsi_rebound_ok:true}], ['symbol','buy','roe','rsi_rebound_ok']);
  assert.equal(csv, '\uFEFF"symbol","buy","roe","rsi_rebound_ok"\r\n"2330","true","6","true"');
});

test('automatic refresh prevents overlap and waits the full interval after completion', async () => {
  let now=0, calls=0, finish;
  const clock=new AutoRefresh(()=>{calls++;return new Promise(resolve=>{finish=resolve;});},{now:()=>now});
  clock.enable();
  const first=clock.tick();
  assert.equal(calls,1);
  await clock.tick();assert.equal(calls,1);
  now=90000;await clock.tick({blocked:true});assert.equal(calls,1);
  finish();await first;
  assert.equal(clock.seconds(),60);
  now=149999;await clock.tick();assert.equal(calls,1);
  now=150000;const next=clock.tick();assert.equal(calls,2);finish();await next;
});
test('automatic refresh pauses while hidden or scanning and stops on error', async () => {
  let calls=0, error;
  const clock=new AutoRefresh(async()=>{calls++;throw new Error('offline');},{onError:e=>{error=e.message;},now:()=>0});
  clock.enable();
  await clock.tick({paused:true});await clock.tick({blocked:true});assert.equal(calls,0);
  await clock.tick();assert.equal(calls,1);assert.equal(error,'offline');assert.equal(clock.enabled,false);
  await clock.tick();assert.equal(calls,1);
});
test('turning automatic refresh off cancels future scans', async () => {
  let calls=0;
  const clock=new AutoRefresh(async()=>{calls++;},{now:()=>0});
  clock.enable();clock.disable();await clock.tick();assert.equal(calls,0);
  clock.enable();clock.defer();assert.equal(clock.seconds(),60);await clock.tick();assert.equal(calls,0);
});
