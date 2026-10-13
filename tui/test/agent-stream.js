import assert from 'node:assert/strict';
import * as transcript from '../src/transcript.js';

assert.equal(typeof transcript.readEventPages, 'function', 'incremental event paging must be available');
assert.equal(typeof transcript.reduceAgentEvents, 'function', 'assistant event coalescing must be available');
const { readEventPages, reduceAgentEvents, reduceAgentResult, mergeAgentLines, newAgentTranscript } = transcript;
const delta = (text, step = 1) => ({ type: 'assistant_delta', payload: { step, delta: text } });
const final = (text, step = 1) => ({ type: 'assistant_message', payload: { step, content: text } });
let state = newAgentTranscript(), lines = [];
function apply(events, reset = false) {
  const result = reduceAgentEvents(state, events, { reset });
  state = result.state;
  lines = mergeAgentLines(lines, result.changes, { sessionKey: 'local/session', reset, width: 60 });
}
const text = () => lines.map(line => line.map(span => span.t).join('')).join('\n');
apply([{ type: 'user_message' }, delta('first'), delta(' part')]);
assert.equal(text(), 'first part');
apply([final('first final')]);
assert.equal(text(), 'first final', 'final replaces the complete in-flight block');
const stable = lines;
apply([]);
assert.equal(lines, stable, 'empty event pages never redraw transcript');
apply([{ type: 'user_message' }, delta('second')]);
assert.equal(text(), 'first final\nsecond', 'step numbers reset between user turns');
const result = reduceAgentResult(state, { content: 'second final' });
state = result.state;
lines = mergeAgentLines(lines, result.changes, { sessionKey: 'local/session', width: 60 });
apply([delta(' final'), final('second final')]);
assert.equal(text(), 'first final\nsecond final', 'result fallback and delayed final event remain one block');
apply([{ type: 'user_message' }, final('replacement')], true);
assert.equal(text(), 'replacement', 'rewind reset removes stale session blocks');
const early = reduceAgentResult(newAgentTranscript(), { answer: 'already complete' });
const replay = reduceAgentEvents(early.state, [{ type: 'user_message' }, delta('already '), final('already complete')]);
const earlyLines = mergeAgentLines([], early.changes, { sessionKey: 'early', width: 60 });
const replayLines = mergeAgentLines(earlyLines, replay.changes, { sessionKey: 'early', width: 60 });
assert.equal(replayLines.flat().map(span => span.t).join(''), 'already complete', 'fast final followed by journal replay does not duplicate');
for (const status of ['waiting_approval', 'waiting_task', 'waiting_children', 'paused', 'cancelled']) {
  const row = transcript.subagentLine({ type: 'subagent_status', payload: { agent: 'critic', status } });
  assert.ok(!row.some(span => span.t.includes('失败')), `${status} must not render as failure`);
}
const calls = [];
const bridge = { call: async request => {
  calls.push(request);
  return { ok: true, events: [delta(String(calls.length))], cursor: `opaque-${calls.length}`, has_more: true, reset: false };
} };
const page = await readEventPages(bridge, 'session', null);
assert.equal(calls.length, 4, 'one monitoring tick has a bounded catch-up budget');
assert.equal(calls[0].cursor, null);
assert.equal(calls[0].tail, true);
assert.equal(calls[0].limit, 200);
assert.equal(calls[1].cursor, 'opaque-1');
assert.equal(page.cursor, 'opaque-4');
assert.equal(page.has_more, true);
await assert.rejects(readEventPages({ call: async () => { throw new Error('connection reset'); } }, 'session', 'unchanged'), /connection reset/);
const unsupported = await readEventPages({ call: async () => ({ ok: false, error: '未知命令：session_events' }) }, 'session', null);
assert.equal(unsupported.unsupported, true);
let reads = 0;
const reset = await readEventPages({ call: async () => ++reads === 1
  ? { ok: true, events: [delta('discard')], cursor: 'old', has_more: true, reset: false }
  : { ok: true, events: [final('current')], cursor: 'new', has_more: false, reset: true } }, 'session', 'before');
assert.equal(reset.reset, true);
assert.equal(reset.events.length, 1, 'reset during catch-up discards prior generation pages');
console.log('Agent stream coalescing, final reconciliation, reset, no-op, waiting states and bounded opaque paging passed');
