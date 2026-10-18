import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { Bridge } from '../src/bridge.js';

function engine() {
  const proc = new EventEmitter();
  proc.stdout = new PassThrough(); proc.stderr = new PassThrough();
  proc.stdin = new EventEmitter(); proc.requests = [];
  proc.stdin.write = line => proc.requests.push(JSON.parse(line));
  proc.kill = () => proc.emit('exit', 0);
  proc.reply = message => proc.stdout.write(JSON.stringify(message) + '\n');
  return proc;
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

// Every old process callback must be harmless after a new engine is ready.
for (const oldEvent of ['exit', 'error', 'stdin-error', 'ready', 'reply', 'partial-line']) {
  const previous = engine(), current = engine(); let starts = 0;
  const bridge = new Bridge({ remoteUrl: '', spawn: () => starts++ ? current : previous });
  try {
    previous.reply({ ready: true }); await bridge.whenReady();
    const abandoned = assert.rejects(bridge.call({ cmd: 'old-call' }, { timeoutMs: 15 }), /启动|断开/);
    previous.emit('error', new Error('fixture failed engine'));
    await abandoned;
    bridge.disconnect(); current.reply({ ready: true }); await bridge.whenReady();
    const response = bridge.call({ cmd: 'new-call' });
    response.catch(() => {});
    const newId = current.requests[0].request_id;
    if (oldEvent === 'exit') previous.emit('exit', 1);
    if (oldEvent === 'error') previous.emit('error', new Error('late old failure'));
    if (oldEvent === 'stdin-error') previous.stdin.emit('error', new Error('late old pipe failure'));
    if (oldEvent === 'ready') previous.reply({ ready: true });
    if (oldEvent === 'reply') previous.reply({ ok: true, request_id: newId, value: 'old-engine' });
    if (oldEvent === 'partial-line') previous.stdout.write('{"ok":true,"request_id":"');
    previous.stderr.write('late fixture diagnostic');
    await pause(20); // the old timeout must not flush or settle the new generation
    current.reply({ ok: true, request_id: newId, value: 'new-engine' });
    assert.equal((await response).value, 'new-engine', oldEvent);
    assert.equal(bridge.ready, true, oldEvent);
    assert.equal(bridge.localError, null, oldEvent);
    assert.equal(bridge.pending.size, 0, oldEvent);
  } finally { bridge.close(); }
}

// A stale ready marker also cannot complete the new engine's handshake early.
const previous = engine(), current = engine(); let starts = 0;
const bridge = new Bridge({ remoteUrl: '', spawn: () => starts++ ? current : previous });
try {
  previous.reply({ ready: true }); previous.emit('error', new Error('fixture'));
  bridge.disconnect(); previous.reply({ ready: true });
  assert.equal(bridge.ready, false);
  const response = bridge.call({ cmd: 'wait-for-current' });
  assert.equal(current.requests.length, 0);
  current.reply({ ready: true });
  current.reply({ ok: true, request_id: current.requests[0].request_id });
  await response;
} finally { bridge.close(); }

// A timeout already queued by the retired generation cannot flush new work.
const before = engine(), after = engine(); let restarted = false;
const timed = new Bridge({ remoteUrl: '', spawn: () => restarted ? after : before });
const nativeTimeout = globalThis.setTimeout;
let staleTimeout;
try {
  before.reply({ ready: true });
  globalThis.setTimeout = (callback, delay) => {
    staleTimeout = callback;
    return nativeTimeout(callback, delay);
  };
  const oldCall = assert.rejects(timed.call({ cmd: 'old-timed-call' }), /启动|断开/);
  globalThis.setTimeout = nativeTimeout;
  before.emit('error', new Error('fixture')); await oldCall;
  restarted = true; timed.disconnect();
  const newCall = timed.call({ cmd: 'wait-for-new-engine' });
  const flush = timed._flush.bind(timed); let staleFlushes = 0;
  timed._flush = () => { staleFlushes++; flush(); };
  staleTimeout();
  assert.equal(staleFlushes, 0, 'stale timeout must be ignored before touching new queue');
  assert.equal(timed.pending.size, 1);
  assert.equal(after.requests.length, 0);
  after.reply({ ready: true });
  after.reply({ ok: true, request_id: after.requests[0].request_id });
  await newCall;
} finally { globalThis.setTimeout = nativeTimeout; timed.close(); }
console.log('Bridge restarted engine ignores stale process events and timeout callbacks');

// Aborted transports may still resolve; both HTTP and JSON completion must
// reject the retired connection before its content reaches the caller.
for (const heldPhase of ['fetch', 'json']) {
  let finishFetch, finishJson;
  const oldToken = 'old-fixture-token', newToken = 'new-fixture-token';
  const remote = new Bridge({ remoteUrl: 'http://old.invalid', token: oldToken,
    fetch: () => new Promise(resolve => { finishFetch = resolve; }) });
  try {
    const response = remote.call({ cmd: 'session_activity', session_id: 'old-session' });
    const rejection = assert.rejects(response, error => /中断|超时|切换/.test(error.message)
      && !error.message.includes(oldToken) && !error.message.includes(newToken));
    const oldResult = { ok: false, error: `server included ${oldToken}` };
    if (heldPhase === 'json') {
      finishFetch({ ok: true, json: () => new Promise(resolve => { finishJson = resolve; }) });
      await pause(0);
    }
    remote.connect('http://new.invalid', newToken);
    if (heldPhase === 'json') finishJson(oldResult);
    else finishFetch({ ok: true, json: async () => oldResult });
    await rejection;
    assert.equal(remote.controllers.size, 0, heldPhase);
  } finally { remote.close(); }
}

// A same-address reconnect is still a new connection object/credential epoch.
let finishSameAddress;
const reconnecting = new Bridge({ remoteUrl: 'http://same.invalid', token: 'same-fixture-token',
  fetch: () => new Promise(resolve => { finishSameAddress = resolve; }) });
try {
  const response = reconnecting.call({ cmd: 'list' });
  const rejection = assert.rejects(response, /中断|超时|切换/);
  reconnecting.connect('http://same.invalid', 'same-fixture-token');
  finishSameAddress({ ok: true, json: async () => ({ ok: true, value: 'retired-response' }) });
  await rejection;
  assert.equal(reconnecting.controllers.size, 0);
} finally { reconnecting.close(); }

const currentRemote = new Bridge({ remoteUrl: 'http://current.invalid', token: 'current-fixture-token',
  fetch: async () => ({ ok: true, json: async () => ({ ok: false, error: 'server included current-fixture-token' }) }) });
try {
  const result = await currentRemote.call({ cmd: 'list' });
  assert.equal(result.ok, false);
  assert.ok(result.error.includes('[已隐藏]'));
  assert.ok(!result.error.includes('current-fixture-token'));
  assert.equal(currentRemote.controllers.size, 0);
} finally { currentRemote.close(); }
console.log('Remote bridge rejects stale fetch/JSON results and hides the captured request token');

// Switching to a remote host retires outstanding local UI requests, including
// work queued before the local handshake. No unsent local command may run later.
for (const ready of [false, true]) {
  const local = engine();
  const switching = new Bridge({ remoteUrl: '', spawn: () => local,
    fetch: async () => ({ ok: true, json: async () => ({ ok: true, value: 'remote' }) }) });
  let firstResult, queuedResult;
  try {
    if (ready) local.reply({ ready: true });
    const first = switching.call({ cmd: 'local-status' }).then(
      value => { firstResult = { value }; }, error => { firstResult = { error }; });
    const queued = switching.call({ cmd: 'local-write' }).then(
      value => { queuedResult = { value }; }, error => { queuedResult = { error }; });
    switching.connect('http://remote.invalid', 'fixture-token');
    await pause(0);
    assert.match(firstResult?.error?.message || '', /切换/, 'retire local request immediately');
    assert.match(queuedResult?.error?.message || '', /切换/, 'discard local command before it is sent');
    await Promise.all([first, queued]);
    if (!ready) local.reply({ ready: true });
    assert.equal(local.requests.length, ready ? 1 : 0, 'remote mode never flushes queued local commands');
    assert.equal((await switching.call({ cmd: 'list' })).value, 'remote');
    switching.disconnect();
    const fresh = switching.call({ cmd: 'fresh-local' });
    if (ready) local.reply({ ok: true, value: 'retired-local' });
    assert.equal(local.requests.at(-1).cmd, 'fresh-local');
    local.reply({ ok: true, value: 'fresh-local' });
    assert.equal((await fresh).value, 'fresh-local', 'late legacy reply cannot settle a fresh request');
    assert.equal(switching.pending.size, 0);
  } finally { switching.close(); }
}

// Correlated local replies cannot escape into the remote workspace either.
const correlated = engine();
const switched = new Bridge({ remoteUrl: '', spawn: () => correlated });
try {
  correlated.reply({ ready: true });
  const handshake = switched.call({ cmd: 'handshake' });
  correlated.reply({ ok: true, request_id: correlated.requests[0].request_id });
  await handshake;
  let staleResult;
  const stale = switched.call({ cmd: 'local-session' }).then(
    value => { staleResult = { value }; }, error => { staleResult = { error }; });
  const staleId = correlated.requests.at(-1).request_id;
  switched.connect('http://remote.invalid', 'fixture-token');
  correlated.reply({ ok: true, request_id: staleId, value: 'old-local-session' });
  await stale;
  assert.match(staleResult?.error?.message || '', /切换/);
  switched.disconnect();
  const fresh = switched.call({ cmd: 'fresh' });
  correlated.reply({ ok: true, request_id: correlated.requests.at(-1).request_id, value: 'fresh' });
  assert.equal((await fresh).value, 'fresh');
} finally { switched.close(); }
console.log('Local-to-remote switching retires pending work and preserves reply alignment');
