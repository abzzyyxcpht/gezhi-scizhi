import assert from 'node:assert/strict';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureModelPickerNarrowFrames = () => upgradeFixture(async t => {
  await t.command('/models');
  await t.wait(() => t.frame().includes('科研模型配置'), 'narrow picker opens');
  await t.press('第一行'); await t.press('\u000a'); await t.press('第二行'); await t.press('\u000a'); await t.press('第三行');
  t.fail = true; t.resize(54, 18); await t.press(''); await t.press('\r');
  await t.wait(() => t.frame().includes('fixture：模型配置已变化'), 'CAS failure remains visible');
  assert.ok(t.frame().replace(/\n$/, '').split('\n').length <= 18, 'three-line draft + CAS error fits 54x18');
  assert.ok(t.frame().includes('第一行') && t.frame().includes('第三行'), 'multiline draft remains intact');
  t.capture('model-narrow-cas', 54, 18);
  assert.ok(!t.calls.some(call => ['agent_run', 'create', 'workflow_run'].includes(call.cmd)));
});

if (process.argv[1]?.endsWith('model-picker-narrow-ui.js')) {
  const result = await captureModelPickerNarrowFrames(); console.log(`${result.frames.length} narrow model-picker frames passed`);
  process.exit(0);
}
