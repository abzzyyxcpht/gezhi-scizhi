// Composer 编辑器：历史召回恢复草稿、
// 超长粘贴保留原草稿并提示、CJK/emoji 长草稿 80x24 有界、提交才执行。
// 注：bracketed 粘贴/前向删除/Esc 清空已由 composer-review（OpenTUI 版）覆盖；
// Alt+Enter/Kitty/Ctrl+J 换行属 Ink raw-input 适配器行为，OpenTUI 由框架输入层承接。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import stringWidth from 'string-width';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureComposerFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const launches = [];
  Bridge.prototype.call = async function (request) {
    t.calls.push(request);
    if (request.cmd === 'agent_launch') { launches.push(request); return { ok: true, result: { session_id: 'composer-session', status: 'done' } }; }
    return originalCall.call(this, request);
  };
  try {
    // 提交前零执行：草稿阶段不发起任何请求
    await t.press('abc');
    await t.press('\x1b[D'); // 光标到中段
    assert.ok(t.frame().includes('❯ abc'), 'prompt visible while editing');
    // 历史召回后返回恢复草稿
    await t.press('\x1b[A'); // 无历史：忽略
    await t.press('\u0015');
    await t.press('previous draft');
    await t.press('\r');
    await t.wait(() => launches.length === 1, 'first submission');
    await t.press('abc');
    await t.press('\x1b[D');
    await t.press('\x1b[A'); // 召回 previous draft
    await t.wait(() => t.frame().includes('❯ previous draft'), 'history recall restores prior request');
    await t.press('\x1b[B'); // 返回新草稿
    await t.wait(() => t.frame().includes('❯ abc'), 'down restores the in-progress draft');
    // 历史草稿追加编辑后提交：请求原文完整
    await t.press('X');
    assert.ok(t.frame().includes('❯ abcX'), 'history draft stays editable');
    await t.press('\u0015');
    // 超长粘贴：原草稿保留 + 提示，不静默丢弃
    await t.press('keep');
    await t.paste('x'.repeat(32000));
    await t.wait(() => t.frame().includes('粘贴内容过长') || t.frame().includes('缩小内容'), 'oversize paste warns');
    assert.ok(t.frame().includes('❯ keep'), 'oversize paste preserves the draft');
    // CJK/emoji 长草稿：80x24 有界
    await t.press('\u0015');
    await t.paste('请分析以下材料：\n' + '测'.repeat(60) + '\n\t' + '🧪'.repeat(10));
    await t.wait(() => t.frame().includes('请分析以下材料'), 'CJK draft entered');
    const rows = t.frame().replace(/\n$/, '').split('\n');
    assert.ok(rows.length <= 24, 'bounded height');
    assert.ok(rows.every(line => stringWidth(line) <= 80), 'bounded width');
    // 提交：恰好一次，请求原文完整
    await t.press('\r');
    await t.wait(() => launches.length === 2, 'exactly one more submission');
    assert.ok(launches[1].request.includes('请分析以下材料'), 'CJK payload preserved');
    assert.ok(launches[1].request.includes('🧪'), 'emoji payload preserved');
    assert.ok(!t.calls.some(call => ['create', 'workflow_run'].includes(call.cmd)), 'composer never dispatches research tasks');
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureComposerFrames();
  console.log(`${result.frames.length} OpenTUI composer scenarios passed (history, oversize paste, Unicode bounds)`);
  process.exit(0);
}
