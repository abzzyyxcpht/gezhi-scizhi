// 工作台验收帧存档：
// 真渲染帧写入 design-previews/2026-09-17-harness-workbench/，宽度断言 80/100/140 无溢出。
// 所有帧来自 upgrade-ui-fixture 驱动的真实 OpenTUI runtime，内存 fixture，无 Python/网络/终端。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import stringWidth from 'string-width';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

const directory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../design-previews/2026-09-17-harness-workbench');

export const captureWorkbenchFrames = () => upgradeFixture(async t => {
  fs.mkdirSync(directory, { recursive: true });
  const originalCall = Bridge.prototype.call;
  let processes = [];
  const nodes = [{ id: 'lit_filter', name: '文献猎手' }, { id: 'human_review', name: '人工确认' },
    { id: 'extract', name: '综述搭子' }, { id: 'review_frame', name: '综述框架' }, { id: 'citation', name: '格式管家' }];
  const calls = [];
  Bridge.prototype.call = async function (request) {
    calls.push(request.cmd);
    if (request.cmd === 'list') return { ok: true, tasks: processes };
    if (request.cmd === 'workbench_status') return { ok: true, processes, loops: [] };
    if (request.cmd === 'workflows') return { ok: true, workflows: [{ id: 'research_pipeline', title: '完整科研流水线',
      description: '检索、证据复核、综述与引用', stages: nodes, requires_approval: true }] };
    return originalCall.call(this, request);
  };
  const capture = (filename, label, width) => {
    const text = t.frame();
    assert.ok(text.split('\n').every(line => stringWidth(line) <= width), `${filename}: overflow`);
    fs.writeFileSync(path.join(directory, `tui-${filename}`), `OpenTUI 渲染帧 / ACTUAL OPENTUI RENDER\n数据源：内存 mock fixture；无真实 API、联网、Loop 执行或终端启动。\n场景：${label}；终端宽度：${width} 列；动画关闭。\n\n${text}\n`, 'utf8');
  };
  try {
    capture('01-startup.txt', '启动工作台（空态）', 100);
    await t.command('/help');
    await t.wait(() => t.frame().includes('条命令'), 'help');
    capture('02-help.txt', '/help 命令表', 100);
    await t.press('\u0015');
    processes = [{ task_id: 'fixture-task-01', status: 'waiting_human', topic: '免疫细胞单细胞研究 · fixture', workflow_id: 'research_pipeline', offline: true }];
    t.resize(140, 30);
    await t.command('/tasks');
    await t.wait(() => t.frame().includes('fixture-task-01'), 'task listed');
    capture('03-wide-tasks.txt', '宽屏任务列表；待审批任务为 fixture', 140);
    t.resize(80, 24);
    await t.command('/tasks');
    await t.wait(() => t.frame().includes('fixture-task-01'), 'narrow task listed');
    capture('04-narrow-tasks.txt', '窄屏任务列表不溢出', 80);
    await t.command('/workflows');
    await t.wait(() => t.frame().includes('完整科研流水线'), 'workflows listed');
    capture('05-workflows.txt', '工作流目录（五节点）', 80);
    fs.writeFileSync(path.join(directory, 'tui-README.txt'), [
      '格致 TUI 工作台验收帧 · 2026-09-17（2026-10-12 起由 OpenTUI 引擎重新采集）',
      '所有 txt 中的界面均来自 upgrade-ui-fixture 驱动的真实 OpenTUI 渲染，不是手写布局。',
      'Bridge 生命周期与请求以进程内 fixture 替换；未开启 HTTP 服务、网络连接、Python、真实 Loop 或原生终端。',
      '03/04 的任务为明确标记 fixture，其余是空态与实际命令帮助。',
      '宽度断言：100 / 140 / 80 列均无溢出。',
      '复现：在 tui 目录运行 node test/capture-workbench.js',
      `观察到的只读命令：${[...new Set(calls)].join(', ')}`,
    ].join('\n') + '\n', 'utf8');
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureWorkbenchFrames();
  console.log(`Captured OpenTUI workbench frames: ${directory} (${result.frames.length} frames)`);
  process.exit(0);
}
