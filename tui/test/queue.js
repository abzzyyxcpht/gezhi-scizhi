import assert from 'node:assert/strict';
import { runWorkbenchCommand, featureKeys } from '../src/workbench-commands.js';
import { COMMANDS } from '../src/commands.js';
import { queueCanRunWhileBusy } from '../src/queue-commands.js';

const calls = [], output = [];
let fail = false, remote = false, emptyAck = false, resolvedRequestId;
const queue = { session_id: 'current', enabled: true, paused: true, pause_reason: '重启后需要确认', resume_required: true,
  worker_active: null, revision: 3, history_truncated: true, total_items: 70, counts: { queued: 1, needs_review: 1 }, items: [
    { id: 'ticket-a', state: 'queued', request: '核对支持材料', mode: 'agent', agent: 'research', offline: true },
    { id: 'ticket-b', state: 'needs_review', request: '复核条件', error: '需要重新核对' },
  ] };
const context = { currentAgentSession: 'current', queueRequestIds: new Map(),
  bridge: { connection: () => ({ mode: remote ? 'remote' : 'local', url: remote ? 'http://host' : '' }), call: async request => {
    calls.push(request);
    if (fail) throw new Error('fixture timeout');
    if (emptyAck) return { ok: true };
    return { ok: true, queue, ...(request.cmd === 'queue_add' ? { ticket: { id: 'ticket-a', request_id: request.queue_request_id, state: 'queued' } }
      : resolvedRequestId && ['queue_discard', 'queue_remove'].includes(request.cmd) ? { ticket: { id: request.ticket_id, request_id: resolvedRequestId, state: 'removed' } } : {}) };
  } }, out: text => output.push(text), onSettings: () => {},
  setCurrentAgentSession: () => assert.fail('queue actions must not replace selection'),
};
const run = args => runWorkbenchCommand('queue', args, context);
assert.equal(queueCanRunWhileBusy('disable'), true, 'only the explicit queue feature control stays reachable during a turn');
assert.equal(queueCanRunWhileBusy('enable'), true);
assert.equal(await run(''), true, '/queue must be implemented');
assert.deepEqual(calls.at(-1), { cmd: 'queue_list', session_id: 'current' });
assert.ok(output.join('\n').includes('重启后需要确认'));
assert.ok(output.join('\n').includes('下一轮'));
assert.ok(output.join('\n').includes('较早历史'), 'bounded server history must not be described as the complete journal');
assert.ok(output.join('\n').includes('未记录'), 'unknown worker liveness must not be declared idle');
fail = true;
await assert.rejects(run('add 核验  保留空格'), /timeout/);
const first = calls.at(-1);
assert.equal(first.request, '核验  保留空格');
assert.ok(first.queue_request_id);
assert.equal(first.request_id, undefined, 'business identity must not use the RPC correlation field');
fail = false;
await run('add 核验  保留空格');
assert.equal(calls.at(-1).queue_request_id, first.queue_request_id, 'retry after uncertain delivery preserves identity');
await run('add 核验  保留空格');
assert.notEqual(calls.at(-1).queue_request_id, first.queue_request_id, 'a later confirmed new submission gets a new identity');
emptyAck = true;
await assert.rejects(run('add missing acknowledgement'), /回执|确认/);
const uncertainId = calls.at(-1).queue_request_id;
emptyAck = false;
await run('add missing acknowledgement');
assert.equal(calls.at(-1).queue_request_id, uncertainId, 'empty ok response never discards the idempotency key');
for (const action of ['remove', 'discard']) {
  emptyAck = true;
  await assert.rejects(run(`add resolved by ${action}`), /回执|确认/);
  resolvedRequestId = calls.at(-1).queue_request_id;
  emptyAck = false;
  await run(`${action} ticket-a`);
  await run(`add resolved by ${action}`);
  assert.notEqual(calls.at(-1).queue_request_id, resolvedRequestId, 'explicitly resolved ticket permits a fresh same-text submission');
  resolvedRequestId = undefined;
}
for (const [args, cmd, ticket] of [['pause', 'queue_pause'], ['resume', 'queue_resume'],
  ['remove ticket-a', 'queue_remove', 'ticket-a'], ['discard ticket-b', 'queue_discard', 'ticket-b']]) {
  await run(args);
  assert.deepEqual(calls.at(-1), { cmd, session_id: 'current', ...(ticket ? { ticket_id: ticket } : {}) });
}
await run('reorder ticket-b ticket-a');
assert.deepEqual(calls.at(-1), { cmd: 'queue_reorder', session_id: 'current', ordered_ids: ['ticket-b', 'ticket-a'] });
await run('enable');
assert.deepEqual(calls.at(-1), { cmd: 'settings_save', settings: { experimental_message_queue: true } });
await run('disable');
assert.deepEqual(calls.at(-1).settings, { experimental_message_queue: false });
remote = true;
let count = calls.length;
await run('enable'); await run('disable');
assert.equal(calls.length, count, 'remote cannot change the host experiment switch');
assert.ok(output.at(-1).includes('主机'));
await run('list'); assert.equal(calls.at(-1).cmd, 'queue_list');
count = calls.length;
for (const invalid of ['add', 'pause extra', 'remove', 'discard a b', 'unknown']) await run(invalid);
assert.equal(calls.length, count);
context.currentAgentSession = null;
await run('add missing session');
assert.equal(calls.length, count);
assert.ok(featureKeys.includes('experimental_message_queue'));
assert.ok(COMMANDS.some(row => row.name === 'queue'));
console.log('Queue explicit scope, pause/review labels, RPC-safe idempotency, retry and local-only flag contracts passed');
