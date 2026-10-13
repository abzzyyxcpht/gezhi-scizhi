import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureModelPickerFrames = () => upgradeFixture(async t => {
  await t.command('/models');
  await t.wait(() => t.frame().includes('科研模型配置'), '/models opens shared catalog');
  assert.ok(t.frame().includes('planning') && t.frame().includes('默认连接'));
  assert.ok(t.frame().includes('适配器') && t.frame().includes('未验证'));
  assert.ok(!t.frame().includes('模型配置已保存'), 'opening the picker is not a save acknowledgement');
  await t.press('原草稿'); await t.press('\x1b[B');
  assert.ok(t.frame().includes('profile-model'), 'down chooses independent profile');
  t.hold = 'model_control_apply'; await t.press('\r'); await t.press('\r');
  await t.wait(() => t.held.length >= 1, 'save is held');
  assert.equal(t.held.length, 1, 'double Enter is coalesced into one apply request');
  const save = t.held[0];
  assert.deepEqual(save.request, { cmd: 'model_control_apply', stage: 'planning', profile_id: 'lab',
    expected_revision: 'revision-1' });
  await t.press('继续输入'); assert.ok(t.frame().includes('❯ 原草稿继续输入'));
  t.capture('model-save-pending');
  save.resolve(save.answer()); t.held = []; t.hold = '';
  await t.wait(() => !t.frame().includes('科研模型配置') && t.frame().includes('模型配置已保存'), 'ack closes the picker');
  assert.ok(t.frame().includes('❯ 原草稿继续输入')); t.capture('model-save-ack');

  await t.command('/model'); await t.wait(() => t.frame().includes('科研模型配置'), '/model no args opens');
  t.fail = true; await t.press('\r');
  await t.wait(() => t.frame().includes('fixture：模型配置已变化'), 'CAS failure displayed');
  assert.ok(t.frame().includes('科研模型配置')); t.capture('model-stale-failed');
  await t.press('\x1b'); t.fail = false;

  // Read completion cannot resurrect a picker that the user has closed.
  t.hold = 'model_control_catalog'; await t.command('/models');
  await t.wait(() => t.held.length === 1, 'catalog held');
  await t.press('\x1b'); const pending = t.held.shift(); pending.resolve(pending.answer()); t.hold = '';
  await t.press('读取取消后草稿');
  assert.ok(!t.frame().includes('科研模型配置') && t.frame().includes('读取取消后草稿'));

  await t.command('/models'); await t.wait(() => t.frame().includes('科研模型配置'), 'reopen');
  await t.press('\t'); await t.press('\x1b[B');
  await t.wait(() => t.calls.some(call => call.cmd === 'model_control_catalog' && call.stage === 'synthesis'), 'Tab selects stage');
  await t.press('\t'); await t.press('\x1b[B');
  assert.ok(t.frame().includes('思考'), 'effort focus exists');
  t.resize(54, 18); await t.press(''); t.capture('model-narrow', 54, 18); await t.press('\x1b');
  t.mode = 'remote'; const before = t.calls.filter(call => call.cmd === 'model_control_apply').length;
  await t.command('/models');
  await t.wait(() => t.frame().includes('主机') && !t.frame().includes('科研模型配置'), 'remote routes to host');
  assert.equal(t.calls.filter(call => call.cmd === 'model_control_apply').length, before);
  assert.ok(!t.calls.some(call => ['agent_run', 'create', 'workflow_run'].includes(call.cmd)));
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureModelPickerFrames(); console.log(`${result.frames.length} model picker frames passed`);
  process.exit(0);
}
