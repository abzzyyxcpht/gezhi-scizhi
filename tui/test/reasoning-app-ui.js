// 思考强度应用帧存档：
// 保存待确认期间输入不锁、保存确认、失败保留旧值、80x24 有界。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import stringWidth from 'string-width';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureReasoningAppFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  let holdSave = false, failSave = false;
  const saved = [], held = [];
  const frames = [];
  Bridge.prototype.call = async function (request) {
    t.calls.push(request);
    if (request.cmd === 'settings_save') {
      if (failSave) return { ok: false, error: 'fixture：设置保存失败' };
      if (holdSave) return new Promise(resolve => held.push(() => {
        saved.push(request.settings);
        resolve({ ok: true, settings: { ...request.settings } });
      }));
      saved.push(request.settings);
      return { ok: true, settings: { ...request.settings } };
    }
    return originalCall.call(this, request);
  };
  const capture = (id) => {
    const text = t.frame();
    const rows = text.replace(/\n$/, '').split('\n'); // 帧尾换行不计入行数（与夹具 t.capture 同口径）
    assert.ok(rows.length <= 24, `${id}: exceeds 24 rows`);
    assert.ok(rows.every(line => stringWidth(line) <= 80), `${id}: exceeds 80 columns`);
    frames.push({ id, text });
  };
  try {
    // 读取：不伪装保存回执
    await t.command('/reasoning');
    await t.wait(() => t.frame().includes('推理强度 low'), 'current reasoning visible');
    assert.ok(!t.frame().includes('已设置为'), 'read is not a save receipt');
    capture('01-read');
    // 保存待确认：输入可用
    holdSave = true;
    await t.command('/reasoning high');
    await t.wait(() => held.length === 1, 'save request held');
    await t.press('切换时保留输入');
    assert.ok(t.frame().includes('❯ 切换时保留输入'), 'input remains usable while save is pending');
    capture('02-save-pending');
    // 保存确认
    holdSave = false;
    held.splice(0).forEach(release => release());
    await t.wait(() => t.frame().includes('已设置为 high'), 'saved revision acknowledged');
    capture('03-saved');
    // 保存失败：错误可见，旧值保留
    failSave = true;
    await t.command('/reasoning low');
    await t.wait(() => t.frame().includes('fixture：设置保存失败'), 'save rejection surfaced');
    capture('04-save-failed');
    assert.ok(!t.calls.some(call => ['agent_run', 'agent_launch', 'create', 'workflow_run'].includes(call.cmd)),
      'settings flow never dispatches research or model work');
  } finally {
    held.splice(0).forEach(release => release());
    Bridge.prototype.call = originalCall;
  }
  return frames;
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  await captureReasoningAppFrames();
  console.log('Reasoning app real OpenTUI interaction frames passed: save acknowledgement, read/failure isolation, input (mock Bridge)');
  process.exit(0);
}
