// 科研时间线面板：验证阶段、计数和窄屏滚动。
// 覆盖：纯函数不变量（行模型/空洞/几何/光标/滚动 + ui-parts 零件）、控制器聚合、
// 80x28 集成分页与空洞展示、80x24 小终端不溢出。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  timelineRowModel, timelineGeometry, timelineCursorRow, timelineScrollFor,
  timelineCounts, timelineDetail, TIMELINE_STAGES,
} from '../src/opentui/components/research-timeline.js';
import { meterBar, sparkline, statusLamp, statusLabel, countLine, segmentBar, kbd, compactNumber, divider } from '../src/opentui/components/ui-parts.js';
import { createSessionController } from '../src/opentui/session-controller.js';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

// ---- 1) ui-parts 零件----
assert.equal(meterBar(0, 8), "········", "空计量条");
assert.equal(meterBar(1, 8), "████████", "满计量条");
assert.equal(meterBar(0.5, 8), "████····", "半格计量的 0.5 格语义");
assert.equal(sparkline([], 4), "····", "空迷你图给平线");
assert.equal(sparkline([1, 1, 1], 4), "▁▁▁", "等值不放大");
assert.equal(sparkline([0, 4], 4), "▁█", "序列落差");assert.equal(statusLamp("done"), "●");
assert.equal(statusLamp("failed"), "✕");
assert.equal(statusLamp("pending"), "○");
assert.equal(statusLabel("running"), "进行中");
assert.equal(countLine([{ n: 2, symbol: "✓" }, { n: 0, symbol: "✕" }]), "✓ 2", "0 缺席");
assert.equal(segmentBar([{ key: "done", n: 1 }], 3), "███", "满占比填满");
assert.equal(segmentBar([{ key: "done", n: 1 }, { key: "failed", n: 1 }], 4), "██▓▓", "两类各半");
assert.equal(segmentBar([], 3), "···", "无总量给空条");
assert.equal(kbd(["ctrl", "p"], { bracket: true }), "[Ctrl p]", "键帽：每键一颗 + 方括号");
assert.equal(compactNumber(8200), "8.2K", "紧凑数字 K（去 .0）");
assert.equal(compactNumber(68000), "68K");
assert.equal(compactNumber(500), "500", "<1k 原样");
assert.equal(compactNumber(5_600_000), "5.6M");
assert.equal(divider(4), "────", "通栏分隔线");
assert.equal(timelineDetail("hypotheses", 3), "3 条", "阶段明细带单位");

// ---- 2) timeline 行模型/几何/光标 ----
const stages = [
  { key: "goal", count: 1, status: "done", detail: "目标：复现基线实验", meter: 1 },
  { key: "hypotheses", count: 2, status: "done", detail: "2 条", meter: 0.25 },
  { key: "evidence", count: 0, status: "pending", detail: "", meter: 0 },
  { key: "facts", count: 5, status: "done", detail: "5 张", meter: 0.62 },
  { key: "experiments", count: 0, status: "pending", detail: "", meter: 0 },
  { key: "todos", count: 3, status: "done", detail: "3 项", meter: 0.37 },
];
const data = { goal: "复现基线实验", stages, counts: timelineCounts(stages) };
const rows = timelineRowModel(data);
assert.equal(rows.filter(r => r.kind === "header" || r.kind === "rail" || r.kind === "summary" || r.kind === "footer").length, 4, "家具行 4 行");
assert.equal(rows.filter(r => r.kind === "station" || r.kind === "hole").length, stages.length, "阶段行 == 阶段数");
assert.equal(rows.filter(r => r.kind === "hole").length, 2, "空洞阶段（无数据）");
assert.equal(rows.find(r => r.key === "stage:hypotheses").meter, 0.25, "计量值透传");
assert.ok(rows.find(r => r.key === "stage:evidence").hint.includes("检索"), "空洞提示指向证据来源");
assert.ok(rows.find(r => r.key === "stage:goal").hint.includes("/goal"), "立项空洞指向 /goal");

deepGeomCheck();
function deepGeomCheck() {
  for (const height of [18, 24, 28, 34, 60]) {
    const geom = timelineGeometry(height, data);
    assert.ok(geom.maxVisibleRows >= 4, `h=${height} 最小窗口`);
    assert.equal(geom.visibleRows, Math.min(geom.totalRows, geom.maxVisibleRows), `h=${height} 窗口钳制`);
    assert.equal(geom.panelRows, geom.visibleRows + 4 + (geom.overflows ? 2 : 0), `h=${height} 预算 == 渲染最坏行数`);
  }
  assert.equal(timelineGeometry(60, data).overflows, false, "充足终端全量可见");
  assert.equal(timelineGeometry(24, data).overflows, true, "24 行溢出（窗口化）");
}
assert.equal(timelineCursorRow(rows, 0), 2, "序号 0 → 首阶段行");
assert.equal(timelineCursorRow(rows, 5), 7, "序号 5 → 末阶段行");
assert.equal(timelineCursorRow(rows, 99), 7, "越界钳到末阶段行");
{
  let scroll = 0
  for (let ordinal = 0; ordinal < stages.length; ordinal++) {
    const row = timelineCursorRow(rows, ordinal)
    scroll = timelineScrollFor(rows, row, scroll, 4)
    assert.ok(row >= scroll && row < scroll + 4, `序号 ${ordinal} 光标可见`)
  }
  assert.equal(timelineScrollFor(rows, 7, 999, 4), rows.length - 4, "滚动越界钳到最大")
  assert.equal(timelineScrollFor(rows, 0, 999, 4), 0, "光标回顶时滚动跟着回顶")
}

// ---- 3) 控制器聚合（stub bridge）----
{
  const calls = [];
  const bridge = {
    connection: () => ({ mode: 'local', url: '' }),
    call: async (request) => {
      calls.push(request);
      if (request.cmd === 'notebook_list') return { ok: true, records: [
        { id: 'h1', title: '假设甲', supports: 3, challenges: 1, status: 'testing' },
        { id: 'h2', title: '假设乙', supports: 5, challenges: 0, status: 'supported' },
      ] };
      if (request.cmd === 'evidence_brief') return { ok: true, brief: { records: [
        { id: 'r1', counts: { supports: 6, challenges: 2, context: 4 } },
        { id: 'r2', counts: { supports: 1, challenges: 0, context: 2 } },
      ] } };
      if (request.cmd === 'world_fact_list') return { ok: true, facts: [{ id: 'f1' }, { id: 'f2' }, { id: 'f3' }] };
      if (request.cmd === 'todo_list') return { ok: true, todos: [{ id: 't1', title: '整理笔记', status: 'doing' }] };
      return { ok: true };
    },
  };
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const tl = await ctrl.timeline();
  const byKey = Object.fromEntries(tl.stages.map((s) => [s.key, s]));
  assert.equal(byKey.goal.status, "pending", "未立项 → 空洞");
  assert.equal(byKey.hypotheses.status, "done", "假设有数据 → 完成灯");
  assert.equal(byKey.hypotheses.count, 2);
  assert.equal(byKey.evidence.count, 15, "证据合计 = 支持9 + 背景6 + 反驳2");
  assert.ok(byKey.evidence.detail.includes("支持 7"), "证据明细三段");
  assert.equal(byKey.evidence.meter, 7 / 15, "计量 = 支持占比");
  assert.equal(byKey.facts.count, 3);
  assert.equal(byKey.todos.count, 1);
  assert.equal(tl.counts.find((c) => c.key === "done").n, 5, "计数行：5 阶段有数据（实验树同源 notebook_list）");
  assert.equal(tl.counts.find((c) => c.key === "pending").n, 1, "计数行：仅未立项为空洞");
}

// ---- 4) 集成：科研模式 80x28 打开 → 空洞展示 → ↓ 光标 → 小终端不溢出 ----
await upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  Bridge.prototype.call = async function (request) {
    if (request.cmd === 'notebook_list') return { ok: true, records: [
      { id: 'h1', title: '假设甲', supports: 3, challenges: 1, status: 'testing' },
      { id: 'h2', title: '假设乙', supports: 5, challenges: 0, status: 'supported' },
    ] };
    if (request.cmd === 'evidence_brief') return { ok: true, brief: { records: [{ id: 'r1', counts: { supports: 6, challenges: 2, context: 4 } }] } };
    if (request.cmd === 'world_fact_list') return { ok: true, facts: [{ id: 'f1' }, { id: 'f2' }] };
    if (request.cmd === 'todo_list') return { ok: true, todos: [{ id: 't1', title: '整理笔记', status: 'doing' }] };
    return originalCall.call(this, request);
  };
  try {
    t.resize(80, 28);
    await t.command('/timeline');
    await t.wait(() => t.frame().includes('科研时间线'), '/timeline opens in research mode');
    assert.ok(t.frame().includes('立项目标'), '阶段行：立项');
    assert.ok(t.frame().includes('假设台') && t.frame().includes('2 条'), '假设计数');
    assert.ok(t.frame().includes('支持 6'), '证据三段明细');
    assert.ok(t.frame().includes('┈'), '空洞阶段有虚记号');
    assert.ok(t.frame().includes('用 /goal'), '空洞提示指向 /goal');
    assert.ok(t.frame().includes('✓ 5') && t.frame().includes('○ 1'), '计数行（0 缺席）');
    t.capture('research-timeline-28', 80, 28);
    // ↓ 移动光标（选中态文字通道出现）
    for (let i = 0; i < 3; i++) await t.press('\x1b[B');
    assert.ok(t.frame().includes('进行中') || t.frame().includes('完成') || t.frame().includes('待定'), '选中行带文字状态');
    // 80x24 小终端：帧不溢出
    t.resize(80, 24);
    await t.press('');
    assert.ok(t.frame().replace(/\n$/, '').split('\n').length <= 24, '80x24 帧不溢出');
    assert.ok(t.frame().includes('科研时间线'), '小终端仍可见表头');
    t.capture('research-timeline-24', 80, 24);
    await t.press('\x1b');
    await t.wait(() => !t.frame().includes('PgUp/PgDn 翻页'), 'Esc 关闭');
  } finally {
    Bridge.prototype.call = originalCall;
  }
}, { startupMode: 'research' });

console.log('Research timeline: lamps/meters/holes windowed, controller aggregation, 80x28/80x24 frames');
process.exit(0);
