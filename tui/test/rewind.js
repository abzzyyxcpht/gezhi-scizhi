// /rewind 命令：命令层契约（mock 桥）+ 桥层 request_id 关联（假引擎）。
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { runWorkbenchCommand } from '../src/workbench-commands.js';
import { Bridge } from '../src/bridge.js';

// ---- 命令层：mock 桥 ----
const calls = [], output = [], opened = [];
let rewindResult = { ok: true, rewound_to: 8, removed: 5 };
const ctx = {
  taskId: null, currentAgentSession: 'sess-1',
  bridge: { connection: () => ({ mode: 'local' }), async call(request) {
    calls.push(request);
    if (request.cmd === 'session_rewind') return rewindResult;
    if (request.cmd === 'session_snapshot') {
      return { ok: true, snapshot: { session_id: request.session_id, status: 'draft' } };
    }
    return { ok: true };
  } },
  out: (text) => output.push(text),
  setCurrentAgentSession: (id) => { ctx.currentAgentSession = id; },
  openSession: (id, snapshot) => opened.push({ id, snapshot }),
};

// 当前无会话 → 友好报错，且不发桥请求
ctx.currentAgentSession = null;
assert.equal(await runWorkbenchCommand('rewind', '', ctx), true);
assert.ok(output.at(-1).includes('当前没有选中的 Agent 会话'));
assert.equal(calls.length, 0, '无会话时不得调用 session_rewind');
ctx.currentAgentSession = 'sess-1';

// 默认回滚 1 轮；成功后 transcript 重载（重新取快照并打开）
assert.equal(await runWorkbenchCommand('rewind', '', ctx), true);
assert.deepEqual(calls.at(-2), { cmd: 'session_rewind', session_id: 'sess-1', turns: 1 });
assert.equal(calls.at(-1).cmd, 'session_snapshot', '成功后重载 transcript 快照');
assert.ok(output.some(line => line.includes('✎ 已回滚 1 轮')));
assert.equal(opened.at(-1).id, 'sess-1');
assert.equal(opened.at(-1).snapshot.status, 'draft');

// 显式 N 轮
await runWorkbenchCommand('rewind', '3', ctx);
assert.deepEqual(calls.at(-2), { cmd: 'session_rewind', session_id: 'sess-1', turns: 3 });

// 非法参数 → 用法提示，请求不出本地
const before = calls.length;
await runWorkbenchCommand('rewind', '0', ctx);
await runWorkbenchCommand('rewind', 'abc', ctx);
await runWorkbenchCommand('rewind', '1.5', ctx);
await runWorkbenchCommand('rewind', '-2', ctx);
assert.equal(calls.length, before);
assert.ok(output.some(line => line.includes('用法：/rewind')));

// 后端拒绝（无可回滚）→ 抛错，由工作台兜底呈现
rewindResult = { ok: true, rewound_to: 8, removed: 5, warning: '旧执行文件稍后清理' };
await runWorkbenchCommand('rewind', '', ctx);
assert.ok(output.some(line => line.includes('旧执行文件稍后清理')));

rewindResult = { ok: false, error: '没有可回滚的用户轮次' };
await assert.rejects(runWorkbenchCommand('rewind', '', ctx), /没有可回滚的用户轮次/);

// ---- 桥层：假引擎验证 session_rewind 请求/响应契约（request_id 关联，乱序返回） ----
function engine() {
  const proc = new EventEmitter();
  proc.stdout = new PassThrough(); proc.stderr = new PassThrough();
  proc.requests = [];
  proc.stdin = { write(line) { proc.requests.push(JSON.parse(line)); } };
  proc.kill = () => proc.emit('exit', 0);
  proc.reply = (msg) => proc.stdout.write(JSON.stringify(msg) + '\n');
  return proc;
}
const proc = engine();
const bridge = new Bridge({ spawn: () => proc, timeoutMs: 1000, remoteUrl: '' });
try {
  proc.reply({ ready: true });
  await bridge.whenReady();
  const other = bridge.call({ cmd: 'session_snapshot', session_id: 'sess-1' });
  const rewound = bridge.call({ cmd: 'session_rewind', session_id: 'sess-1', turns: 2 });
  // 就绪握手后、首个带 ID 响应前仍是串行：先回 snapshot，rewind 请求才会被写出
  proc.reply({ ok: true, snapshot: { status: 'draft' }, request_id: proc.requests.at(-1).request_id });
  assert.equal((await other).snapshot.status, 'draft');
  const rewindReq = proc.requests.at(-1);
  assert.equal(rewindReq.cmd, 'session_rewind');
  assert.equal(rewindReq.session_id, 'sess-1');
  assert.equal(rewindReq.turns, 2);
  assert.ok(rewindReq.request_id != null, '请求必须带 request_id');
  // 再发一条并乱序返回：rewind 先于 probe 到达
  const probe = bridge.call({ cmd: 'usage' });
  proc.reply({ ok: true, rewound_to: 6, removed: 4, request_id: rewindReq.request_id });
  proc.reply({ ok: true, text: '', request_id: proc.requests.at(-1).request_id });
  assert.deepEqual(await rewound, { ok: true, rewound_to: 6, removed: 4, request_id: rewindReq.request_id });
  await probe;
  // 失败契约透传
  const failed = bridge.call({ cmd: 'session_rewind', session_id: 'sess-1', turns: 1 });
  proc.reply({ ok: false, error: '没有可回滚的用户轮次', request_id: proc.requests.at(-1).request_id });
  assert.deepEqual(await failed, { ok: false, error: '没有可回滚的用户轮次', request_id: proc.requests.at(-1).request_id });
} finally { bridge.close(); }
console.log('/rewind command contract, friendly errors, transcript reload and bridge request_id correlation passed');
