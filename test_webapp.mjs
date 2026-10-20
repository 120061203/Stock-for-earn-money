import test from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULTS, numericInput, validParams, filterRows, exportCSV} from './webapp/view-model.mjs';

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
