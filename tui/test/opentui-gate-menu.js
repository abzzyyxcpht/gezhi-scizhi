// 人工确认关口菜单 + 回看锚定（OpenTUI，upgradeFixture 驱动）：
// /open 命中 waiting_human → 编号菜单弹出（数字直选执行 REVIEW_MENU 映射命令）→
// Esc 收起后可重新 /open 呼出；回看中流式新行到达视口不漂移（ZCode 行为）。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureGateMenuFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  let taskStatus = 'waiting_human';
  let streamPage = 0;
  let streamFinished = false;
  let releaseNextPage;
  Bridge.prototype.call = async function (request) {
    t.calls.push(request); // 包装层也要记账：夹具只在 originalCall 里 push
    if (request.cmd === 'command') {
      const text = String(request.text || '');
      if (text.startsWith('/open')) return { ok: true, task_id: 'gate-task', text: '任务 gate-task · 已打开' };
      if (text === '/confirm') return { ok: true, text: '已放行，继续后续节点' };
      if (text === '/pool 10') return { ok: true, text: '文献池复核（前 10 篇）' };
      if (text === '/pause') return { ok: true, text: '已就地暂停' };
      return { ok: true, text: `已执行：${text}` };
    }
    if (request.cmd === 'poll' && request.task_id === 'gate-task') return { ok: true, status: taskStatus };
    if (request.cmd === 'agent_launch') return { ok: true, result: { session_id: 'gate-session', status: 'running', launch_pending: true } };
    if (request.cmd === 'agent_launch_status') return { ok: true, launch_pending: !streamFinished,
      finished: streamFinished, snapshot: { session_id: 'gate-session', status: streamFinished ? 'done' : 'running' }, result: null };
    if (request.cmd === 'session_snapshot') return { ok: true, snapshot: { session_id: request.session_id, status: 'done' } };
    if (request.cmd === 'session_events') {
      // 游标分页只返回新事件；第二页在回看后释放，模拟同一轮的后台流式输出。
      const events = (start, count) => Array.from({ length: count }, (_, i) => ({
        kind: 'tool_call', payload: { name: `tool-${String(start + i).padStart(2, '0')}` },
      }));
      streamPage++;
      if (streamPage === 1) return { ok: true, events: events(1, 12), cursor: 'gate-1', reset: false, has_more: true };
      if (streamPage === 2) return new Promise(resolve => {
        releaseNextPage = () => {
          streamFinished = true;
          resolve({ ok: true, events: events(13, 4), cursor: 'gate-2', reset: false, has_more: false });
        };
      });
      return { ok: true, events: [], cursor: 'gate-2', reset: false, has_more: false };
    }
    return originalCall.call(this, request);
  };
  try {
    // 1) /open 命中关口：菜单弹出，四个选项 + 提示行，composer 仍可达
    await t.command('/open gate-task');
    await t.wait(() => t.frame().includes('待人工确认'), 'gate menu opens on waiting_human');
    assert.ok(t.frame().includes('确认放行，继续后续节点'), 'option 1 rendered');
    assert.ok(t.frame().includes('展开文献池复核'), 'option 3 rendered');
    assert.ok(t.frame().includes('数字键直选'), 'hint row rendered');
    t.capture('gate-menu-open');
    // 2) 数字直选 3 → /pool 10 经引擎命令分发执行，菜单关闭
    await t.press('3');
    await t.wait(() => t.calls.some(call => call.cmd === 'command' && call.text === '/pool 10'),
      'digit 3 runs the mapped command');
    await t.wait(() => !t.frame().includes('待人工确认'), 'menu closes after selection');
    assert.ok(t.frame().includes('文献池复核'), 'command output rendered');
    // 3) 重新 /open 可再次呼出；Esc 收起；再次 /open 再次呼出（显式重开语义）
    await t.command('/open gate-task');
    await t.wait(() => t.frame().includes('待人工确认'), 'menu re-summonable');
    await t.press('\x1b');
    await t.wait(() => !t.frame().includes('待人工确认'), 'Esc dismisses');
    await t.command('/open gate-task');
    await t.wait(() => t.frame().includes('待人工确认'), 'menu re-opens after dismiss');
    // 4) Enter 确认当前选中项（默认第 1 项 → /confirm）
    await t.press('\r');
    await t.wait(() => t.calls.some(call => call.cmd === 'command' && call.text === '/confirm'),
      'Enter confirms the selected option');
    // 5) 回合中回看锚定：新事件页到达后，偏移补偿且原历史行保持可见。
    await t.command('第一问');
    await t.wait(() => t.frame().includes('tool-12') && releaseNextPage, 'first stream page fills the transcript');
    await t.scrollWheel('up');
    await t.wait(() => /↑ 回看 [1-9]/.test(t.frame()), 'wheel scrollback visible');
    const offsetOf = frame => Number((frame.match(/↑ 回看 (\d+) 行/) || [])[1] || 0);
    const visibleTools = frame => frame.split('\n').filter(line => /⚙ tool-\d+/.test(line)).map(line => line.trim());
    const before = offsetOf(t.frame());
    const visibleBefore = visibleTools(t.frame());
    releaseNextPage();
    await t.wait(() => offsetOf(t.frame()) > before, 'anchor compensates for streamed lines');
    assert.deepEqual(visibleTools(t.frame()), visibleBefore, 'same historical rows remain visible while new events arrive');
    t.capture('streaming-scrollback-anchored');
    // 用户主动提交命令应回到底部；它不是后台事件，不能用来验证回看锚定。
    await t.wait(() => t.calls.some(call => call.cmd === 'session_snapshot' && call.session_id === 'gate-session'), 'stream finishes');
    await t.command('/pool 10');
    await t.wait(() => offsetOf(t.frame()) === 0, 'submit auto-scrolls to bottom');
  } finally {
    releaseNextPage?.();
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  try {
    const result = await captureGateMenuFrames();
    console.log(`Gate menu + scroll anchor: ${result.frames.length} frames passed`);
    process.exit(0);
  } catch (error) {
    console.error(error);
    process.exit(1);
  }
}
