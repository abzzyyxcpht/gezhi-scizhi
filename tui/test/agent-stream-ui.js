// Agent 事件流：local launch_pending 期刊等待、
// remote 只读会话、legacy 引擎无事件流时的如实回退（最终回复 + 探测一次）。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

const mode = process.argv[2] || 'remote';

export const captureAgentStreamFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const calls = [];
  let finished = false, eventStage = 0, holdStatus = false;
  const held = [];
  Bridge.prototype.call = async function (request) {
    t.calls.push(request); // 包装层也要记账：夹具只在 originalCall 里 push
    if (['agent_run', 'agent_launch'].includes(request.cmd)) return { ok: true, result: {
      session_id: 'stream-session', status: mode === 'legacy' ? 'done' : 'running',
      ...(mode === 'local' ? { launch_pending: true } : {}),
      ...(mode === 'legacy' ? { answer: 'LEGACY complete' } : {}) } };
    if (request.cmd === 'agent_launch_status') {
      if (holdStatus) return new Promise(resolve => held.push(resolve));
      return { ok: true, finished,
        snapshot: { session_id: 'stream-session', status: finished ? 'done' : 'running' },
        result: finished ? { session_id: 'stream-session', status: 'done', answer: 'STREAMING first final answer' } : null };
    }
    if (request.cmd === 'session_snapshot') return { ok: true, snapshot: { session_id: request.session_id, status: finished ? 'done' : 'running' } };
    if (mode === 'legacy' && (request.cmd === 'agent_continue' || request.cmd === 'command')) {
      return { ok: true, result: { session_id: 'stream-session', status: 'done', answer: 'LEGACY complete' } };
    }
    if (request.cmd === 'session_events') {
      if (mode === 'legacy') return { ok: false, error: '未知命令：session_events' };
      if (eventStage === 0) { eventStage = 1; return { ok: true, reset: false, cursor: 'first', has_more: false, events: [
        { kind: 'user_message', payload: { text: 'question' } },
        { kind: 'assistant_delta', payload: { step: 1, delta: 'STREAMING first' } },
        { kind: 'subagent_status', payload: { child: 'child-1', agent: 'critic', status: 'waiting_approval' } },
      ] }; }
      if (finished && eventStage === 1) { eventStage = 2; return { ok: true, reset: false, cursor: 'last', has_more: false, events: [
        { kind: 'assistant_delta', payload: { step: 1, delta: ' final answer' } },
        { kind: 'assistant_message', payload: { step: 1, content: 'STREAMING first final answer' } },
      ] }; }
      return { ok: true, reset: false, cursor: request.cursor, has_more: false, events: [] };
    }
    return originalCall.call(this, request);
  };
  try {
    const firstCommand = mode === 'remote' ? '/session stream-session' : '第一问';
    if (mode === 'remote') {
      await t.command(firstCommand);
      await t.wait(() => t.frame().includes('会话 stream-session'), 'open existing remote session');
      await t.press('q');
      await t.wait(() => !t.frame().includes('（↑↓ 滚动'), 'return to remote stream composer');
      assert.equal(t.calls.filter(call => ['agent_run', 'agent_launch'].includes(call.cmd)).length, 0,
        'remote streaming does not use host-only launch RPCs');
      return;
    }
    if (mode === 'legacy') {
      await t.command(firstCommand);
      await t.wait(() => t.frame().includes('LEGACY complete') && t.frame().includes('不支持实时事件流'),
        'legacy final and truthful fallback');
      // 重新打开同一会话：事件流探测每个连接只发生一次
      const probeCount = t.calls.filter(call => call.cmd === 'session_events').length;
      await t.command('/session stream-session');
      await t.wait(() => t.frame().includes('会话 stream-session'), 'reopen same session');
      assert.equal(t.calls.filter(call => call.cmd === 'session_events').length, probeCount,
        'unsupported stream probes once per submit');
      await t.press('q');
      await t.command('/continue');
      await t.wait(() => t.calls.some(call => call.cmd === 'agent_continue'), 'same-turn continue called');
      assert.ok(t.frame().split('LEGACY complete').length - 1 <= 2, 'same-turn continuation must not render a duplicate answer');
      return;
    }
    // local：launch_pending 期，流式增量先于完成渲染
    holdStatus = true;
    await t.command(firstCommand);
    await t.wait(() => t.frame().includes('STREAMING first'), 'assistant delta must render before completion');
    assert.ok(t.frame().includes('critic') && t.frame().includes('待审批'), 'approval wait visible');
    assert.ok(!t.frame().includes('critic 失败'), 'approval wait is not failure');
    assert.ok(t.frame().includes('处理中') || t.frame().includes('就绪'), 'busy indicator present during stream');
    finished = true;
    holdStatus = false;
    held.forEach(resolve => resolve({ ok: true, finished: true,
      snapshot: { session_id: 'stream-session', status: 'done' },
      result: { session_id: 'stream-session', status: 'done', answer: 'STREAMING first final answer' } }));
    await t.wait(() => t.frame().includes('final answer'), 'final content renders after completion');
    assert.ok(t.calls.some(call => call.cmd === 'session_events' && call.cursor === 'first'), 'opaque cursor is reused');
  } finally {
    held.forEach(resolve => resolve({ ok: true, finished: true, snapshot: {}, result: null }));
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureAgentStreamFrames();
  console.log(`Agent stream OpenTUI regression: ${mode} passed (${result.frames.length} frames)`);
  process.exit(0);
}
