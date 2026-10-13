import assert from 'node:assert/strict';
import { runWorkbenchCommand, safeCommandEcho, featureKeys, motionEnabled, panelVisible } from '../src/workbench-commands.js';

const requests = [], output = [], opened = [], settings = [], sessions = [];
const ctx = { bridge: {
  async call(request) { requests.push(request); return { ok: true,
    processes: [{ task_id: 't1', status: 'waiting_human', topic: 'human review' }], loops: [],
    workflows: [{ id: 'review', title: '文献审阅', stages: ['search', 'approve'], requires_approval: true }],
    task_id: 'new-task', loop: { loop_id: 'l1', status: 'running', iteration: 1, max_iterations: 2 },
    snapshot: { session_id: 's1', events: [{ type: 'tool', payload: { token: 'secret' } }] }, path: '/tmp/session.md',
    settings: { llm_api_key: 'secret', llm_model: 'demo', ui_animations: false },
    plugins: { names: [{ name: 'bib', enabled: false }], errors: [] },
  }; },
  connect(url) { this.url = url; }, disconnect() {}, connection() { return { mode: 'local' }; },
}, out: line => output.push(line), openTask: id => opened.push(id),
  openSession: (id, snapshot) => sessions.push({ id, snapshot }),
  onSettings: value => settings.push(value), setPanel: value => ctx.panel = value,
  setWorkbench: value => ctx.workbench = value,
  refreshPlugins: async () => {}, connectionChanged: async () => {},
  openTerminal: async () => { ctx.terminalOpened = true; },
};
async function run(name, args = '') { return runWorkbenchCommand(name, args, ctx); }
assert.equal(await run('nonexistent'), false);
await run('ps'); assert.equal(requests.at(-1).cmd, 'workbench_status');
assert.ok(output.some(line => line.includes('waiting_human')));
await run('workflows'); assert.ok(output.some(line => line.includes('文献审阅')));
await run('workflow', 'review multi word topic');
assert.deepEqual(requests.at(-1), { cmd: 'workflow_run', workflow_id: 'review', topic: 'multi word topic' });
assert.equal(opened.at(-1), 'new-task');
await run('loop', 'start review 60 3 multi word topic');
assert.deepEqual(requests.at(-1), { cmd: 'loop_start', workflow_id: 'review', interval_seconds: 60, max_iterations: 3, topic: 'multi word topic' });
let count = requests.length;
for (const args of ['start review NaN 3 topic', 'start review 0 3 topic', 'start review 0.5 3 topic', 'start review 60 0 topic', 'start review 60 1.5 topic', 'start review 60 101 topic', 'start review 31536001 3 topic', 'stop']) await run('loop', args);
assert.equal(requests.length, count, 'invalid loop input must not reach service');
await run('loop', 'resume l1'); assert.deepEqual(requests.at(-1), { cmd: 'loop_resume', loop_id: 'l1' });
await run('loop', 'stop l1'); assert.deepEqual(requests.at(-1), { cmd: 'loop_stop', loop_id: 'l1' });
await run('session', 's1'); assert.equal(sessions.at(-1).id, 's1');
assert.ok(!JSON.stringify(sessions).includes('secret'));
await run('session-export', 's1 json'); assert.equal(requests.at(-1).format, 'json');
count = requests.length; await run('session-export', 's1 exe'); assert.equal(requests.length, count);
await run('settings'); assert.ok(!output.join('\n').includes('secret'));
for (const key of featureKeys) { await run('toggle', `${key} off`); assert.deepEqual(requests.at(-1).settings, { [key]: false }); }
count = requests.length; await run('toggle', 'llm_api_key off'); assert.equal(requests.length, count);
await run('plugins', 'disable bib'); assert.deepEqual(requests.at(-1), { cmd: 'plugin_toggle', name: 'bib', on: false });
await run('model', 'fast faster'); assert.deepEqual(requests.at(-1).settings, { llm_model_fast: 'faster' });
await run('model', 'clear-key'); assert.deepEqual(requests.at(-1).settings, { llm_api_key: '' });
await run('terminal'); assert.equal(ctx.terminalOpened, true);
await run('remote', 'start 8765'); assert.deepEqual(requests.at(-1), { cmd: 'remote_start', host: '0.0.0.0', port: 8765 });
count = requests.length; await run('remote', 'start 70000'); assert.equal(requests.length, count);
await run('panel', 'off'); assert.equal(ctx.panel, false);
assert.ok(!safeCommandEcho('/connect http://localhost:8765 secret').includes('secret'));
assert.ok(!safeCommandEcho('/model key secret').includes('secret'));
assert.equal(motionEnabled({ isTTY: false, settings: {}, env: {} }), false);
assert.equal(motionEnabled({ isTTY: true, settings: {}, env: { GEZHI_REDUCED_MOTION: '1' } }), false);
assert.equal(motionEnabled({ isTTY: true, settings: { ui_animations: false }, env: {} }), false);
assert.equal(motionEnabled({ isTTY: true, settings: { ui_animations: true }, env: {} }), true);
assert.equal(motionEnabled({ isTTY: true, settings: {}, env: { TERM: 'dumb' } }), false, 'dumb terminals use static indicators');
assert.equal(motionEnabled({ isTTY: true, settings: {}, env: { NO_COLOR: '' } }), false, 'explicit NO_COLOR also disables decorative motion');
assert.equal(panelVisible(109, true), false); assert.equal(panelVisible(110, true), true);
assert.equal(panelVisible(140, false), false);
console.log('Workbench commands / validation / settings redaction / motion / panel tests passed');
