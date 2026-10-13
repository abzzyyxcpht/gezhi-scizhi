import assert from 'node:assert/strict';
import { runWorkbenchCommand } from '../src/workbench-commands.js';
const calls = [], reports = [];
const context = { currentAgentSession: 'one', out: text => reports.push(text), openReport: (_title, text) => reports.push(text),
  bridge: { connection: () => ({ mode: 'remote', url: 'http://example.invalid' }), call: async request => {
    calls.push(request);
    if (request.cmd === 'session_history_message') return { ok: true, message: { session_id: request.session_id, message_id: request.message_id,
      generation: 'generation', offset: request.offset, content: 'full text', characters: 9, next_offset: null, kind: 'steer_applied' } };
    return { ok: true, history: { session_id: request.session_id, query: request.query, page: request.page || 3, pages: 3, total: 95,
      snapshot: 'snapshot-' + request.session_id, generation: 'generation', new_messages: false, reset: false,
      messages: [{ id: 81, role: 'user', kind: 'steer_applied', content: 'api_key=secret-value', truncated: true, characters: 40000 }] } };
  } } };
const run = args => runWorkbenchCommand('history', args, context);
await run('');
assert.equal(calls.at(-1).cmd, 'session_history');
assert.ok(!reports.at(-1).includes('secret-value') && reports.at(-1).includes('分段查看'));
await run('2');
assert.equal(calls.at(-1).snapshot, 'snapshot-one');
await run('反例');
assert.equal(calls.at(-1).snapshot, undefined);
await run('1');
assert.equal(calls.at(-1).query, '反例');
await run('search 23');
assert.equal(calls.at(-1).query, '23', 'numeric search must remain distinct from a page number');
await run('message 81');
assert.equal(calls.at(-1).generation, 'generation');
context.currentAgentSession = 'two';
await run('2');
assert.equal(calls.at(-1).snapshot, undefined, 'different session never reuses another snapshot');
assert.ok(calls.every(request => request.cmd.startsWith('session_history')));
console.log('History shared pagination, query snapshot scope, long-message chunks and redaction passed');
