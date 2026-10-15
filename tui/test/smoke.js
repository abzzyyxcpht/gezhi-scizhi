// 冒烟：握手首屏 / 命令表 / Tab 补全 / Enter 补全 / Esc 清空 /
// Ctrl+C 提示 / 行内编辑 / Ctrl+L 清屏 / /read 用法 / 忙碌指示 / 历史召回 / /menu 面板等价。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureSmokeFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  let holdDoctor = false;
  const held = [];
  Bridge.prototype.call = async function (request) {
    t.calls.push(request);
    if (request.cmd === 'doctor') { // /doctor 走 BRIDGE_COMMAND_MAP 直连，桩按真实请求拦截
      if (holdDoctor) return new Promise(resolve => held.push(resolve));
      return { ok: true, text: '系统自检：12/12 项通过' };
    }
    return originalCall.call(this, request);
  };
  try {
    // 0) 引擎就绪握手 + 首屏
    await t.wait(() => t.frame().includes('格致'), 'brand visible');
    assert.ok(t.frame().includes('Ctrl+G'), 'mode switch discoverable');
    assert.ok(t.frame().includes(process.version ? '· 输入' : '· 输入'), 'composer ready');
    // 1) /help 命令表
    await t.command('/help');
    await t.wait(() => t.frame().includes('条命令'), 'help table');
    assert.ok(t.frame().includes('/mode') && t.frame().includes('/read'), 'core commands listed');
    // 2) Tab 补全：/the → /theme
    await t.press('\u0015');
    await t.press('/the');
    await t.press('\t');
    await t.wait(() => t.frame().includes('/theme'), 'Tab completes to /theme');
    await t.press('\u0015');
    // 3) 补全面板：/rea 列候选，Enter 先补全不报未知命令
    await t.press('/rea');
    await t.wait(() => t.frame().includes('/read'), 'completion panel lists candidates');
    await t.press('\r');
    await t.wait(() => t.frame().includes('❯ /read') && !t.frame().includes('未知命令'), 'Enter completes before submitting');
    await t.press('\u0015');
    // 4) Esc 清空
    await t.press('随便打点字');
    await t.press('\x1b');
    await t.wait(() => t.frame().includes('草稿已清空') || t.frame().includes('或直接输入'), 'Esc clears draft');
    // 5) Ctrl+C 首击仅提示 —— 暂时移除：opentui-core 0.2.15 测试渲染器在完整应用中收到 ETX
    // 按键后，下一次 captureCharFrame 必崩（原生段错误，与应用按键处理无关，裸应用不复现）。
    // 三级语义的纯逻辑覆盖见 test/opentui-shell-logic.js（resolveCtrlCIntent）；
    // 待渲染器修复或升级后恢复本步骤（已知问题记录于 docs/opentui-parity.md）。
    // 6) 行内编辑 Ctrl+A/E/U
    await t.press('abc');
    await t.press('\u0005'); // Ctrl+E 行尾
    await t.press('Z');
    await t.wait(() => t.frame().includes('❯ abcZ'), 'Ctrl+E then insert at end');
    await t.press('\u0015');
    await t.wait(() => !t.frame().includes('❯ abcZ'), 'Ctrl+U clears to start');
    // 7) Ctrl+L 清屏保留输入
    await t.press('/help');
    await t.press('\u000C');
    await t.wait(() => t.frame().includes('已清屏') && t.frame().includes('❯ /help'), 'Ctrl+L clears but keeps draft');
    await t.press('\u0015');
    // 8) /read 缺参 → 用法提示
    await t.command('/read');
    await t.wait(() => t.frame().includes('可阅读：digest'), '/read usage hint');
    // 9) /doctor 忙碌指示 → 结果返回
    holdDoctor = true;
    await t.command('/doctor');
    await t.wait(() => t.frame().includes('处理中'), 'busy indicator');
    holdDoctor = false;
    held.forEach(resolve => resolve({ ok: true, text: '系统自检：12/12 项通过' }));
    await t.wait(() => t.frame().includes('系统自检：12/12 项通过'), 'doctor result returns');
    // 10) ↑ 历史召回
    await t.press('\x1b[A');
    await t.wait(() => t.frame().includes('❯ /doctor'), 'history recall to live input');
    await t.press('\u0015');
    // 11) /menu → 命令面板等价入口
    await t.command('/menu');
    await t.wait(() => t.frame().includes('命令面板'), 'menu opens palette equivalent');
    await t.press('\x1b');
    assert.ok(t.calls.some(call => call.cmd === 'workbench_mode'), 'startup synced workspace mode via bridge');
  } finally {
    held.forEach(resolve => resolve({ ok: true, text: '系统自检：12/12 项通过' }));
    Bridge.prototype.call = originalCall;
  }
}, { startupMode: 'research' }); // 冒烟流程按科研工作台：/rea 补全首选 /read

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureSmokeFrames();
  console.log(`TUI 冒烟通过：${result.frames.length} 帧`);
  process.exit(0);
}
