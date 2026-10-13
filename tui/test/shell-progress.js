import assert from 'node:assert/strict';
import { newAgentTranscript, reduceAgentEvents, mergeAgentLines } from '../src/transcript.js';

const event = (type, payload = {}) => ({ type, payload });
const call = (id, name = 'shell.run') => event('tool_call', { id, name, arguments: { command: 'fixture' } });
const output = (id, text, stream = 'stdout') => event('shell_output', { tool_call_id: id, text, stream, partial: true });
const result = (id, reply = { ok: true, exit_code: 0 }) => event('tool_result', { id, name: 'shell.run', result: reply });
let state = newAgentTranscript(), lines = [];
const apply = (events, reset = false) => {
  const reduced = reduceAgentEvents(state, events, { reset });
  state = reduced.state;
  lines = mergeAgentLines(lines, reduced.changes, { sessionKey: 'shell-fixture', reset, width: 60 });
  return reduced;
};
const text = () => lines.map(row => row.map(span => span.t).join('')).join('\n');

apply([event('user_message'), call('one')]);
assert.match(text(), /shell\.run.*执行中/, 'tool call has a running block before any output');
const initialBlock = lines[0][0].agentBlock;
const first = apply([output('one', 'early output\n')]);
assert.match(text(), /early output/, 'shell output is visible before tool_result');
assert.match(text(), /执行中/, 'progress never claims completion');
assert.equal(first.changes[0].id, initialBlock, 'progress updates the existing tool block');
assert.equal(state.latest, null, 'tool progress is not an assistant message');
apply([output('one', 'warning output\n', 'stderr')]);
assert.match(text(), /early output/);
assert.match(text(), /warning output/);
apply([result('one')]);
assert.match(text(), /shell\.run.*已完成/);
assert.ok(!text().includes('执行中'));
assert.equal((text().match(/early output/g) || []).length, 1);
const settled = lines;
assert.deepEqual(apply([output('one', 'too late\n')]).changes, []);
assert.equal(lines, settled, 'output after final result cannot alter a completed tool');

apply([call('two'), call('three'), output('two', 'two-only\n'), output('three', 'three-only\n')]);
const live = Object.values(state.tools).filter(tool => !tool.complete);
assert.equal(live.length, 2);
assert.ok(live.some(tool => tool.detail.includes('two-only') && !tool.detail.includes('three-only')));
assert.ok(live.some(tool => tool.detail.includes('three-only') && !tool.detail.includes('two-only')));
const before = JSON.stringify(state);
const next = reduceAgentEvents(state, [output('two', 'next\n')]);
assert.equal(JSON.stringify(state), before, 'the reducer never mutates previous nested tool state');
assert.ok(Object.values(next.state.tools).some(tool => tool.detail.includes('next')));

apply([event('user_message'), output('two', 'orphan from previous turn\n')]);
assert.ok(!text().includes('orphan from previous turn'), 'a missing current-turn call is ignored');
apply([call('two'), output('two', 'new-turn-only\n')]);
const reused = Object.values(state.tools).filter(tool => tool.detail.includes('new-turn-only'));
assert.equal(reused.length, 1);
assert.ok(!reused[0].detail.includes('two-only'), 'reused call IDs never inherit another turn output');
apply([result('two', { ok: true, exit_code: 9 })]);
assert.match(text(), /退出码 9/, 'nonzero shell exits are failures even with legacy ok=true');

const orphan = reduceAgentEvents(newAgentTranscript(), [output('missing', 'tail fragment\n'), result('missing')]);
assert.deepEqual(orphan.changes, [], 'a tail page without tool_call does not invent a tool or assistant');
apply([call('safe'), output('safe', '\x1b[31mred\x1b[0m\x1b]0;owned\x07\napi_key=hidden-fixture\n')]);
assert.ok(!text().includes('\x1b') && !text().includes('\x07'), 'ANSI/OSC payloads cannot control the terminal');
assert.ok(!text().includes('hidden-fixture'));
assert.ok(!text().includes('owned'));
assert.match(text(), /red/);

apply([call('bounded'), output('bounded', 'old-prefix\n' + 'x'.repeat(16000) + '\nretained-tail\n')]);
const bounded = Object.values(state.tools).find(tool => tool.detail.includes('retained-tail'));
assert.ok(bounded.detail.length <= 1000, 'each call keeps at most 1,000 characters');
assert.ok(!bounded.detail.includes('old-prefix'));
for (let index = 0; index < 50; index++) apply([call(`bounded-${index}`), output(`bounded-${index}`, `${index}\n`)]);
assert.ok(Object.keys(state.tools).length <= 32, 'tool index is bounded for long sessions');
apply([output('bounded', 'evicted-index\n')]);
assert.ok(!text().includes('evicted-index'));

const reset = apply([output('safe', 'stale after reset\n')], true);
assert.deepEqual(reset.changes, []);
assert.equal(text(), '');
console.log('Shell progress: early output, final state, call/turn isolation, tail safety, ANSI/redaction and bounds passed');
