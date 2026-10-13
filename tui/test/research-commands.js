import assert from 'node:assert/strict';
import { runWorkbenchCommand, safeCommandEcho, redactSettings } from '../src/workbench-commands.js';
import { parseCommand } from '../src/commands.js';

const calls = [], output = [], reports = [];
let failSave = false;
const secret = 'fixture-sensitive-key';
const ctx = { taskId: 'task-evidence', currentAgentSession: 'session-current',
  bridge: { connection: () => ({ mode: 'local' }), async call(request) {
    calls.push(request);
    if (failSave) return { ok: false, error: `invalid API key ${secret}` };
    return { ok: true, registry: {
      connections: [{ id: 'lab', protocol: 'responses', base_url: 'https://example.invalid/v1', api_key: secret, has_api_key: true }],
      profiles: [{ id: 'research', connection_id: 'lab', model: 'fixture-model', reasoning_effort: 'high', max_output_tokens: 8192 }],
      routes: { review: 'research' }, resolved: { default: { profile_id: 'legacy', model: 'legacy-fixture' } },
    }, capabilities: { protocols: ['responses', 'anthropic_messages', 'gemini'] },
    settings: { llm_reasoning_effort: 'high', llm_thinking_style: 'auto', llm_thinking_budget: 4096 },
    text: '连接 lab / fixture-model\n缓存读取：未报送 · 命中率：未知',
    report: '# 科研审计\n证据不足，待复核。', analysis: { supported: 0, unresolved: 2 }, path: '/tmp/audit.md',
    selection: { stage: 'review', selected: ['evidence-review'] },
    result: { session_id: request.session_id, status: request.cmd === 'agent_cancel' ? 'cancelled' : 'waiting_human',
      pending: request.cmd === 'agent_cancel' ? undefined : { id: 'call-2', tool: 'search', capability: 'network' } },
  }; } }, out: text => output.push(text), openReport: (title, body) => reports.push({ title, body }),
  onSettings: () => {}, setCurrentAgentSession: id => { ctx.currentAgentSession = id; },
};
const run = (name, args = '') => runWorkbenchCommand(name, args, ctx);
assert.equal(await run('connections'), true, 'connections command must be implemented');
assert.equal(calls.at(-1).cmd, 'model_registry');
assert.ok(!output.join('\n').includes(secret));
await run('models'); assert.ok(output.some(line => line.includes('8192')));
for (const protocol of ['responses', 'anthropic_messages', 'gemini', 'chat_completions', 'ollama']) {
  await run('model', `protocol ${protocol}`);
  assert.deepEqual(calls.at(-1), { cmd: 'settings_save', settings: { llm_protocol: protocol } });
}
for (const [command, input, setting, value] of [
  ['model', 'reasoning high', 'llm_reasoning_effort', 'high'],
  ['model', 'thinking anthropic_budget', 'llm_thinking_style', 'anthropic_budget'],
  ['model', 'budget 4096', 'llm_thinking_budget', 4096], ['reasoning', 'off', 'llm_reasoning_effort', 'off'],
]) { await run(command, input); assert.deepEqual(calls.at(-1).settings, { [setting]: value }); }
let count = calls.length;
for (const input of ['protocol unsupported', 'reasoning unexpected', 'budget -1', 'budget 1.5']) await run('model', input);
assert.equal(calls.length, count, 'invalid model parameters do not reach backend');
await run('reasoning'); assert.ok(output.some(line => line.includes('high')));
await run('model', 'capabilities'); assert.equal(calls.at(-1).cmd, 'model_capabilities');
const connection = { id: 'lab', protocol: 'responses', base_url: 'https://example.invalid', api_key: secret };
await run('connection', `save ${JSON.stringify(connection)}`);
assert.deepEqual(calls.at(-1), { cmd: 'model_connection_save', connection });
await run('connection', 'delete lab'); assert.deepEqual(calls.at(-1), { cmd: 'model_connection_delete', id: 'lab' });
const profile = { id: 'research', connection_id: 'lab', model: 'fixture-model', thinking_style: 'auto', max_output_tokens: 8192 };
await run('profile', `save ${JSON.stringify(profile)}`); assert.deepEqual(calls.at(-1), { cmd: 'model_profile_save', profile });
await run('profile', 'delete research'); assert.deepEqual(calls.at(-1), { cmd: 'model_profile_delete', id: 'research' });
await run('route', 'review research'); assert.deepEqual(calls.at(-1), { cmd: 'model_route_set', stage: 'review', profile_id: 'research' });
await run('skills-selection', 'review'); assert.deepEqual(calls.at(-1), { cmd: 'skills_selection', stage: 'review' });
await run('skills-selection', 'literature'); assert.deepEqual(calls.at(-1), { cmd: 'skills_selection', stage: 'literature' });
await run('usage'); assert.ok(output.some(line => line.includes('命中率：未知'))); assert.ok(!output.join('\n').includes('0%'));
await run('audit', 'export'); assert.deepEqual(calls.at(-1), { cmd: 'research_audit', task_id: 'task-evidence', export: true });
assert.ok(reports.at(-1).body.includes('证据不足')); assert.ok(reports.at(-1).body.includes('/tmp/audit.md'));
await run('continue'); assert.deepEqual(calls.at(-1), { cmd: 'agent_continue', session_id: 'session-current' });
assert.ok(output.some(line => line.includes('/approve session-current call-2')));
await run('cancel-agent', 'session-other'); assert.deepEqual(calls.at(-1), { cmd: 'agent_cancel', session_id: 'session-other' });
assert.equal(ctx.currentAgentSession, 'session-other');
assert.ok(!safeCommandEcho(`/connection save ${JSON.stringify(connection)}`).includes(secret));
assert.ok(!safeCommandEcho(`/profile save {"password":"${secret}"}`).includes(secret));
count = calls.length; await run('connection', `save {"api_key":"${secret}"`); assert.equal(calls.length, count);
assert.ok(!output.join('\n').includes(secret), 'invalid JSON must not echo credential fragments');
failSave = true;
await assert.rejects(run('connection', `save ${JSON.stringify(connection)}`), error => !error.message.includes(secret));
failSave = false;
ctx.bridge.connection = () => ({ mode: 'remote' });
count = calls.length; await run('connection', `save ${JSON.stringify(connection)}`); await run('model', 'protocol responses');
assert.equal(calls.length, count, 'remote model writes are rejected locally without transmitting credentials');
assert.ok(output.at(-1).includes('主机'));
assert.deepEqual(redactSettings({ max_output_tokens: 8192, cache_read_tokens: null, total_tokens: 90, api_key: secret }),
  { max_output_tokens: 8192, cache_read_tokens: null, total_tokens: 90, api_key: '[已设置，隐藏]' });
assert.deepEqual(redactSettings({ input_tokens_details: { cached_tokens: null }, accessToken: secret }),
  { input_tokens_details: { cached_tokens: null }, accessToken: '[已设置，隐藏]' });
assert.equal(parseCommand('/connection save {"label":"Lab  two"}').args, 'save {"label":"Lab  two"}');
console.log('Research model protocols/profiles/routes, secret redaction, unknown cache, audit and session continuation passed');
