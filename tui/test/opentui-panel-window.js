// 面板窗口化（OpenTUI）：/config、/models、F1 快捷键 三个面板在小终端下按窗口渲染，
// 布局预算（panelRows）与实际渲染行数同源（geometry 单一推导），光标/滚动始终可见。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { configRowModel, configPanelGeometry, configScrollFor, flattenConfig } from '../src/opentui/components/config-panel.js';
import { modelPickerGeometry, modelPickerItemRows, modelPickerContentBudget, modelPickerRowModel } from '../src/opentui/components/model-picker.js';
import { shortcutsRowModel, shortcutsGeometry, shortcutsScrollFor } from '../src/opentui/components/shortcuts-panel.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

// ---- 1) 纯函数：config 行模型 / 几何 / 滚动窗口 ----
const rows = configRowModel();
assert.equal(rows.filter(r => r.kind === 'header').length, 6, 'config 模型、主题、代码、桌面字号、科研、实验能力六组');
assert.equal(rows.filter(r => r.kind === 'item').length, flattenConfig().length, '条目行数 == flatten 索引数');
rows.filter(r => r.kind === 'item').forEach((r, i) => assert.equal(r.itemIndex, i, 'itemIndex 与 flatten 对齐'));

for (const height of [18, 24, 28, 34, 40, 60]) {
  const geom = configPanelGeometry(height);
  assert.ok(geom.maxVisibleRows >= 4, `h=${height} 最小窗口`);
  assert.equal(geom.visibleRows, Math.min(geom.totalRows, geom.maxVisibleRows), `h=${height} 窗口钳制`);
  assert.equal(geom.panelRows, geom.visibleRows + 4 + (geom.overflows ? 2 : 0), `h=${height} 预算 == 渲染最坏行数`);
}
assert.equal(configPanelGeometry(24).overflows, true, '24 行溢出（窗口化）');
assert.equal(configPanelGeometry(24).panelRows, configPanelGeometry(24).visibleRows + 6, '溢出时含上下指示行');
assert.equal(configPanelGeometry(60).panelRows, rows.length + 4, '充足终端：全部设置加头尾和边框，无指示行');

// 光标恒在窗口内（顺序滚动，模拟真实按键链）
for (const height of [24, 28, 34]) {
  const geom = configPanelGeometry(height);
  let scroll = 0;
  for (let index = 0; index < flattenConfig().length; index++) {
    scroll = configScrollFor(rows, index, scroll, geom.maxVisibleRows);
    const cursorRow = rows.findIndex(r => r.kind === 'item' && r.itemIndex === index);
    assert.ok(cursorRow >= scroll && cursorRow < scroll + geom.maxVisibleRows, `h=${height} 第 ${index} 项可见`);
    assert.ok(scroll >= 0 && scroll <= Math.max(0, rows.length - geom.maxVisibleRows), '滚动起点钳制');
  }
}

// ---- 2) 纯函数：model-picker 窗口 ----
assert.deepEqual([18, 24, 28, 34, 60].map(modelPickerItemRows), [1, 4, 6, 6, 6], '候选窗口随高度收缩并封顶 6');
const mkPicker = (items, { error = '', index = 0 } = {}) => ({
  stage: 'planning', focus: 'model', loading: false, saving: false, effort: 'auto', error, index,
  catalog: {
    revision: 'r1', stage: 'planning', active_id: items[0]?.id || '',
    items: items.map((id, i) => ({ id, label: `模型${i}`, model: `x-${i}`, protocol: 'chat_completions', configured: true, efforts: [{ value: 'auto', enabled: true }] })),
  },
});
const many = mkPicker(Array.from({ length: 12 }, (_, i) => `m${i}`), { index: 8 });
for (const height of [18, 24, 34]) {
  const geom = modelPickerGeometry(height, many);
  assert.equal(geom.contentRows, modelPickerRowModel(many, geom.itemRows, { maxRows: geom.maxContentRows }).length, `h=${height} 预算与渲染同源`);
  assert.equal(geom.panelRows, geom.contentRows + 2, `h=${height} 含上下边框`);
  assert.ok(modelPickerRowModel(many, geom.itemRows, { maxRows: geom.maxContentRows }).some(r => r.text.includes('模型8')), `h=${height} 光标项在窗口内`);
}
const midRows = modelPickerRowModel(many, modelPickerItemRows(34), { maxRows: modelPickerContentBudget(34) });
assert.ok(midRows.some(r => r.text.includes('↑ 还有')) && midRows.some(r => r.text.includes('↓ 还有')), '双向溢出指示');
const errState = mkPicker(['a', 'b'], { error: '保存失败' });
const errGeom = modelPickerGeometry(24, errState);
assert.equal(errGeom.contentRows, modelPickerRowModel(errState, errGeom.itemRows, { maxRows: errGeom.maxContentRows }).length, '错误行计入预算');
assert.ok(errGeom.contentRows >= 8, '错误/页脚行优先保留');
assert.ok(modelPickerGeometry(18, mkPicker(['a', 'b'], { error: 'x' })).panelRows <= 10, '小终端选择器 ≤10 行（54x18 帧约束）');
// 预算吃紧时溢出指示让位（错误行/页脚优先）
const tightGeom = modelPickerGeometry(18, errState);
assert.ok(modelPickerRowModel(errState, tightGeom.itemRows, { maxRows: tightGeom.maxContentRows }).every(r => !r.text.includes('还有')), '紧预算丢弃溢出指示');

// ---- 3) 纯函数：shortcuts 窗口 ----
const sRows = shortcutsRowModel();
assert.equal(sRows.length, 30, '快捷键 30 行扁平模型');
assert.equal(sRows.filter(r => r.kind === 'header').length, 5, '5 个分组标题');
for (const height of [24, 28, 34, 40, 60]) {
  const geom = shortcutsGeometry(height);
  assert.ok(geom.maxVisibleRows >= 6, `h=${height} 最小窗口`);
  assert.equal(geom.visibleRows, Math.min(geom.totalRows, geom.maxVisibleRows), `h=${height} 窗口钳制`);
  assert.equal(geom.panelRows, geom.visibleRows + 4 + (geom.overflows ? 2 : 0), `h=${height} 预算 == 渲染最坏行数`);
}
assert.equal(shortcutsGeometry(60).overflows, false, '充足终端全量可见');
assert.equal(shortcutsScrollFor(-5, 30, 14), 0, '负滚动钳到顶');
assert.equal(shortcutsScrollFor(999, 30, 14), 16, '滚动钳到底');
assert.equal(shortcutsScrollFor(3, 30, 40), 0, '内容不足一屏不回滚');

// ---- 4) 集成：28 行终端下 /config 分页 + F1 滚动 ----
await upgradeFixture(async t => {
  t.resize(80, 28);
  await t.press('');
  await t.command('/config');
  await t.wait(() => t.frame().includes('配置 · Enter'), '/config opens at 80x28');
  assert.ok(t.frame().includes('默认模型'), '首窗口含模型组');

  // 跨过新增显示设置到实验能力组，窗口仍跟随光标。
  for (let i = 0; i < flattenConfig().findIndex(item => item.key === 'experimental_evidence_reviews'); i++) await t.press('\x1b[B');
  assert.ok(t.frame().includes('后台证据复核'), '分页后越窗条目可见');
  assert.ok(t.frame().includes('↑ 更多配置'), '顶部溢出指示');
  t.capture('config-window-28', 80, 28);

  // Enter 即切即存（bool 取反）
  await t.press('\r');
  await t.wait(() => t.frame().includes('后台证据复核 → true'), '即切即存');
  await t.press('\x1b');
  await t.wait(() => !t.frame().includes('配置 · Enter'), '/config Esc 关闭');

  // F1 快捷键面板：窗口化 + ↑↓ 滚动
  await t.press('\x1bOP');
  await t.wait(() => t.frame().includes('快捷键'), 'F1 opens');
  assert.ok(t.frame().includes('↓ 更多'), '28 行下溢出指示');
  for (let i = 0; i < 8; i++) await t.press('\x1b[B');
  assert.ok(t.frame().includes('↑ 更多'), '↓ 滚动后顶部指示');
  assert.ok(t.frame().includes('共 30 行'), '页脚计数');
  t.capture('shortcuts-window-28', 80, 28);
  await t.press('\x1b');
  await t.wait(() => !t.frame().includes('共 30 行'), 'F1 Esc 关闭');

  // /models 在 80x28 下窗口化（候选窗口 ≤6，面板不吞整屏）
  await t.command('/models');
  await t.wait(() => t.frame().includes('科研模型配置'), '/models opens at 28 rows');
  assert.ok(t.frame().includes('阶段 规划'), '阶段行在位');
  t.capture('model-picker-window-28', 80, 28);
  await t.press('\x1b');
});

console.log('Panel window: config/models/shortcuts windowed, geometry single-sourced, paging verified at 80x28');
process.exit(0);
