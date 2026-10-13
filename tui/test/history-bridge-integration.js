// Real TUI entry -> Python stdio -> history SQLite. Generated local journal only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { Bridge } from '../src/bridge.js';
import { runWorkbenchCommand } from '../src/workbench-commands.js';

const root = fileURLToPath(new URL('../..', import.meta.url));
const base = path.join(root, '_testtmp');
fs.mkdirSync(base, { recursive: true });
const scratch = fs.mkdtempSync(path.join(base, 'history-wire-'));
const data = path.join(scratch, 'data'), sessions = path.join(data, 'sessions');
fs.mkdirSync(sessions, { recursive: true });
fs.writeFileSync(path.join(data, 'settings.json'), JSON.stringify({ llm_enabled: false, mcp_enabled: false,
  plugins_enabled: false, hooks_enabled: false, git_archive: false, default_offline: true }));
const log = path.join(sessions, 'history-wire.jsonl');
const event = (type, text) => JSON.stringify({ type, session_id: 'history-wire', payload: { [type === 'assistant_message' ? 'content' : 'text']: text } }) + '\n';
fs.writeFileSync(log, Array.from({ length: 95 }, (_, index) => event('user_message', `Question ${index} 反例 api_key=private-value`)).join(''));
const source = `import atexit,json,os,socket
from pathlib import Path
attempts=[]
def deny(*args,**kwargs):
    attempts.append('network')
    raise AssertionError('network disabled for history integration')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=deny
atexit.register(lambda:(Path(os.environ['GEZHI_DATA_DIR'])/'guard.json').write_text(json.dumps(attempts)))
from src.bridge import main
main()
`;
const environment = { ...process.env, GEZHI_DATA_DIR: data, PYTHONIOENCODING: 'utf-8', GEZHI_REMOTE_URL: '', GEZHI_REMOTE_TOKEN: '' };
const bridge = new Bridge({ remoteUrl: '', spawn: (_cmd, _args, options) => spawn('python', ['-B', '-u', '-c', source], { ...options, cwd: root, env: environment }) });
const reports = [], calls = [], replies = [];
let closed = false;
const context = { currentAgentSession: 'history-wire', out: text => reports.push(text), openReport: (_title, text) => reports.push(text),
  bridge: { connection: () => bridge.connection(), call: async request => {
    calls.push(request); const reply = await bridge.call(request); replies.push(reply); return reply;
  } } };
const run = args => runWorkbenchCommand('history', args, context);
async function close() {
  if (closed) return;
  const exit = once(bridge.proc, 'exit'); bridge.close(); await exit; closed = true;
}
try {
  await bridge.whenReady();
  await run('');
  assert.equal(replies.at(-1).history.page, 3);
  assert.ok(!reports.at(-1).includes('private-value'));
  await run('1');
  assert.equal(replies.at(-1).history.decoded_events, 0);
  assert.ok(reports.at(-1).includes('Question 0'));
  fs.appendFileSync(log, event('steer_applied', '新的反例补充🧪'.repeat(5000)));
  await run('2');
  assert.equal(replies.at(-1).history.total, 95);
  assert.equal(replies.at(-1).history.new_messages, true);
  await run('refresh');
  const last = replies.at(-1).history.messages.at(-1);
  assert.equal(last.kind, 'steer_applied'); assert.equal(last.truncated, true);
  await run(`message ${last.id}`);
  assert.equal(replies.at(-1).message.next_offset, 20000);
  await run('Question 9');
  assert.equal(replies.at(-1).history.total, 6);
  const replacement = log + '.replacement';
  fs.writeFileSync(replacement, event('user_message', '替换后的历史'));
  fs.renameSync(replacement, log);
  await run('1');
  assert.equal(replies.at(-1).history.reset, true);
  assert.equal(replies.at(-1).history.total, 0);
  await run('refresh');
  assert.ok(reports.at(-1).includes('替换后的历史'));
  assert.ok(calls.every(row => row.cmd.startsWith('session_history')));
  await close();
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(data, 'guard.json'), 'utf8')), []);
  const report = { ok: true, transport: 'actual TUI history entry -> Python stdio -> SQLite derived index', network_attempts: 0,
    checks: ['bounded latest and first pages', 'redacted stored/displayed text', 'no old event decoding on cached page',
      'append keeps snapshot with new-message hint', 'long steer message chunk reading', 'literal search matches', 'atomic rewrite resets old snapshot'] };
  fs.mkdirSync(path.join(root, 'test-output'), { recursive: true });
  fs.writeFileSync(path.join(root, 'test-output', 'history-index-tui-wire-2026-09-28.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally {
  await close();
  if (!path.resolve(scratch).startsWith(path.resolve(base) + path.sep)) throw new Error('cleanup escaped history fixture');
  fs.rmSync(scratch, { recursive: true, force: true });
}
