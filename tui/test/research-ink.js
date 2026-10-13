// 科研创新命令（Ink research-ink 的 OpenTUI 移植）：注册表 6 命令在位、
// /goal 生命周期、科研面板模式门禁、/novelty 与世界模型、/reproduce /peer-review 用法路径。
// 真引擎 round-trip 由 spike/e2e-research.js 覆盖；此处走夹具全桩。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { COMMANDS } from '../src/commands.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureResearchInkFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  Bridge.prototype.call = async function (request) {
    t.calls.push(request);
    if (request.cmd === 'workbench_mode') return { ok: true, mode: request.mode || 'research', agent: 'research' };
    if (request.cmd === 'world_fact_save') return { ok: true, fact: { id: 'f_1', claim: request.fact?.claim || '' } };
    if (request.cmd === 'workflow_run') return { ok: true, task_id: 'repro-1' };
    return originalCall.call(this, request);
  };
  try {
    // 1) 命令注册表：6 个科研创新命令在位
    for (const name of ['hypotheses', 'consensus', 'matrix', 'experiments', 'novelty', 'goal']) {
      assert.ok(COMMANDS.some((c) => c.name === name), `命令表缺少 /${name}`);
    }
    // 2) /goal 生命周期（runtime 本地分支）
    await t.command('/goal 复现论文实验并复现基线结果');
    await t.wait(() => t.frame().includes('目标已设置'), '/goal 设置');
    await t.command('/goal status');
    await t.wait(() => t.frame().includes('当前目标'), '/goal status');
    await t.command('/goal off');
    await t.wait(() => t.frame().includes('研究目标已清除'), '/goal off 清除');
    // 3) 科研分析面板：切科研模式后可用（空库降级提示）
    await t.press('\u0007'); // Ctrl+G → research
    await t.wait(() => t.frame().includes('◎ 科研'), 'research mode for panels');
    await t.command('/hypotheses');
    await t.wait(() => t.frame().includes('假设排行榜'), '/hypotheses panel opens');
    await t.press('\x1b');
    await t.command('/consensus');
    await t.wait(() => t.frame().includes('共识计量'), '/consensus panel opens');
    await t.press('\x1b');
    await t.command('/matrix');
    await t.wait(() => t.frame().includes('证据矩阵'), '/matrix panel opens');
    await t.press('\x1b');
    await t.command('/experiments');
    await t.wait(() => t.frame().includes('实验树'), '/experiments panel opens');
    await t.press('\x1b');
    // 4) 通用模式门禁：分析面板拒绝
    await t.press('\u0007'); // → general
    await t.command('/hypotheses');
    await t.wait(() => t.frame().includes('仅在科研模式'), 'insight gated to research mode');
    await t.press('\u0007'); // → research
    // 5) 世界模型：录入 + 面板
    await t.command('/fact add 退火温度与缺陷检出率正相关');
    await t.wait(() => t.frame().includes('事实已录入'), '/fact add');
    await t.command('/facts');
    await t.wait(() => t.frame().includes('世界模型'), '/facts panel');
    await t.press('\x1b');
    // 6) 用法路径：无参给用法
    await t.command('/novelty');
    await t.wait(() => t.frame().includes('用法：/novelty'), '/novelty usage');
    await t.command('/reproduce');
    await t.wait(() => t.frame().includes('用法：/reproduce'), '/reproduce usage');
    await t.command('/peer-review');
    await t.wait(() => t.frame().includes('用法：/peer-review'), '/peer-review usage');
    assert.ok(!t.calls.some(call => ['create', 'agent_run'].includes(call.cmd)), 'usage paths never dispatch work');
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureResearchInkFrames();
  console.log(`TUI 科研命令测试通过（OpenTUI 移植）：${result.frames.length} 帧`);
  process.exit(0);
}
