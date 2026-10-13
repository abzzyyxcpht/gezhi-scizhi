// Real stdio Python bridge + the real TUI slash dispatcher. Isolated fixtures only.
// Socket/model entry points fail closed; no external service or user data is used.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { Bridge } from '../src/bridge.js';
import { runWorkbenchCommand } from '../src/workbench-commands.js';

const root = fileURLToPath(new URL('../..', import.meta.url));
const testRoot = path.join(root, '_testtmp');
fs.mkdirSync(testRoot, { recursive: true });
const scratch = fs.mkdtempSync(path.join(testRoot, 'queue-tui-integration-'));
const data = path.join(scratch, 'data');
const guard = path.join(scratch, 'guarded_bridge.py');
fs.writeFileSync(guard, `import atexit, json, os, socket, sys
from pathlib import Path
sys.path.insert(0, os.getcwd())
attempts = {'network': 0, 'model': 0}
def network(*args, **kwargs):
    attempts['network'] += 1
    raise AssertionError('network forbidden by integration fixture')
socket.socket.connect = socket.socket.connect_ex = network
socket.create_connection = socket.getaddrinfo = network
from src import config, settings, llm
def model(*args, **kwargs):
    attempts['model'] += 1
    raise AssertionError('model calls forbidden by integration fixture')
llm.chat = llm.chat_json = llm.chat_stream_agent = model
audit = Path(os.environ['GEZHI_DATA_DIR']) / ('guard-' + str(os.getpid()) + '.json')
atexit.register(lambda: audit.write_text(json.dumps(attempts), encoding='utf-8'))
if not settings._PATH.exists():
    settings.save({'experimental_message_queue': False, 'default_offline': True, 'llm_enabled': False,
        'ui_animations': False, 'plugins_enabled': False, 'mcp_enabled': False,
        'hooks_enabled': False, 'memory_enabled': False, 'subagents_enabled': False, 'git_archive': False})
from src.agent_runtime import SessionStore
store = SessionStore()
for sid in ('bridge-current', 'bridge-other', 'bridge-cancel', 'bridge-restart', 'bridge-disabled'):
    if not store._path(sid).exists():
        store.append(sid, 'user_message', {'text': 'fixture original', 'mode': 'plan', 'agent': 'research', 'offline': True})
        store.append(sid, 'driver_paused', {'status': 'waiting_approval'})
from src.bridge import main
main()
`, 'utf8');
const environment = { ...process.env, GEZHI_DATA_DIR: data, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1',
  GEZHI_REMOTE_URL: '', GEZHI_REMOTE_TOKEN: '', GEZHI_LLM_BASE_URL: '', GEZHI_LLM_API_KEY: '', GEZHI_LLM_MODEL: '',
  GEZHI_LLM_MODEL_FAST: '', GEZHI_LLM_MODEL_STRONG: '', GEZHI_REDUCED_MOTION: '1' };
const bridges = [];
function connect() {
  const bridge = new Bridge({ remoteUrl: '', timeoutMs: 10000, spawn: (_command, _args, options) =>
    spawn('python', ['-B', '-u', guard], { ...options, cwd: root, env: environment }) });
  bridges.push(bridge);
  return bridge;
}
async function close(bridge) {
  if (bridge.closed) return;
  if (bridge.proc.exitCode !== null || bridge.proc.signalCode !== null) { bridge.close(); return; }
  const exited = once(bridge.proc, 'exit');
  bridge.close();
  await exited;
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(read, predicate, label) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) { const value = await read(); if (predicate(value)) return value; await pause(35); }
  assert.fail(label);
}
function finish(sid) {
  fs.appendFileSync(path.join(data, 'sessions', `${sid}.jsonl`), JSON.stringify({ type: 'completed', session_id: sid,
    timestamp: new Date().toISOString(), payload: { status: 'done' } }) + '\n');
}
function events(sid) { return fs.readFileSync(path.join(data, 'sessions', `${sid}.jsonl`), 'utf8').trim().split('\n').map(line => JSON.parse(line)); }
const checks = [];
let bridge, context, loseReply = false;
const outgoing = [];
function useBridge(value) {
  bridge = value;
  context = { currentAgentSession: 'bridge-current', queueRequestIds: new Map(), out: () => {}, openReport: () => {},
    onSettings: () => {}, refreshSessionStatus: () => {}, bridge: {
      connection: () => bridge.connection(),
      call: async request => {
        outgoing.push(request);
        const response = await bridge.call(request);
        if (request.cmd === 'queue_add' && response.ok && loseReply) { loseReply = false; throw new Error('fixture lost successful reply'); }
        return response;
      },
    } };
}
const run = args => runWorkbenchCommand('queue', args, context);
const list = sid => bridge.call({ cmd: 'queue_list', session_id: sid || context.currentAgentSession });
try {
  useBridge(connect()); await bridge.whenReady();
  assert.equal((await list()).queue.enabled, false);
  await assert.rejects(run('add disabled fixture'), /实验|开启/);
  await run('enable');
  loseReply = true;
  await assert.rejects(run('add first queued plan'), /lost successful reply/);
  await run('add first queued plan');
  const attempts = outgoing.filter(row => row.cmd === 'queue_add' && row.request === 'first queued plan');
  assert.equal(attempts[0].queue_request_id, attempts[1].queue_request_id);
  let view = await list();
  assert.equal(view.queue.items.length, 1);
  assert.equal(view.queue.items[0].request_id, attempts[0].queue_request_id);
  assert.notEqual(view.request_id, view.queue.items[0].request_id, 'RPC and business identities are distinct');
  checks.push('real accepted-but-lost reply retried exactly once');
  await run('add second queued plan');
  view = await list();
  assert.equal(view.queue.counts.queued, 2);
  assert.equal(events('bridge-current').filter(row => row.payload?.queue_ticket).length, 0, 'approval state blocks dispatch');
  const [first, second] = view.queue.items;
  await run(`reorder ${second.id} ${first.id}`);
  context.currentAgentSession = 'bridge-other';
  assert.equal((await list()).queue.items.length, 0, 'TUI current SID controls queue scope');
  loseReply = true;
  await assert.rejects(run('add discard recovery'), /lost successful reply/);
  const unresolved = (await list()).queue.items[0];
  await run(`discard ${unresolved.id}`);
  await run('add discard recovery');
  const fresh = (await list()).queue.items.find(item => item.state === 'queued');
  assert.notEqual(fresh.request_id, unresolved.request_id, 'confirmed discard makes the same text a new intent');
  await run(`remove ${fresh.id}`);
  checks.push('confirmed discard resolves uncertain identity before same-text resubmission');
  context.currentAgentSession = 'bridge-current';
  await bridge.call({ cmd: 'settings_save', settings: { default_offline: false, llm_enabled: true,
    llm_base_url: 'http://127.0.0.1:9/v1', llm_api_key: 'fixture-key', llm_model: 'fixture-never-call' } });
  finish('bridge-current');
  await until(list, value => value.queue.counts.delivered === 2, 'queued plans did not complete');
  const delivered = events('bridge-current').filter(row => row.type === 'user_message' && row.payload?.queue_ticket);
  assert.deepEqual(delivered.map(row => row.payload.text), ['second queued plan', 'first queued plan']);
  assert.ok(delivered.every(row => row.payload.offline === true && row.payload.mode === 'plan'));
  checks.push('approval gate, reorder, frozen offline plan mode and selected SID');

  context.currentAgentSession = 'bridge-cancel';
  await run('add cancelled follow-up');
  assert.equal((await bridge.call({ cmd: 'agent_cancel', session_id: 'bridge-cancel' })).ok, true);
  await until(list, value => value.queue.paused && value.queue.resume_required, 'cancellation did not pause remainder');
  assert.equal(events('bridge-cancel').filter(row => row.payload?.queue_ticket).length, 0);
  checks.push('real agent_cancel pauses pending next turns');

  context.currentAgentSession = 'bridge-restart';
  await run('add survives restart');
  await close(bridge);
  useBridge(connect()); await bridge.whenReady(); context.currentAgentSession = 'bridge-restart';
  view = await list();
  assert.equal(view.queue.resume_required, true);
  assert.equal(view.queue.paused, true);
  finish('bridge-restart'); await pause(300);
  assert.equal(events('bridge-restart').filter(row => row.payload?.queue_ticket).length, 0);
  await run('resume');
  await until(list, value => value.queue.counts.delivered === 1, 'explicit restart recovery failed');
  checks.push('bridge restart preserves queue and requires explicit resume');

  context.currentAgentSession = 'bridge-disabled';
  await run('add feature-toggle follow-up'); await run('disable');
  await until(list, value => value.queue.paused && !value.queue.enabled, 'disable did not pause queue');
  finish('bridge-disabled'); await pause(300);
  assert.equal(events('bridge-disabled').filter(row => row.payload?.queue_ticket).length, 0);
  await assert.rejects(run('resume'), /实验|开启/);
  await run('enable'); await run('resume');
  await until(list, value => value.queue.counts.delivered === 1, 'enabled explicit resume failed');
  checks.push('experiment switch preserves pending entries and never silently dispatches');
  await close(bridge);
  const guards = fs.readdirSync(data).filter(name => /^guard-\d+\.json$/.test(name)).map(name => JSON.parse(fs.readFileSync(path.join(data, name), 'utf8')));
  assert.equal(guards.length, 2);
  assert.ok(guards.every(value => value.network === 0 && value.model === 0));
  const report = { date: '2026-09-28', transport: 'real Python stdio bridge', frontend: 'real TUI runWorkbenchCommand entry',
    fixture: 'isolated _testtmp sessions; explicit fixture completion events; offline plan turns',
    assertions_passed: true, checks, guards, llm_calls: 0, network_attempts: 0 };
  const destination = path.join(root, 'docs', 'research', '2026-09-28-queue-tui-integration.json');
  fs.writeFileSync(destination, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally {
  for (const value of bridges) await close(value);
  if (!path.resolve(scratch).startsWith(path.resolve(testRoot) + path.sep)) throw new Error('cleanup target escaped fixture root');
  fs.rmSync(scratch, { recursive: true, force: true });
}
