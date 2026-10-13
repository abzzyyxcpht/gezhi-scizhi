import assert from 'node:assert/strict';
import { runWorkbenchCommand } from '../src/workbench-commands.js';
import { COMMANDS } from '../src/commands.js';

const calls = [], output = [], refreshed = [];
let fail = false, changed = true, minimal = false;
const state = {
  session_id: 'current', status: 'paused', agent: 'research', mode: 'agent', driver: 'adaptive',
  model: { model: 'fixture-model', protocol: 'responses' },
  context: { tokens: 1200, limit_tokens: 8000, measurement: 'heuristic_tokens', compressions: 2, messages: 15 },
  budget: { token_limit: 10000, remaining_tokens: 9600, model_calls: 3, max_steps: 12, remaining_steps: 9 },
  tokens: 400, usage_complete: false, session_usage: { tokens: 900, model_calls: 7, usage_complete: false },
  waiting_reason: '等待用户继续', can_compact: true, compact_unavailable_reason: '', updated_at: '2026-09-27T12:00:00Z',
};
const context = {
  currentAgentSession: 'current',
  bridge: { connection: () => ({ mode: 'remote' }), call: async request => {
    calls.push(request);
    if (fail) return { ok: false, error: '会话正在运行，不能压缩' };
    return { ok: true, status: minimal ? { session_id: request.session_id, status: 'done', driver: 'planned', model: {},
      context: null, budget: null, tokens: null, usage_complete: null, session_usage: null,
      can_compact: false, compact_unavailable_reason: '旧会话没有模型执行状态' } : { ...state, session_id: request.session_id },
      result: { session_id: request.session_id, changed, before_tokens: 1200, after_tokens: changed ? 700 : 1200,
        removed_messages: changed ? 6 : 0, method: 'deterministic', model_calls: 0,
        message: changed ? '已保留目标和近期对话' : '没有可缩减的历史' } };
  } }, out: text => output.push(text), refreshSessionStatus: id => refreshed.push(id),
  setCurrentAgentSession: () => assert.fail('status and compaction never select a different session'),
  onAgentContent: () => assert.fail('status and compaction never replay assistant content'),
};
const run = (name, args = '') => runWorkbenchCommand(name, args, context);
assert.equal(await run('session-status'), true, 'session-status command must be implemented');
assert.deepEqual(calls.at(-1), { cmd: 'session_status', session_id: 'current' });
let text = output.join('\n');
for (const fragment of ['fixture-model', 'responses', '1200/8000', '估算 token', '本轮', '400', '9600', '9', '会话累计', '900', '不完整', '等待用户继续']) assert.ok(text.includes(fragment), fragment);
assert.ok(!text.includes('字节'), 'estimated context is not byte or billed-token accounting');
await run('session-status', 'historic');
assert.equal(calls.at(-1).session_id, 'historic');
assert.equal(refreshed.length, 0, 'read-only status never refreshes execution selection');
await run('compact');
assert.deepEqual(calls.at(-1), { cmd: 'session_compact', session_id: 'current' });
assert.deepEqual(refreshed, ['current']);
assert.ok(output.some(line => line.includes('1200') && line.includes('700')));
assert.ok(output.some(line => line.includes('模型调用 0')));
await run('compact', 'historic');
assert.deepEqual(refreshed, ['current'], 'compacting another session does not poke the active session');
changed = false;
output.length = 0;
await run('compact');
assert.ok(output.join('\n').includes('没有可缩减的历史'));
assert.ok(!output.some(line => line.includes('已压缩')), 'no-op never claims a successful reduction');
minimal = true;
output.length = 0;
await run('session-status', 'old-session');
text = output.join('\n');
assert.ok(text.includes('未记录'));
assert.ok(text.includes('旧会话没有模型执行状态'));
assert.ok(!/\bNaN\b|undefined|null|0\/0/.test(text), 'missing measurements remain unknown');
fail = true;
const before = refreshed.length;
await assert.rejects(run('compact'), /会话正在运行/);
assert.equal(refreshed.length, before, 'failed mutation never triggers success refresh');
const count = calls.length;
await run('session-status', 'one two');
await run('compact', 'one two');
context.currentAgentSession = null;
await run('session-status'); await run('compact');
assert.equal(calls.length, count, 'invalid or missing selection does not call the bridge');
for (const name of ['session-status', 'compact']) assert.ok(COMMANDS.some(command => command.name === name), `${name} registered for help/completion`);
assert.ok(COMMANDS.some(command => command.name === 'status' && command.desc.includes('任务')), 'workflow status stays intact');
console.log('Session status/compact protocol, scopes, unknown usage, no-op, errors and refresh isolation passed');
