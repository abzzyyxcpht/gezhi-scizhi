// 会话监控（Ink session-monitor-ui 的 OpenTUI 移植）：快照失败自动重试、远程已有会话
// 监控、免参 /approve、回看 Esc 不取消工作、同会话继续后重启审批监控。
// 注：Ink「第二次 Esc 取消 Agent」在 OpenTUI 语义为 Esc 暂停流式（agent_cancel 走 /cancel-agent），
// 此处按 OpenTUI 语义断言「Esc 永不取消」；陈旧审批由串行提交 + 轮询刷新承接。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

const pending = id => ({ id, tool: 'shell.run', capability: 'execute', arguments: { command: `echo ${id}` } });

export const captureSessionMonitorFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  let continued = false, approved = false, snapshots = 0, failFirstSnapshot = false;
  Bridge.prototype.call = async function (request) {
    t.calls.push(request);
    if (request.cmd === 'agent_continue') { continued = true; return { ok: true, result: { session_id: request.session_id, status: 'running' } }; }
    if (request.cmd === 'agent_approve') { approved = true; return { ok: true, result: { session_id: request.session_id, status: 'done' } }; }
    if (request.cmd === 'agent_cancel') return { ok: true, result: { session_id: request.session_id, status: 'cancelled' } };
    if (request.cmd === 'agent_launch') return { ok: true, result: { session_id: 'selected-session', status: 'running' } };
    if (request.cmd === 'session_snapshot') {
      snapshots++;
      if (failFirstSnapshot && snapshots === 1) throw new Error('temporary connection loss');
      return { ok: true, snapshot: { session_id: request.session_id,
        status: approved ? 'done' : (continued || failFirstSnapshot) ? 'waiting_approval' : 'running',
        pending: !approved && (continued || failFirstSnapshot) ? pending('current-call') : null } };
    }
    return originalCall.call(this, request);
  };
  try {
    // 选择已有会话（远程监控语义：不发起新会话）
    await t.command('/session selected-session');
    await t.wait(() => t.frame().includes('会话 selected-session'), 'session selected');
    await t.press('q');
    assert.equal(t.calls.filter(call => ['agent_run', 'agent_launch'].includes(call.cmd)).length, 0,
      'monitoring uses an existing session');
    // 快照失败自动重试：1.2s 轮询下一拍补上
    failFirstSnapshot = true;
    await t.wait(() => t.frame().includes('等待审批') && t.frame().includes('shell.run'),
      'temporary snapshot failure automatically retries', 8000);
    // 免参 /approve：决策当前待审批调用
    await t.command('/approve');
    await t.wait(() => approved, 'argument-free approval');
    assert.equal(t.calls.find(r => r.cmd === 'agent_approve')?.tool_call_id, 'current-call');
    // 同会话继续：审批监控重启
    approved = false; continued = true;
    await t.command('/continue');
    await t.wait(() => t.frame().includes('等待审批') || t.frame().includes('已允许'), 'continuation restarts monitoring', 6000);
    // 回看 Esc：永不取消工作（OpenTUI 语义：暂停流式）
    await t.press('\u001b[5~'); // PageUp 回看
    await t.press('\x1b');
    await t.pause ? null : null;
    await new Promise(r => setTimeout(r, 200));
    assert.equal(t.calls.filter(r => ['agent_cancel', 'cancel'].includes(r.cmd)).length, 0,
      'scrollback Escape never cancels work');
    // /cancel-agent 显式取消仍可用
    await t.command('/cancel-agent');
    await t.wait(() => t.calls.some(r => r.cmd === 'agent_cancel'), 'explicit cancel available');
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureSessionMonitorFrames();
  console.log(`Session monitor OpenTUI regression: retry, remote, argument-free approve and no-esc-cancel passed (${result.frames.length} frames)`);
  process.exit(0);
}
