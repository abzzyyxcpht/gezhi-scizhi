// Real slash commands -> Python stdio -> durable review inbox and notebook.
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
const scratch = fs.mkdtempSync(path.join(testRoot, 'review-tui-'));
const guard = path.join(scratch, 'bridge.py'), data = path.join(scratch, 'data');
fs.writeFileSync(guard, `import atexit, json, os, socket, sys
from pathlib import Path
sys.path.insert(0, os.getcwd())
attempts = {'network': 0, 'model': 0}
def deny_network(*args, **kwargs):
    attempts['network'] += 1
    raise AssertionError('network forbidden in local review integration')
socket.socket.connect = socket.socket.connect_ex = deny_network
socket.create_connection = socket.getaddrinfo = deny_network
from src import settings, llm
def deny_model(*args, **kwargs):
    attempts['model'] += 1
    raise AssertionError('model forbidden in local review integration')
llm.chat = llm.chat_json = llm.chat_stream_agent = deny_model
settings.save({'llm_enabled': False, 'mcp_enabled': False, 'plugins_enabled': False,
 'hooks_enabled': False, 'git_archive': False, 'default_offline': True})
atexit.register(lambda: (Path(os.environ['GEZHI_DATA_DIR'])/'guard.json').write_text(json.dumps(attempts),encoding='utf-8'))
from src.bridge import main
main()
`);
const environment = { ...process.env, GEZHI_DATA_DIR: data, PYTHONIOENCODING: 'utf-8', GEZHI_REMOTE_URL: '', GEZHI_REMOTE_TOKEN: '' };
const bridge = new Bridge({ remoteUrl: '', spawn: (_command, _args, options) => spawn('python', ['-B', '-u', guard], { ...options, cwd: root, env: environment }) });
const calls = [], output = [];
let lose = true, exited = false;
const context = { out: text => output.push(text), openReport: (_title, text) => output.push(text),
  bridge: { connection: () => bridge.connection(), call: async req => {
    calls.push(req);
    const result = await bridge.call(req);
    if (req.cmd === 'review_start' && result.ok && lose) { lose = false; throw new Error('fixture lost accepted review receipt'); }
    return result;
  } } };
const run = args => runWorkbenchCommand('reviews', args, context);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function ready(id) {
  for (let index = 0; index < 150; index++) {
    const reply = await bridge.call({ cmd: 'review_get', job_id: id });
    if (reply.job?.status === 'ready') return reply.job;
    await pause(25);
  }
  assert.fail('review did not become ready');
}
async function close() {
  if (exited) return;
  const ended = once(bridge.proc, 'exit');
  bridge.close();
  await ended;
  exited = true;
}
try {
  await bridge.whenReady();
  const created = await bridge.call({ cmd: 'notebook_save', record: { kind: 'hypothesis', title: '真实桥接验收记录',
    question: '反例是否充分？', evidence: [{ relation: 'supports', scope: 'abstract', quote: '合成材料', source: { id: 'fixture', synthetic: true } }] } });
  assert.equal(created.ok, true);
  const record = created.record, start = `start ${record.id} 1 local`;
  await run('enable');
  await assert.rejects(run(start), /lost accepted/);
  const key = calls.at(-1).review_request_id;
  await run(start);
  assert.equal(calls.at(-1).review_request_id, key);
  let listed = await bridge.call({ cmd: 'review_list', record_id: record.id });
  assert.equal(listed.jobs.length, 1);
  const job = await ready(listed.jobs[0].id);
  assert.equal((await bridge.call({ cmd: 'notebook_get', id: record.id })).record.version, 1);
  await run(`show ${job.id}`);
  assert.ok(output.some(text => text.includes('合成')));
  await run(`accept ${job.id}`); await run(`accept ${job.id}`);
  const saved = (await bridge.call({ cmd: 'notebook_get', id: record.id })).record;
  assert.equal(saved.version, 2); assert.equal(saved.observations.length, 1);
  assert.deepEqual(saved.evidence, record.evidence);
  await run(`start ${record.id} 2 local`);
  listed = await bridge.call({ cmd: 'review_list', record_id: record.id });
  const next = await ready(listed.jobs[0].id);
  assert.notEqual(next.id, job.id);
  await bridge.call({ cmd: 'notebook_save', expected_version: 2, record: { id: record.id, limitations: '新条件' } });
  await assert.rejects(run(`accept ${next.id}`), /已变更/);
  await close();
  const guards = JSON.parse(fs.readFileSync(path.join(data, 'guard.json'), 'utf8'));
  assert.deepEqual(guards, { network: 0, model: 0 });
  const report = { ok: true, transport: 'real TUI slash -> Python stdio -> review manager -> notebook', guards,
    checks: ['accepted-but-lost receipt retries one job', 'background local result preserves original record',
      'explicit acceptance writes one note once', 'evidence remains unchanged', 'new source version blocks old result'] };
  fs.mkdirSync(path.join(root, 'test-output'), { recursive: true });
  fs.writeFileSync(path.join(root, 'test-output', 'review-inbox-tui-wire-2026-09-28.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally {
  await close();
  if (!path.resolve(scratch).startsWith(path.resolve(testRoot) + path.sep)) throw new Error('cleanup escaped fixture root');
  fs.rmSync(scratch, { recursive: true, force: true });
}
