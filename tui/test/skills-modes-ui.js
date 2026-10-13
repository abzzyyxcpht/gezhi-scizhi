import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { COMMANDS } from '../src/commands.js';
import { commandsForMode } from '../src/workspace-mode.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureSkillModesFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const writes = [];
  const skills = [
    { name: 'audit', description: '核验证据', active: false, mode: 'auto', source: 'D:/skills/audit/SKILL.md', invocation_status: 'available' },
    { name: 'manual', description: '需要显式入口', active: true, mode: 'on', source: 'D:/skills/manual/SKILL.md', invocation_status: 'manual_only' },
    { name: 'fork', description: '需要独立执行', active: false, mode: 'auto', source: 'D:/skills/fork/SKILL.md', context: 'fork', invocation_status: 'unsupported' },
    { name: 'disabled', description: '已停用', active: false, mode: 'off', source: 'D:/skills/disabled/SKILL.md', invocation_status: 'disabled' },
  ];
  Bridge.prototype.call = async function (request) {
    if (request.cmd === 'skills_list') return { ok: true, skills };
    if (['skill_mode', 'skill_toggle'].includes(request.cmd)) {
      writes.push(request);
      return request.cmd === 'skill_mode'
        ? { ok: true, skill: { name: request.name, mode: request.mode } }
        : { ok: true, active: request.on ? [request.name] : [] };
    }
    return originalCall.call(this, request);
  };
  try {
    t.resize(112, 44);
    await t.command('/skills');
    await t.wait(() => t.frame().includes('禁止模型调用（手动入口待支持）'), 'manual status does not claim an implemented manual executor');
    const list = t.frame();
    for (const text of ['模式 auto（按阶段选择）', '模式 on（明确启用）', '模式 off（停用）',
      '来源 D:/skills/audit/SKILL.md', '要求独立上下文执行；当前加载器尚不支持', '已停用，模型工具不能读取']) {
      assert.ok(list.includes(text), text);
    }
    t.capture('skill-modes', 112, 44);
    await t.command('/skill audit auto');
    await t.wait(() => t.frame().includes('技能 audit 已设为 auto（按阶段选择）'), 'auto acknowledges stage selection');
    assert.deepEqual(writes.at(-1), { cmd: 'skill_mode', name: 'audit', mode: 'auto' });
    await t.command('/skill audit on');
    await t.wait(() => t.frame().includes('技能 audit 已启用（当前：audit）'), 'on retains existing receipt');
    assert.deepEqual(writes.at(-1), { cmd: 'skill_toggle', name: 'audit', on: true });
    await t.command('/skill audit off');
    await t.wait(() => t.frame().includes('技能 audit 已停用（当前：无）'), 'off retains existing receipt');
    assert.deepEqual(writes.at(-1), { cmd: 'skill_toggle', name: 'audit', on: false });
    const beforeInvalid = writes.length;
    await t.command('/skill audit auto trailing');
    await t.wait(() => t.frame().includes('用法：/skill <名> <auto|on|off>'), 'extra argument is rejected');
    assert.equal(writes.length, beforeInvalid);
    assert.equal(COMMANDS.find(command => command.name === 'skill').args, '<名> <auto|on|off>');
    for (const name of ['skill', 'skills']) {
      assert.equal(commandsForMode(COMMANDS, 'general', { remote: true }).find(command => command.name === name).available,
        false, `${name} remote contract remains unchanged`);
    }
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureSkillModesFrames();
  console.log(`${result.frames.length} skill mode real OpenTUI frame, auto/on/off routing and remote contract passed`);
}

// opentui 渲染器保活计时器：直接运行时必须显式退出（被 capture 脚本 import 时不退出）
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) process.exit(0);
