import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { Bridge } from '../src/bridge.js';

function engine() {
  const proc = new EventEmitter();
  proc.stdout = new PassThrough(); proc.stderr = new PassThrough();
  proc.requests = [];
  proc.stdin = { write(line) { proc.requests.push(JSON.parse(line)); } };
  proc.kill = () => proc.emit('exit', 0);
  proc.reply = (msg) => proc.stdout.write(JSON.stringify(msg) + '\n');
  return proc;
}
const pause = (ms) => new Promise(resolve => setTimeout(resolve, ms));

const proc = engine();
const bridge = new Bridge({ spawn: () => proc, timeoutMs: 1000, remoteUrl: '' });
try {
  assert.ok(bridge.pending instanceof Map, 'requests must be tracked by ID');
  proc.reply({ ready: true });
  await bridge.whenReady();
  const initial = bridge.call({ cmd: 'first' });
  proc.reply({ ok: true, request_id: proc.requests[0].request_id });
  await initial;
  const expired = bridge.call({ cmd: 'slow' }, { timeoutMs: 15 });
  const expiredId = proc.requests.at(-1).request_id;
  await assert.rejects(expired, /超时/);
  const fresh = bridge.call({ cmd: 'fresh' });
  const freshId = proc.requests.at(-1).request_id;
  proc.reply({ ok: true, request_id: expiredId, value: 'late' });
  proc.reply({ ok: true, request_id: freshId, value: 'fresh' });
  assert.equal((await fresh).value, 'fresh');
  assert.equal(bridge.pending.size, 0);
  const a = bridge.call({ cmd: 'a' }); const aid = proc.requests.at(-1).request_id;
  const b = bridge.call({ cmd: 'b' }); const bid = proc.requests.at(-1).request_id;
  proc.reply({ ok: true, request_id: bid, value: 'b' });
  proc.reply({ ok: true, request_id: aid, value: 'a' });
  assert.equal((await a).value, 'a'); assert.equal((await b).value, 'b');
  const pending = bridge.call({ cmd: 'never' });
  proc.emit('exit', 2);
  await assert.rejects(pending, /退出/);
  assert.equal(bridge.pending.size, 0);
} finally { bridge.close(); }

const oldProc = engine();
const legacy = new Bridge({ spawn: () => oldProc, remoteUrl: '' });
oldProc.reply({ ready: true });
try {
  const expired = legacy.call({ cmd: 'old' }, { timeoutMs: 15 });
  await assert.rejects(expired, /超时/);
  const fresh = legacy.call({ cmd: 'new' });
  assert.equal(oldProc.requests.length, 1, 'legacy requests stay serial after timeout');
  oldProc.reply({ ok: true, value: 'late old' });
  assert.equal(oldProc.requests.length, 2);
  oldProc.reply({ ok: true, value: 'new' });
  assert.equal((await fresh).value, 'new');
} finally { legacy.close(); }

const calls = [];
const remote = new Bridge({ remoteUrl: 'http://192.168.1.10:8765', token: 'private-secret',
  fetch: async (url, init) => { calls.push({ url, init }); return {
    ok: true, json: async () => ({ ok: true, task_id: 'remote-task' }),
  }; }, spawn: () => { throw new Error('must not spawn Python in remote mode'); } });
try {
  assert.equal((await remote.call({ cmd: 'list' })).task_id, 'remote-task');
  assert.equal(calls[0].url, 'http://192.168.1.10:8765/api/command');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer private-secret');
  assert.equal(calls[0].init.redirect, 'error');
  assert.ok(!calls[0].url.includes('private-secret'));
  assert.ok(!calls[0].init.body.includes('private-secret'));
  assert.ok(!JSON.stringify(remote.connection()).includes('private-secret'));
} finally { remote.close(); }
const broken = new Bridge({ remoteUrl: 'http://localhost:8765', token: 'private-secret',
  fetch: async () => { throw new Error('private-secret leaked by server'); } });
await assert.rejects(broken.call({ cmd: 'list' }), error => !error.message.includes('private-secret'));
broken.close();
await pause(5);
const closingProc = engine();
let ended = false, killed = false;
closingProc.stdin.end = () => { ended = true; queueMicrotask(() => closingProc.emit('exit', 0)); };
closingProc.kill = () => { killed = true; closingProc.emit('exit', 0); };
const closing = new Bridge({ spawn: () => closingProc, remoteUrl: '' });
closingProc.reply({ ready: true });
closing.close();
await pause(5);
assert.equal(ended, true, 'normal close should send EOF so Python finally cleans up remote server');
assert.equal(killed, false, 'responsive engine exits gracefully');
const disconnectProc = engine();
let remoteAborted = false;
const disconnecting = new Bridge({ remoteUrl: 'http://localhost:8765', token: 'fixture-token',
  spawn: () => disconnectProc,
  fetch: async (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => { remoteAborted = true; reject(new Error('aborted')); });
  }),
});
const remotePending = assert.rejects(disconnecting.call({ cmd: 'workbench_status' }), /中断/);
disconnecting.disconnect();
await remotePending;
assert.equal(remoteAborted, true, 'disconnect cancels remote inflight fetch');
assert.equal(disconnecting.connection().mode, 'local');
disconnecting.close();
console.log('Bridge request ID / timeout / exit / legacy / remote token tests passed');
