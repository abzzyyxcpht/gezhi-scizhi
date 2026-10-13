import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureCommandPaletteFrames = () => upgradeFixture(async t => {
  await t.press('保留光标草稿'); await t.press('\x1b[D');
  await t.press('\u0010');
  await t.wait(() => t.frame().includes('搜索：'), 'Ctrl+P opens the palette');
  assert.ok(t.frame().includes('保留光标草稿'), 'opening retains original draft');
  await t.press('实验室文献');
  await t.wait(() => t.frame().includes('/lab-probe'), 'Chinese plugin description search');
  t.capture('palette-plugin-search');
  await t.press('\x1b'); await t.press('X');
  assert.ok(t.frame().includes('保留光标草X稿'), 'Esc restores original caret and input');
  await t.press('\u0010'); await t.press('模型配置');
  assert.ok(t.frame().includes('/models'), 'static Chinese description is searchable');
  await t.press('\r');
  await t.wait(() => t.frame().includes('/models') && !t.frame().includes('搜索：'), 'Enter fills a command without dispatch');
  assert.equal(t.calls.filter(call => call.cmd === 'model_control_catalog').length, 0);
  await t.command('/palette');
  await t.wait(() => t.frame().includes('搜索：'), '/palette opens');
  await t.press('no-such-command');
  assert.ok(t.frame().includes('无匹配命令')); t.capture('palette-empty-search');
  await t.press('\x1b'); await t.press('回到输入');
  assert.ok(t.frame().includes('回到输入'));
  t.resize(54, 18); await t.press('\u0010'); t.capture('palette-narrow', 54, 18);
  assert.ok(!t.calls.some(call => ['agent_run', 'create', 'workflow_run', 'plugin'].includes(call.cmd)));
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureCommandPaletteFrames();
  console.log(`${result.frames.length} command palette real OpenTUI frames passed`);
}

// opentui 渲染器保活计时器：直接运行时必须显式退出（被 capture 脚本 import 时不退出）
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) process.exit(0);
