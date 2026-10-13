import assert from 'node:assert/strict';
import { runFeatureCommand, FEATURE_COMMANDS } from '../src/feature-commands.js';
import { safeCommandEcho } from '../src/sensitive-text.js';

const requests = [], lines = [];
const context = { taskId: 'research1', out: line => lines.push(line), setCurrentAgentSession: sid => { context.sid = sid; },
  bridge: { connection: () => ({ mode: 'local' }), call: async req => { requests.push(req); return { ok: true, text: '已保存' }; } } };
const json = '{"title":"方法 A", "content":"保留 \\\"引号\\\""}';
assert.equal(await runFeatureCommand('memory', 'save ' + json, context), true);
assert.equal(requests[0].text, '/memory save ' + json);
assert.equal(requests[0].task_id, 'research1');
assert.equal(await runFeatureCommand('unknown', '', context), false);
context.bridge.call = async () => ({ ok: true, result: { session_id: 'abc', status: 'waiting_approval', content: '' } });
await runFeatureCommand('run', 'review-method data', context);
assert.equal(context.sid, 'abc');
context.bridge.call = async () => ({ ok: true, path: '/exports/abc.html', format: 'html', session_id: 'abc' });
await runFeatureCommand('chat', 'share abc html', context);
assert.ok(lines.some(line => line.includes('/exports/abc.html')));
for (const name of ['project', 'chat', 'todo', 'schedule', 'notifications', 'handoff']) {
  assert.ok(FEATURE_COMMANDS.some(c => c.name === name));
}
context.bridge.connection = () => ({ mode: 'remote' });
context.bridge.call = async () => { throw new Error('remote must not send config'); };
assert.equal(await runFeatureCommand('mcp-config', 'save {"env":{"X":"private"}}', context), true);
assert.ok(!safeCommandEcho('/mcp-config save {"env":{"X":"private"}}').includes('private'));
assert.ok(!safeCommandEcho('/browser call type {"text":"private"}').includes('private'));
assert.ok(FEATURE_COMMANDS.some(c => c.name === 'notebook'));
console.log('Shared feature command dispatch, JSON fidelity, session selection and credential masking passed');
