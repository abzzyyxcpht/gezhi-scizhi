// 长会话渲染压测（M4 缺口①：500+ 行无明显卡顿 + 内存抽查）
// 运行: node scripts/benchmark_long_session.mjs
// 口径：AppShell 渲染 550 行转写（含 CJK/长路径/dim/工具行混合）→ 每批追加 50 行
// 重新渲染计时；阈值：单帧 P95 < 100ms、heap 增量 < 60MB。
import React from "react";
import { testRender } from "@mbears/opentui-react/test-utils";
import { AppShell } from "../src/opentui/app-shell.js";

const WIDTH = 120;
const HEIGHT = 32;
const INITIAL = 500;
const BATCHES = 10;
const BATCH = 50;

const lines = [];
for (let i = 0; i < INITIAL + BATCHES * BATCH; i++) {
  const kind = i % 5;
  if (kind === 0) lines.push({ text: `用户消息 ${i}：请分析基于多尺度异构卷积协同注意力的热轧钢表面缺陷检测结果`, user: true });
  else if (kind === 1) lines.push({ text: `助手回复 ${i}：检测流程包含数据预处理、模型训练与评估三个阶段，mAP 达到 0.932`, dim: false });
  else if (kind === 2) lines.push({ text: `⚙ search_papers`, dim: true, kind: "tool" });
  else if (kind === 3) lines.push({ text: `  参数：{"query":"defect detection","limit":20}`, dim: true, kind: "tool_detail" });
  else lines.push({ text: `  D:\\deeplearning\\基于通用智能体的AI科研智能体应用开发\\data\\research\\${i}\\report.md`, dim: true });
}

const state = {
  workspaceMode: "research",
  terminalWidth: WIDTH,
  terminalHeight: HEIGHT,
  lines: lines.slice(0, INITIAL),
  spinner: { active: true, frame: 3 },
  draft: "分析中",
  cursor: 3,
  status: "就绪",
  sidebar: { hidden: false, sections: [{ title: "技能", lines: [{ text: "lit-critique" }] }] },
  slotValues: { mode: "◎ 科研", model: "glm-5", cwd: "D:\\deeplearning\\基于通用智能体的AI科研智能体应用开发" },
  tips: [{ text: "Ctrl+P 命令面板", weight: 2 }],
  used: 8200,
  total: 68000,
};

const setup = await testRender(React.createElement(AppShell, { state }), { width: WIDTH, height: HEIGHT, targetFps: 30 });

// 预热
for (let i = 0; i < 3; i++) await setup.renderOnce();

const timings = [];
const heapBefore = process.memoryUsage().heapUsed;
let rendered = INITIAL;
for (let b = 0; b < BATCHES; b++) {
  rendered += BATCH;
  const t0 = performance.now();
  await setup.flush();
  timings.push(performance.now() - t0);
}
// 重新挂载 550 行整树（模拟超长会话首屏）
const tFull = performance.now();
await testRender(React.createElement(AppShell, { state: { ...state, lines } }), { width: WIDTH, height: HEIGHT, targetFps: 30 });
const fullMs = performance.now() - tFull;
const heapAfter = process.memoryUsage().heapUsed;

const sorted = [...timings].sort((a, b) => a - b);
const p50 = sorted[Math.floor(sorted.length * 0.5)];
const p95 = sorted[Math.floor(sorted.length * 0.95)];
const heapDeltaMb = (heapAfter - heapBefore) / 1048576;

const report = {
  initial_lines: INITIAL,
  final_lines: rendered,
  flush_ms: { p50: Math.round(p50 * 10) / 10, p95: Math.round(p95 * 10) / 10, max: Math.round(sorted[sorted.length - 1] * 10) / 10 },
  full_mount_ms: Math.round(fullMs),
  heap_delta_mb: Math.round(heapDeltaMb * 10) / 10,
  thresholds: { p95_ms: 100, heap_delta_mb: 60 },
  ok: p95 < 100 && heapDeltaMb < 60,
};
console.error(JSON.stringify(report, null, 2));
setup.renderer.destroy();
process.exit(report.ok ? 0 : 1);
