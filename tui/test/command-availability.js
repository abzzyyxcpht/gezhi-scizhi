import assert from 'node:assert/strict';
import { COMMANDS } from '../src/commands.js';
import { FEATURE_COMMANDS } from '../src/feature-commands.js';
import { CONTROL_COMMANDS, paletteCommands } from '../src/command-palette.js';
import { commandsForMode } from '../src/workspace-mode.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

const all = [...COMMANDS, ...CONTROL_COMMANDS];
const command = (name, context = {}) => commandsForMode(all, 'general', context).find(row => row.name === name);
for (const row of commandsForMode(all, 'general')) {
  assert.equal(typeof row.busy_allowed, 'boolean', `${row.name}: busy contract`);
  assert.equal(typeof row.shortcut, 'string', `${row.name}: shortcut contract`);
  assert.equal(typeof row.effect, 'string', `${row.name}: effect contract`);
  assert.equal(typeof row.available, 'boolean', `${row.name}: availability contract`);
  assert.equal(typeof row.disabled_reason, 'string', `${row.name}: unavailable reason contract`);
}
assert.equal(command('new', { busy: true }).available, false);
assert.ok(command('new', { busy: true }).disabled_reason.includes('上一命令'));
assert.equal(command('new', { remote: true }).available, true);
assert.equal(command('mode', { busy: true }).busy_allowed, true);
assert.equal(command('cancel-agent', { busy: true }).available, true);
assert.equal(command('queue', { busy: true, command: { name: 'queue', args: 'add 下一轮' } }).available, true);
assert.equal(command('queue', { busy: true, command: { name: 'queue', args: 'unknown' } }).available, false);
assert.equal(command('queue', { remote: true, command: { name: 'queue', args: 'enable' } }).available, false);
assert.equal(command('queue', { remote: true, command: { name: 'queue', args: 'add 下一轮' } }).available, true);
assert.equal(command('steer', { remote: true, command: { name: 'steer', args: 'enable' } }).available, false);
assert.equal(command('queue', { remote: true, command: { name: 'steer', args: 'enable' } }).available, true, 'one command action must not disable another command');
assert.equal(command('reviews', { remote: true, command: { name: 'reviews', args: 'enable' } }).available, false);
assert.equal(command('model', { remote: true }).available, false);
assert.equal(command('model', { remote: true, command: { name: 'model', args: 'capabilities' } }).available, true);
assert.equal(command('connections', { remote: true }).available, true);
assert.equal(command('route', { remote: true }).available, true);
assert.equal(command('route', { remote: true, command: { name: 'route', args: 'planning lab' } }).available, false);
assert.equal(command('theme', { remote: true }).available, true);
assert.equal(command('terminal', { remote: true }).available, true, 'native client terminal keeps its original behavior');
for (const row of FEATURE_COMMANDS) assert.equal(command(row.name, { remote: true }).available, row.name === 'evidence', `${row.name}: follows the feature handler remote gate`);
assert.equal(command('agent', { remote: true }).available, false);
assert.equal(command('approve', { remote: true }).available, true);
assert.equal(command('mode').shortcut, 'Ctrl+G');
assert.equal(paletteCommands([], [{ name: 'plugin', help: '插件命令' }], '', 'general', { busy: true })[0].available, false);
for (const mode of ['general', 'research']) {
  assert.equal(paletteCommands(all, [], 'skills', mode)[0]?.name, 'skills', 'complete names outrank longer prefixes');
  assert.equal(paletteCommands(all, [], '/skills', mode)[0]?.name, 'skills', 'palette accepts pasted slash commands');
  assert.equal(paletteCommands(all, [], '/mode', mode)[0]?.name, 'mode');
  assert.ok(paletteCommands(all, [], '/', mode).length > 6, 'a bare slash discovers the full command catalog');
}

await upgradeFixture(async t => {
  t.mode = 'remote';
  await t.command('/new 远程科研方向');
  await t.wait(() => t.calls.some(call => call.cmd === 'workflow_run'), 'remote research creation uses the allowed workflow endpoint');
  assert.deepEqual(t.calls.find(call => call.cmd === 'workflow_run'), {
    cmd: 'workflow_run', workflow_id: 'research_pipeline', topic: '远程科研方向',
  });
  assert.equal(t.calls.filter(call => call.cmd === 'create').length, 0, 'remote /new must not use the local-only create endpoint');
});

await upgradeFixture(async t => {
  t.mode = 'remote';
  await t.press('保留远程草稿'); await t.press('\x1b[D'); await t.press('\u0010'); await t.press('models');
  await t.wait(() => t.frame().includes('主机') && t.frame().includes('/models'), 'remote palette explains disabled model configuration');
  await t.press('\r');
  assert.ok(t.frame().includes('命令面板'), 'disabled palette selection remains open');
  assert.ok(t.frame().includes('保留远程草稿'), 'disabled selection preserves composer');
  assert.equal(t.calls.filter(call => call.cmd === 'model_control_catalog').length, 0);
  await t.press('\x1b'); await t.press('X');
  assert.ok(t.frame().includes('保留远程草X稿'), 'disabled selection preserves original caret');
  await t.press('\u0005'); await t.press('\u0015'); await t.press('/models'); await t.press('\r');
  assert.ok(t.frame().split('\n').filter(line => line.includes('❯')).at(-1)?.includes('/models'), 'disabled completion Enter keeps the typed command');
  await t.press('\t');
  assert.ok(t.frame().split('\n').filter(line => line.includes('❯')).at(-1)?.includes('/models'), 'disabled Tab leaves the command untouched');
  await t.press(' '); await t.press('\r');
  assert.ok(t.frame().split('\n').filter(line => line.includes('❯')).at(-1)?.includes('/models'), 'adding arguments cannot bypass the remote gate');
  assert.equal(t.calls.filter(call => call.cmd === 'model_control_catalog').length, 0);
});

await upgradeFixture(async t => {
  t.hold = 'settings_save';
  await t.command('/reasoning high');
  await t.wait(() => t.held.length > 0, 'setting request owns busy state');
  await t.press('忙碌中草稿'); await t.press('\u0010'); await t.press('new');
  await t.wait(() => t.frame().includes('上一命令') && t.frame().includes('/new'), 'busy palette explains why task creation is disabled');
  await t.press('\r');
  assert.ok(t.frame().includes('命令面板') && t.frame().includes('忙碌中草稿'));
  assert.equal(t.calls.filter(call => call.cmd === 'create').length, 0);
});
console.log('Command availability metadata, busy/remote palette and disabled completion preserve draft/caret passed');

// opentui 渲染器保活计时器：直接运行时必须显式退出（被 import 时不退出）
import path from 'node:path';
import { fileURLToPath } from 'node:url';
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) process.exit(0);
