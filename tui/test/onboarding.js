import assert from 'node:assert/strict';
import { runWorkbenchCommand } from '../src/workbench-commands.js';
import { COMMANDS } from '../src/commands.js';

const calls = [], reports = [], output = [], opened = [];
let remote = false, fail = false, offline = true;
const readiness = { version: '0.6.0', agent_mode: 'rules',
  model: { status: 'not_configured', model: null, protocol: null, connection_verified: false, execution_ready: false,
    reason: '模型尚未配置，可先离线体验', api_key: 'never-display-secret', base_url: 'https://private.invalid' },
  research: { default_offline: false, offline_available: true, notice: '离线样例使用内置合成文献' },
  checks: [{ id: 'dependencies', title: '基础依赖', status: 'ready', detail: '可用' }],
  suggestions: { offline_command: '/demo', example_request: '检索研究证据', settings_command: '/models' } };
const context = { bridge: { connection: () => ({ mode: remote ? 'remote' : 'local' }), call: async request => {
  calls.push(request);
  if (fail) return { ok: false, error: '引擎不可用' };
  return request.cmd === 'product_readiness' ? { ok: true, readiness }
    : { ok: true, task_id: 'demo-task', workflow_id: 'literature_scan', offline,
      notice: '内置合成样例，仅供体验；不可作为真实科研证据。' };
} }, out: text => output.push(text), openReport: (title, content) => reports.push({ title, content }),
  openTask: id => opened.push(id) };
const run = (name, args = '') => runWorkbenchCommand(name, args, context);
assert.equal(await run('setup'), true, '/setup must be implemented');
assert.deepEqual(calls, [{ cmd: 'product_readiness' }], 'setup performs one read-only readiness request');
assert.equal(opened.length, 0, 'setup never starts a task');
for (const phrase of ['0.6.0', '未配置', '未验证连接', '/demo', '/models', '/model', '基础依赖']) assert.ok(reports.at(-1).content.includes(phrase), phrase);
assert.ok(!reports.at(-1).content.includes('never-display-secret'));
assert.ok(!reports.at(-1).content.includes('private.invalid'));
remote = true;
readiness.model.status = 'configured'; readiness.model.model = 'configured-model'; readiness.model.execution_ready = true;
await run('setup');
assert.ok(reports.at(-1).content.includes('主机'));
assert.ok(reports.at(-1).content.includes('未验证连接'), 'configured is never presented as connectivity-verified');
await run('demo');
assert.deepEqual(calls.at(-1), { cmd: 'product_demo' });
assert.deepEqual(opened, ['demo-task']);
assert.ok(output.join('\n').includes('不可作为真实科研证据'));
assert.ok(!calls.some(call => ['settings_save', 'agent_run', 'create', 'workflow_run'].includes(call.cmd)));
const count = calls.length;
await run('setup', 'unexpected'); await run('demo', 'unexpected');
assert.equal(calls.length, count);
fail = true;
await assert.rejects(run('demo'), /引擎不可用/);
assert.equal(opened.length, 1, 'failed demo does not change selected task');
fail = false; offline = false;
await assert.rejects(run('demo'), /离线/);
assert.equal(opened.length, 1, 'non-offline response cannot be labeled as an offline demo');
for (const name of ['setup', 'demo']) assert.ok(COMMANDS.some(command => command.name === name));
console.log('Read-only onboarding, unverified model config, remote guidance and explicit offline synthetic demo contracts passed');
