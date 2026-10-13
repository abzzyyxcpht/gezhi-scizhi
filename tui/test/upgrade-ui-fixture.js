// UI 夹具（M4 后唯一引擎 OpenTUI）：testRender 驱动真 runtime + 全桩 Bridge。
// 由 Ink 版移植：同一套 Bridge 桩与断言口径，输入/帧改走 opentui 测试工具。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import React from 'react';
import { testRender } from '@mbears/opentui-react/test-utils';
import stringWidth from 'string-width';
import { Bridge } from '../src/bridge.js';
import { OpenTuiApp } from '../src/opentui/runtime.js';
import { createSessionController } from '../src/opentui/session-controller.js';

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

// opentui 渲染器带保活计时器：断言失败若变成未处理拒绝会挂住进程，
// 直接运行时统一以非零码退出（被 capture 脚本 import 时同样只影响失败路径）。
process.on("unhandledRejection", (error) => {
  console.error(error);
  process.exit(1);
});

// 所有 React.act 串行化：wait 轮询与 command 按键派发并发时会互相践踏丢按键
// （stdin 数据在另一个 act 的渲染中途处理即丢失），故统一走同一条链。
let actChain = Promise.resolve();
const act = (fn) => {
  const run = actChain.then(fn);
  actChain = run.catch(() => {});
  return run;
};

// Ink 原始按键序列 → opentui mockInput 映射（\x1b[A/B/C/D、\r、\t、\u0010 等）
async function send(setup, input) {
  const arrow = { '\x1b[A': 'up', '\x1b[B': 'down', '\x1b[C': 'right', '\x1b[D': 'left' };
  if (arrow[input]) {
    await act(async () => { setup.mockInput.pressArrow(arrow[input]) });
    return;
  }
  if (input === '\r') {
    await act(async () => { setup.mockInput.pressEnter() });
    return;
  }
  if (input === '\x1b') {
    await act(async () => { setup.mockInput.pressEscape() });
    return;
  }
  if (input === '\t') {
    await act(async () => { setup.mockInput.pressTab() });
    return;
  }
  if (input === '\x1bOP') {
    await act(async () => { setup.mockInput.pressKey('F1') });
    return;
  }
  if (input === '\u0010') {
    await act(async () => { setup.mockInput.pressKey('p', { ctrl: true }) });
    return;
  }
  if (input === '\u0015') {
    await act(async () => { setup.mockInput.pressKey('u', { ctrl: true }) });
    return;
  }
  if (input === '\u0005') {
    await act(async () => { setup.mockInput.pressKey('e', { ctrl: true }) });
    return;
  }
  if (input === '\u0003') {
    await act(async () => { setup.mockInput.pressKey('c', { ctrl: true }) });
    return;
  }
  // 单个数字：面板数字直选需要 key.name 语义（typeText 不产生按键事件）
  if (/^[0-9]$/.test(input)) {
    await act(async () => { setup.mockInput.pressKey(input) });
    return;
  }
  await act(async () => { await setup.mockInput.typeText(input) });
}

export async function upgradeFixture(run, options = {}) {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'gezhi-upgrade-ui-'));
  const old = { start: Bridge.prototype._startLocal, call: Bridge.prototype.call,
    connection: Bridge.prototype.connection, env: { ...process.env } };
  process.env.GEZHI_DATA_DIR = scratch; process.env.GEZHI_REMOTE_URL = '';
  process.env.GEZHI_REDUCED_MOTION = '1';
  let setup;
  const context = { calls: [], frames: [], mode: 'local', held: [], hold: '', fail: false,
    settings: { ui_animations: false, llm_reasoning_effort: 'low' }, revision: 'revision-1',
    startupMode: ['general', 'research'].includes(options.startupMode) ? options.startupMode : 'general' };
  context.catalog = (stage = 'planning') => ({ revision: context.revision, stage,
    active_id: 'legacy', stages: ['planning', 'synthesis'], items: [
      { id: 'legacy', label: '默认连接', model: 'legacy-model', protocol: 'chat_completions',
        configured: true, reasoning_effort: 'low', efforts: [
          { value: 'auto', enabled: true, reason: '' }, { value: 'low', enabled: true, reason: '' },
          { value: 'max', enabled: false, reason: '该适配器不接受 max' }] },
      { id: 'lab', label: '实验模型', model: 'profile-model', protocol: 'responses',
        configured: true, reasoning_effort: 'high', efforts: [
          { value: 'auto', enabled: true, reason: '' }, { value: 'high', enabled: true, reason: '' }] },
    ] });
  Bridge.prototype._startLocal = function () { this._readyPromise = Promise.resolve(true); };
  Bridge.prototype.connection = () => ({ mode: context.mode, url: context.mode === 'remote' ? 'http://fixture.invalid' : '' });
  Bridge.prototype.call = async function (request) {
    context.calls.push(request);
    const answer = () => {
      if (request.cmd === 'model_control_catalog') return { ok: true, catalog: context.catalog(request.stage) };
      if (request.cmd === 'model_control_apply') {
        if (context.fail) return { ok: false, error: 'fixture：模型配置已变化，请刷新' };
        context.revision = 'revision-2';
        return { ok: true, catalog: { ...context.catalog(request.stage), active_id: request.profile_id }, settings: context.settings };
      }
      if (request.cmd === 'session_activity') return { ok: true, activity: context.activity || {} };
      if (request.cmd === 'settings_get') return { ok: true, settings: context.settings };
      // 启动模式读取（无 mode 字段）：未自备桩的测试按 options.startupMode 模拟持久化模式；写回原样回声
      if (request.cmd === 'workbench_mode') return { ok: true, mode: request.mode || context.startupMode };
      const fixtures = { nodes: { nodes: [] }, list: { tasks: [] }, session_status: { status: { context: {
          measurement: 'heuristic_tokens', tokens: 7200, limit_tokens: 12000, messages: 12, compressions: 1,
        } } },
        plugin_commands: { commands: [{ name: 'lab-probe', help: '实验室文献检索插件' }] },
        llm_status: { configured: true, model: 'legacy-model', connection_id: 'legacy', reasoning_effort: 'low' },
        // /connections 面板数据源：model_registry.describe() 形状（连接/模型/生效阶段）
        model_registry: { registry: {
          connections: [
            { id: 'lab', label: '实验室网关', protocol: 'responses', base_url: 'https://gw.example.edu/v1', has_api_key: true },
            { id: 'backup', label: '备用镜像', protocol: 'chat_completions', base_url: 'https://backup.example.edu/v1', has_api_key: false },
          ],
          profiles: [
            { id: 'lab-profile', label: '实验模型', connection_id: 'lab', model: 'profile-model', reasoning_effort: 'high' },
            { id: 'spare', label: '备用模型', connection_id: 'backup', model: 'spare-model', reasoning_effort: 'auto' },
          ],
          routes: { planning: 'lab-profile' },
          resolved: {
            planning: { stage: 'planning', profile_id: 'lab-profile', connection_id: 'lab', model: 'profile-model', protocol: 'responses', base_url: 'https://gw.example.edu/v1' },
            extraction: { stage: 'extraction', profile_id: 'lab-profile', connection_id: 'lab', model: 'profile-model', protocol: 'responses', base_url: 'https://gw.example.edu/v1' },
            synthesis: { stage: 'synthesis', profile_id: 'legacy', connection_id: 'legacy', model: 'legacy-model', protocol: 'chat_completions', base_url: '' },
            review: { stage: 'review', profile_id: 'legacy', connection_id: 'legacy', model: 'legacy-model', protocol: 'chat_completions', base_url: '' },
          },
          protocols: ['chat_completions', 'responses'], thinking_styles: ['auto'], efforts: ['auto', 'low', 'high'],
          stages: ['planning', 'extraction', 'synthesis', 'review'],
        } },
        plugins_summary: { plugins: { count: 1, names: ['lab'] } }, skills_list: { skills: [] },
        context_status: { context: { found: false } }, usage: { usage: { model: {} } },
        workbench_status: { processes: [], loops: [] } };
      return { ok: true, ...(fixtures[request.cmd] || {}) };
    };
    if (context.hold === request.cmd) return new Promise(resolve => context.held.push({ request, resolve, answer }));
    return answer();
  };
  // 帧归一：剥掉草稿光标方块符，使「草稿完整保留」类断言与 Ink 帧口径一致
  context.frame = () => (setup ? setup.captureCharFrame().replace(/▋/g, '') : '');
  context.wait = async (predicate, label) => {
    const until = Date.now() + 5000;
    while (Date.now() < until) {
      await act(async () => { await setup.renderOnce() });
      if (predicate()) return;
      await pause(15);
    }
    assert.fail(`${label}\n${context.frame()}`);
  };
  context.press = async input => {
    await send(setup, input);
    await act(async () => { await setup.renderOnce(); });
    await pause(50);
  };
  context.command = async input => {
    await context.press('\u0015'); await context.press(input); await context.press('\r');
  };
  // bracketed 粘贴助手：整包送入（解析器跨 chunk 累积，分片粘贴用 context.press 逐段送标记）
  context.paste = async text => {
    await act(async () => { await setup.mockInput.pasteBracketedText(text); });
    await act(async () => { await setup.renderOnce(); });
    await pause(50);
  };
  context.resize = (columns, rows) => setup.resize(columns, rows);
  // 滚轮（会话区回看）：dir 为 'up' | 'down'
  context.scrollWheel = async (dir) => {
    await act(async () => { await setup.mockMouse.scroll(4, 1, dir) });
    await act(async () => { await setup.renderOnce(); });
    await pause(50);
  };
  context.capture = (id, width = 80, height = 24, requireInput = true) => {
    const rows = context.frame().replace(/\n$/, '').split('\n');
    const text = rows.join('\n');
    if (requireInput) assert.ok(text.includes('· 输入'), `${id}: composer remains accessible`);
    assert.ok(rows.length <= height, `${id}: ${rows.length} > ${height} rows\n${text}`);
    assert.ok(rows.every(line => stringWidth(line) <= width), `${id}: width`);
    context.frames.push({ id, width, height, text });
  };
  try {
    const controller = createSessionController({ bridge: new Bridge(), sleep: () => Promise.resolve() });
    setup = await testRender(React.createElement(OpenTuiApp, { reducedMotion: true, controller }), {
      width: 80, height: 24, targetFps: 30,
    });
    await context.wait(() => context.frame().includes('· 输入'), 'fixture ready');
    await run(context);
    return { fixture: true, native_pty: false, source: 'real OpenTUI runtime, in-memory Bridge', assertions_passed: true, frames: context.frames };
  } finally {
    context.held.forEach(item => item.resolve(item.answer()));
    // testRender 返回 renderer.destroy()，没有 cleanup()；失败路径也必须卸载计时器。
    try { setup?.renderer?.destroy(); } catch {}
    Bridge.prototype._startLocal = old.start; Bridge.prototype.call = old.call;
    Bridge.prototype.connection = old.connection;
    for (const key of ['GEZHI_DATA_DIR', 'GEZHI_REMOTE_URL', 'GEZHI_REDUCED_MOTION']) {
      if (old.env[key] === undefined) delete process.env[key]; else process.env[key] = old.env[key];
    }
    fs.rmSync(scratch, { recursive: true, force: true });
  }
}
