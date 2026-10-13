// 事件游标恢复（Ink agent-events-ui 的 OpenTUI 移植）：失败页同游标重试、
// rewind reset 游标失效重取、流式/重试不清草稿、本地零 journal 读。
// 注：Ink 的「在途旧页不得渲染」由串行提交结构性保证（忙时排队），不再需要令牌守卫。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureAgentEventFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const originalRead = fs.readFileSync;
  let localJournalReads = 0;
  fs.readFileSync = function (file, ...args) {
    if (String(file).includes('sessions') && String(file).endsWith('.jsonl')) localJournalReads++;
    return originalRead.call(this, file, ...args);
  };
  const calls = [];
  let failed = false, stage = 'initial';
  const event = text => ({ kind: 'assistant_delta', payload: { step: 1, delta: text } });
  Bridge.prototype.call = async function (request) {
    calls.push(request);
    if (request.cmd === 'agent_launch') return { ok: true, result: { session_id: 'first-session', status: 'running', launch_pending: true } };
    if (request.cmd === 'session_snapshot') return { ok: true, snapshot: { session_id: request.session_id, status: 'running' } };
    if (request.cmd === 'session_events') {
      if (!request.cursor) return { ok: true, events: [event('ORIGINAL STREAM')], cursor: 'first-cursor', reset: false, has_more: false };
      if (stage === 'retry' && !failed) { failed = true; throw new Error('temporary event failure'); }
      if (stage === 'reset') return { ok: true, events: [event('REPLACED STREAM')], cursor: 'rewound-cursor', reset: true, has_more: false };
      return { ok: true, events: [], cursor: request.cursor, reset: false, has_more: false };
    }
    return originalCall.call(this, request); // agent_launch_status 走夹具 hold，驱动重试发生在活轮询里
  };
  try {
    t.hold = 'agent_launch_status';
    await t.command('原始流式请求');
    await t.wait(() => t.frame().includes('ORIGINAL STREAM'), 'first incremental page');
    await t.press('DRAFT PRESERVED');
    await t.wait(() => t.frame().includes('❯ DRAFT PRESERVED'), 'draft input');
    // 失败页：同游标重试，不推进游标、不清草稿
    stage = 'retry';
    t.held.splice(0).forEach(item => item.resolve({ ok: true, launch_pending: true }));
    await t.wait(() => calls.filter(call => call.cmd === 'session_events' && call.cursor === 'first-cursor').length >= 2,
      'failed page retries unchanged cursor');
    assert.ok(t.frame().includes('❯ DRAFT PRESERVED'), 'stream/retry never clear composer input');
    // rewind reset：游标失效，从头发起重取
    stage = 'reset';
    t.held.splice(0).forEach(item => item.resolve({ ok: true, launch_pending: true }));
    await t.wait(() => t.frame().includes('REPLACED STREAM'), 'rewound page refetched from scratch');
    assert.ok(calls.some(call => call.cmd === 'session_events' && call.cursor === null),
      'reset invalidates the cursor for a fresh fetch');
    // 收尾：结束轮询
    t.hold = '';
    t.held.splice(0).forEach(item => item.resolve({ ok: true }));
    assert.equal(localJournalReads, 0, 'local TUI never synchronously reads session JSONL');
  } finally {
    t.hold = '';
    t.held.forEach(item => item.resolve({ ok: true }));
    fs.readFileSync = originalRead;
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureAgentEventFrames();
  console.log(`Event cursor retry, rewind reset, composer preservation and zero local journal reads passed (${result.frames.length} frames)`);
  process.exit(0);
}
