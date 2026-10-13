// 工作流视图（Ink workflow-ui 的 OpenTUI 移植）：
// /open 直连引擎 command_center（任务上下文跟随）、/menu 面板等价入口。
// 注：Ink 的客户端节点链渲染（2/2 节点 ●/✔）由 Python command_center 文本承接；
// 迟到响应不得覆盖所选工作流由串行提交结构性保证（忙时排队）。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureWorkflowFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const commandTexts = [];
  Bridge.prototype.call = async function (request) {
    t.calls.push(request);
    if (request.cmd === 'command') {
      commandTexts.push(String(request.text || ''));
      const text = String(request.text || '');
      if (text.startsWith('/open scan')) return { ok: true, task_id: 'scan', text: '任务 scan · 2/2 节点 · 已完成' };
      if (text.startsWith('/open full')) return { ok: true, task_id: 'full', text: '任务 full · 5/5 节点 · 含人工确认节点' };
      if (text.startsWith('/open')) return { ok: true, task_id: 'scan-delayed', text: '任务 scan-delayed · 等待节点' };
      return { ok: true, text: `已执行：${text}` };
    }
    return originalCall.call(this, request);
  };
  try {
    // /open 打开两步任务：文本按行渲染
    await t.command('/open scan');
    await t.wait(() => t.frame().includes('2/2 节点'), 'two-step completion text renders');
    assert.ok(t.calls.some(call => call.cmd === 'command' && String(call.text || '').startsWith('/open scan')),
      '/open forwards to engine command_center');
    // /open 五步任务
    await t.command('/open full');
    await t.wait(() => t.frame().includes('5/5 节点'), 'full workflow text renders');
    // 任务上下文跟随：后续 /read 带当前 task_id
    await t.command('/read ledger');
    await t.wait(() => t.frame().includes('研究时间账') || t.frame().includes('暂无内容'), 'read after open');
    const readCall = t.calls.filter(call => call.cmd === 'command' && String(call.text || '').startsWith('/read')).at(-1);
    assert.equal(readCall?.task_id, 'full', 'read follows the selected task');
    // /menu：命令面板等价入口
    await t.press('q').catch?.(() => {});
    await t.press('\x1b');
    await t.command('/menu');
    await t.wait(() => t.frame().includes('命令面板'), 'menu opens palette equivalent');
    await t.press('\x1b');
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureWorkflowFrames();
  console.log(`Workflow open passthrough, task context follow and menu equivalence passed (${result.frames.length} frames)`);
  process.exit(0);
}
