// 子代理进度行：transcript.js 纯函数（活模块，保留原断言）+ OpenTUI 事件流端到端渲染。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { subagentLine, readSessionEvents } from '../src/transcript.js';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

// ---- 纯函数：subagentLine 记号与文案（src/transcript.js 为活模块，断言原样保留） ----
assert.equal(subagentLine({ type: 'user_message' }), null, '非子代理事件不渲染');
assert.equal(subagentLine({ type: 'subagent_started' }), null, 'started 由 status 行覆盖，不重复渲染');
const textOf = (spans) => (spans || []).map(s => s.t).join('');
const running = subagentLine({ type: 'subagent_status', timestamp: '2026-09-20T10:00:05+0800',
  payload: { child: 'c1', agent: 'explore', status: 'running', steps: 0, tokens: 0 } });
assert.ok(textOf(running).includes('10:00:05'), '带时间戳');
assert.ok(textOf(running).includes('◆'), '运行中用 ◆ 记号（与流程事件一致）');
assert.ok(textOf(running).includes('子代理 explore 运行中…'));
const done = subagentLine({ type: 'subagent_status', payload: { child: 'c1', agent: 'explore', status: 'done', steps: 3, tokens: 120 } });
assert.ok(textOf(done).includes('✔ explore 完成（3 步/120 tokens）'));
const failed = subagentLine({ type: 'subagent_status', payload: { child: 'c2', agent: 'critic', status: 'error', steps: 1, tokens: 40 } });
assert.ok(textOf(failed).includes('✘ critic 失败'));
assert.ok(failed.some(s => s.c === 'red') && done.some(s => s.c === 'green'), '失败红 / 完成绿，与事件行配色一致');

// ---- readSessionEvents：fixture / 缺失 / 非法 ID ----
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'gezhi-subagent-'));
process.env.GEZHI_DATA_DIR = dataDir;
process.once('exit', () => { try { fs.rmSync(dataDir, { recursive: true, force: true }); } catch {} });
fs.mkdirSync(path.join(dataDir, 'sessions'), { recursive: true });
const events = [
  { type: 'user_message', timestamp: '2026-09-20T10:00:00+0800', payload: { text: '调研' } },
  { type: 'subagent_status', timestamp: '2026-09-20T10:00:05+0800',
    payload: { child: 'c1', agent: 'explore', status: 'running', steps: 0, tokens: 0 } },
  { type: 'subagent_status', timestamp: '2026-09-20T10:00:20+0800',
    payload: { child: 'c1', agent: 'explore', status: 'done', steps: 3, tokens: 120 } },
  { type: 'subagent_status', timestamp: '2026-09-20T10:00:21+0800',
    payload: { child: 'c2', agent: 'critic', status: 'error', steps: 1, tokens: 40 } },
];
fs.writeFileSync(path.join(dataDir, 'sessions', 'agent-fixture.jsonl'),
  events.map(e => JSON.stringify(e)).join('\n') + '\n', 'utf-8');
assert.equal(readSessionEvents(dataDir, 'agent-fixture').length, 4);
assert.deepEqual(readSessionEvents(dataDir, 'missing'), [], '缺失文件 → []');
assert.deepEqual(readSessionEvents(dataDir, '../escape'), [], '非法 ID 拒绝读取');

// ---- 端到端：OpenTUI 事件流里子代理行进会话转写区（renderEvents subagent_status 对标） ----
export const captureSubagentFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  let journalDone = false, finished = false;
  Bridge.prototype.call = async function (request) {
    t.calls.push(request); // 包装层也要记账：夹具只在 originalCall 里 push
    if (request.cmd === 'agent_launch') return { ok: true, result: { session_id: 'agent-fixture', status: 'running', launch_pending: true } };
    if (request.cmd === 'agent_launch_status') {
      const answer = () => ({ ok: true, finished,
        snapshot: { session_id: 'agent-fixture', status: journalDone ? 'done' : 'running' },
        result: finished ? { session_id: 'agent-fixture', status: 'done', answer: 'fixture 完成' } : null });
      if (t.hold === 'agent_launch_status') return new Promise(resolve => t.held.push({ request, resolve, answer }));
      return answer();
    }
    if (request.cmd === 'session_snapshot') return { ok: true, snapshot: { session_id: request.session_id, status: 'done' } };
    if (request.cmd === 'session_events') {
      const rows = (journalDone ? events : events.slice(0, 2)).filter(e => e.type !== 'user_message')
        .map(e => ({ kind: e.type, payload: e.payload, timestamp: e.timestamp }));
      if (journalDone) rows.push({ kind: 'assistant_message', payload: { text: 'fixture 完成' } });
      return { ok: true, events: rows, cursor: journalDone ? 'last' : 'first', reset: false, has_more: false };
    }
    return originalCall.call(this, request);
  };
  try {
    t.hold = 'agent_launch_status';
    await t.command('调研问题');
    await t.wait(() => t.frame().includes('子代理 explore 运行中…'), 'subagent running line');
    assert.ok(!t.frame().includes('fixture 完成'), 'running progress renders before the parent finishes');
    assert.equal(t.calls.filter(r => r.cmd === 'agent_launch').length, 1, 'launch exactly once');
    assert.equal(t.calls.filter(r => r.cmd === 'agent_run').length, 0, 'never fall back to duplicate execution');
    journalDone = true; finished = true;
    const held = t.held.splice(0); t.hold = '';
    held.forEach(item => item.resolve(item.answer()));
    await t.wait(() => t.frame().includes('fixture 完成'), 'agent final answer');
    await t.wait(() => t.frame().includes('✔ explore 完成（3 步/120 tokens）'), 'subagent done line');
    await t.wait(() => t.frame().includes('✘ critic 失败'), 'subagent error line');
  } finally {
    t.held.forEach(item => item.resolve(item.answer()));
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureSubagentFrames();
  console.log(`subagent progress lines (pure + OpenTUI transcript rendering) passed (${result.frames.length} frames)`);
  process.exit(0);
}
