import assert from 'node:assert/strict';
import { runWorkbenchCommand } from '../src/workbench-commands.js';
import { reduceAgentEvents, newAgentTranscript } from '../src/transcript.js';
const calls = [], output = [];
let fail = false, turn = 'turn-one', remote = false;
const context = { currentAgentSession: 'one', out: value => output.push(value), bridge: {
  connection: () => ({ mode: remote ? 'remote' : 'local' }),
  call: async request => {
    calls.push(request);
    const steering = { session_id: 'one', turn_id: turn, enabled: true, state: 'open', status: 'running', items: [] };
    if (request.cmd === 'steer_status') return { ok: true, steering };
    if (fail) return { ok: true, steering };
    return { ok: true, steering, ticket: { id: 'ticket', state: 'pending', request_id: request.steer_request_id } };
  } } };
const run = args => runWorkbenchCommand('steer', args, context);
await run('status');
assert.ok(output.some(value => value.includes('turn-one')));
fail = true;
await assert.rejects(run('add 核对  反例'), /回执/);
const first = calls.at(-1);
assert.equal(first.request, '核对  反例');
fail = false;
await run('add 核对  反例');
assert.equal(calls.at(-1).steer_request_id, first.steer_request_id);
fail = true;
await assert.rejects(run('add 未确认'), /回执/);
turn = 'turn-two';
await assert.rejects(run('add 未确认'), /回合已改变/);
assert.equal(calls.at(-1).cmd, 'steer_status', 'old retry must not be resubmitted to a new turn');
remote = true;
const before = calls.length;
await run('enable');
assert.equal(calls.length, before);
const rendered = reduceAgentEvents(newAgentTranscript(), [{ type: 'steer_applied', payload: { steer_id: 's', text: '反例' } }]);
assert.equal(rendered.state.turn, 0, 'steer must not create a user-turn boundary');
assert.ok(rendered.changes[0].text.includes('反例'));
console.log('Steering expected-turn identity, unknown acknowledgement retry, remote opt-in and transcript boundary passed');
