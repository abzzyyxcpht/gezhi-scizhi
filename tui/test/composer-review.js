// Composer 独立回归（Ink composer-review 的 OpenTUI 移植）：bracketed 粘贴（整包/分片/ANSI）、
// 前向删除、同块文本后 Esc 清草稿、80x24 长中英+emoji 混合、ZWJ 字形 caret 不变量。
// 全部走隔离 fixture 与全桩 Bridge；提交后按 agent_launch 的 request 字段断言。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { insertText, editText } from '../src/composer.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureComposerReviewFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const calls = [];
  Bridge.prototype.call = async function (request) {
    calls.push(request);
    if (request.cmd === 'agent_launch') return { ok: true, result: { session_id: 'composer-session', status: 'done' } };
    return { ok: true };
  };
  const submitted = () => calls.filter(call => call.cmd === 'agent_launch');
  const reset = async () => { calls.length = 0; await t.press('\u0015'); }; // Ctrl+U：场景间清空草稿隔离
  async function scenario(name, chunks, expected) {
    await reset();
    for (const chunk of chunks) await t.press(chunk);
    assert.equal(submitted().length, 0, `${name}: nothing executes before explicit submit`);
    await t.press('\r');
    await t.wait(() => submitted().length === 1, `${name}: exactly one submission`);
    assert.equal(submitted()[0].request, expected, name);
  }
  async function pasteScenario(name, text, expected) {
    await reset();
    await t.paste(text);
    assert.equal(submitted().length, 0, `${name}: nothing executes before explicit submit`);
    await t.press('\r');
    await t.wait(() => submitted().length === 1, `${name}: exactly one submission`);
    assert.equal(submitted()[0].request, expected, name);
  }
  try {
    await pasteScenario('whole bracketed CRLF', 'first\r\nsecond', 'first\nsecond');
    await scenario('fragmented bracketed CRLF', ['\x1b[200~', 'first', '\r', '\n', 'second', '\x1b[201~'], 'first\nsecond');
    await pasteScenario('bracketed ANSI text', '\x1b[31mred\x1b[0m', 'red');
    await scenario('forward Delete', ['abc', '\x1b[D', '\x1b[3~'], 'ab');
    await scenario('same-chunk text then Escape', ['discard\x1b', 'replacement'], 'replacement');
    const longDraft = '测'.repeat(150) + '\n\t' + '🧪'.repeat(20);
    await reset();
    await t.paste(longDraft);
    assert.equal(submitted().length, 0, '80x24 long mixed draft: nothing executes before explicit submit');
    t.capture('composer-review-long', 80, 24);
    await t.press('\r');
    await t.wait(() => submitted().length === 1, '80x24 long mixed draft: exactly one submission');
    assert.equal(submitted()[0].request, longDraft, '80x24 long mixed draft');
    const joined = insertText({ text: '👩👧', caret: 2 }, '\u200d');
    const deleted = editText(joined, 'backspace');
    assert.ok([0, joined.text.length].includes(joined.caret), 'caret must be a boundary in the resulting 👩‍👧 cluster');
    assert.equal(deleted.text, '', 'backspace removes the whole ZWJ cluster atomically, never half a grapheme');
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureComposerReviewFrames();
  console.log(`${result.frames.length} composer review real OpenTUI frames: paste, forward delete, Escape clearing, long mixed draft and ZWJ caret passed`);
}
// opentui 渲染器保活计时器：直接运行时必须显式退出（被 capture 脚本 import 时不退出）
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) process.exit(0);
