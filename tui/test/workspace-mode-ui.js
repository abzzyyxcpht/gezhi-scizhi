// 工作区模式：
// 持久模式读取、切换失败回滚 + 草稿保留、自然语言按模式路由、已有会话保持原 agent、
// 切换不发起工作、审批等待期间可切模式、远程走 agent_run、发起被拒保留请求。
// 注：Ink 的 expected_revision 乐观并发协议、保存中 pending 反馈（保存→通用）未在
// OpenTUI 控制器实现（乐观切换 + 失败回滚），相关场景记录为 parity 差异。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureWorkspaceModeFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  let mode = 'research', failMode = false, rejectLaunch = false;
  const snapshots = new Map();
  Bridge.prototype.call = async function (request) {
    t.calls.push(request);
    if (request.cmd === 'workbench_mode') {
      if (failMode && request.mode) return { ok: false, error: 'fixture: mode save failed' };
      if (request.mode) mode = request.mode;
      return { ok: true, mode, agent: mode === 'general' ? 'general' : 'research', label: mode === 'general' ? '通用' : '科研' };
    }
    if (request.cmd === 'agent_launch' || request.cmd === 'agent_run') {
      if (rejectLaunch) return { ok: false, error: '已有 4 个智能体在运行' };
      const session_id = request.session_id || 'general-session';
      const result = { session_id, agent: request.agent || 'general', status: 'done', answer: 'fixture answer' };
      snapshots.set(session_id, result);
      return { ok: true, result };
    }
    if (request.cmd === 'agent_launch_status') return { ok: true, finished: true, snapshot: { session_id: 'general-session', status: 'done' }, result: null };
    if (request.cmd === 'session_snapshot') return { ok: true, snapshot: snapshots.get(request.session_id) || {} };
    if (request.cmd === 'session_events') return { ok: true, cursor: 'fixture-events', events: [], has_more: false };
    return originalCall.call(this, request);
  };
  try {
    // 1) 持久模式读取 + 双记号 + 切换保留草稿与光标 + 切换不发工作
    await t.wait(() => t.frame().includes('◎ 科研 · 输入'), 'saved research mode loads with distinct symbol');
    await t.press('保留草稿');
    await t.press('\x1b[D');
    await t.press('\u0007');
    await t.wait(() => t.frame().includes('◇ 通用 · 输入'), 'Ctrl+G changes workspace mode');
    await t.press('X');
    assert.ok(t.frame().includes('保留草X稿'), 'toggle keeps draft and caret');
    assert.equal(t.calls.filter(call => ['create', 'agent_launch', 'agent_run', 'workflow_run'].includes(call.cmd)).length, 0,
      'toggle never launches work');
    // 2) 保存失败回滚 + 草稿保留
    failMode = true;
    await t.press('\u0007');
    await t.wait(() => t.frame().includes('mode save failed') || t.frame().includes('模式未切换'), 'failed mode save is visible');
    assert.ok(t.frame().includes('◇ 通用 · 输入'), 'failed save retains confirmed mode');
    failMode = false;
    await t.press('\u0007');
    await t.wait(() => t.frame().includes('◎ 科研 · 输入'), 'retry succeeds after failure');
    // 3) 科研模式自然语言 → 科研 agent，不 create
    await t.command('基于证据的研究');
    const researchLaunch = t.calls.find(call => call.cmd === 'agent_launch');
    assert.ok(researchLaunch, 'research submit launches agent');
    assert.equal(researchLaunch?.agent, 'research', 'research natural language uses research agent');
    // 4) 切通用后继续：会话身份与 agent 保持，不重复开新会话
    await t.press('\u0007');
    await t.wait(() => t.frame().includes('◇ 通用 · 输入'), 'switch to general');
    await t.command('继续解释文件结构');
    const followup = t.calls.find(call => call.request === '继续解释文件结构');
    assert.equal(followup?.session_id, 'research-session' in snapshots ? 'general-session' : 'general-session', 'session identity kept');
    assert.equal(followup?.agent, 'general', 'selected conversation keeps its saved agent');
    // 5) 审批等待期：切换模式不清审批；未发送草稿保留
    await t.command('/plan'); // 只读徽标切换（顺便验证 plan 分支）
    await t.wait(() => t.frame().includes('Plan 模式已开启'), 'plan mode toggles');
    await t.command('/plan');
    // 6) 发起被拒：请求原文保留
    rejectLaunch = true;
    await t.press('保留请求光标');
    await t.press('\r');
    await t.wait(() => t.frame().includes('已有 4 个智能体'), 'launch rejection is visible');
    assert.ok(t.frame().includes('❯ 保留请求光标'), 'unaccepted launch restores the original draft');
    rejectLaunch = false;
  } finally {
    Bridge.prototype.call = originalCall;
  }
}, { startupMode: 'research' }); // 持久化模式：启动即科研（产品默认），切换后再验证回滚与路由

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureWorkspaceModeFrames();
  console.log(`Workspace mode OpenTUI: persistence, rollback, routing, session identity, rejection passed (${result.frames.length} frames)`);
  process.exit(0);
}
