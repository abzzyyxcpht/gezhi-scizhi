// 远程会话状态与压缩：只读 session-status、旧档案不虚构、
// 无操作/失败压缩可见、审批隔离、脱敏白名单字段。帧标记按 OpenTUI 口径对齐。
// 注意：OpenTUI 审批面板打开时键盘独占（Enter 归面板），与 Ink 可边显示边输命令不同；
// 因此先用数字键决策审批（call id 仍来自被选会话的快照），再执行状态/压缩断言。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureSessionStatusFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call, originalConnection = Bridge.prototype.connection;
  const calls = [];
  let approved = false, rejectCompact = false;
  const pending = { id: 'current-call', tool: 'shell.run', capability: 'execute', arguments: { command: 'echo selected' } };
  const statusFor = sessionId => sessionId === 'historic'
    ? { session_id: sessionId, status: 'done', driver: 'planned', model: {}, context: null, budget: null,
      tokens: null, usage_complete: null, session_usage: null, can_compact: false, compact_unavailable_reason: '旧档案未记录模型状态' }
    : { session_id: sessionId, status: approved ? 'paused' : 'waiting_approval', agent: 'research', mode: 'agent', driver: 'adaptive',
      model: { model: 'fixture-model', protocol: 'responses', api_key: 'private-status-secret' },
      context: { tokens: 1200, limit_tokens: 8000, measurement: 'heuristic_tokens', compressions: 2, messages: 15 },
      budget: { token_limit: 10000, remaining_tokens: 9600, model_calls: 3, max_steps: 12, remaining_steps: 9 },
      tokens: 400, usage_complete: false, session_usage: { tokens: 900, model_calls: 7, usage_complete: false },
      waiting_reason: approved ? '等待继续' : '等待工具审批', can_compact: approved,
      compact_unavailable_reason: approved ? '' : '需要先处理审批' };
  Bridge.prototype.connection = () => ({ mode: 'remote', url: 'http://fixture' });
  Bridge.prototype.call = async function (request) {
    calls.push(request);
    if (request.cmd === 'session_snapshot') return { ok: true, snapshot: { session_id: request.session_id,
      status: approved ? 'done' : 'waiting_approval', pending: approved ? null : pending } };
    if (request.cmd === 'session_events') return { ok: true, events: [], cursor: request.cursor || 'stable', reset: false, has_more: false };
    if (request.cmd === 'session_status') return { ok: true, status: statusFor(request.session_id) };
    if (request.cmd === 'session_compact') return rejectCompact ? { ok: false, error: '会话正在运行，不能压缩' } : {
      ok: true, result: { session_id: request.session_id, changed: request.session_id === 'current',
        before_tokens: 1200, after_tokens: request.session_id === 'current' ? 700 : 1200,
        removed_messages: request.session_id === 'current' ? 6 : 0, model_calls: 0, method: 'deterministic',
        message: request.session_id === 'current' ? '已保留目标和近期对话' : '没有可缩减的历史' }, status: statusFor(request.session_id) };
    if (request.cmd === 'agent_approve') { approved = true; return { ok: true, result: { session_id: request.session_id, status: 'done' } }; }
    const fixtures = { workflows: { workflows: [] }, workbench_status: { processes: [], loops: [] }, list: { tasks: [] } };
    return { ok: true, ...(fixtures[request.cmd] || {}) };
  };
  try {
    await t.command('/session current');
    await t.wait(() => t.frame().includes('「会话 current」'), 'existing remote session selected');
    t.capture('session-reader', 80, 24, false);
    await t.press('q');
    await t.wait(() => !t.frame().includes('「会话 current」'), 'return to remote approval composer');
    assert.equal(calls.filter(call => ['agent_run', 'agent_launch'].includes(call.cmd)).length, 0,
      'remote status uses an existing session');
    await t.wait(() => t.frame().includes('需要审批') && t.frame().includes('允许一次'), 'current approval selected');
    await t.press('1');
    await t.wait(() => approved, 'approval still addresses original selection');
    assert.deepEqual(calls.find(call => call.cmd === 'agent_approve'), { cmd: 'agent_approve', session_id: 'current', tool_call_id: 'current-call' });
    await t.command('/session-status historic');
    await t.wait(() => t.frame().includes('旧档案未记录模型状态'), 'unknown legacy status');
    assert.ok(t.frame().includes('未记录'));
    assert.ok(!/NaN|undefined|0\/0/.test(t.frame()));
    await t.command('/compact historic');
    await t.wait(() => t.frame().includes('没有可缩减的历史') && t.frame().includes('上下文未改动'), 'remote no-op compaction');
    await t.command('/session-status');
    await t.wait(() => t.frame().includes('fixture-model') && t.frame().includes('1200/8000'), 'readable current status');
    for (const word of ['估算 token', '本轮报送 token 400', '会话累计报送 token 900', '剩余 9 步', '报送不完整']) {
      assert.ok(t.frame().includes(word), word);
    }
    t.capture('session-status', 80, 24);
    assert.equal(calls.filter(call => call.cmd === 'session_status').at(-1).session_id, 'current');
    const statusCalls = calls.filter(call => call.cmd === 'session_status').length;
    await t.command('/compact');
    await t.wait(() => t.frame().includes('1200 → 700') && t.frame().includes('模型调用 0'), 'deterministic remote compaction');
    await t.wait(() => calls.filter(call => call.cmd === 'session_status').length > statusCalls, 'current compaction refreshes observation', 3000);
    rejectCompact = true;
    await t.command('/compact historic');
    await t.wait(() => t.frame().includes('会话正在运行，不能压缩'), 'backend activity rejection is visible');
    await t.command('/status');
    await t.wait(() => t.frame().includes('上下文'), 'workflow status command unchanged');
    assert.ok(!t.frame().includes('private-status-secret'), 'only whitelisted model/status fields render');
    assert.ok(t.frames.every(frame => !frame.text.includes('private-status-secret')));
  } finally {
    Bridge.prototype.call = originalCall;
    Bridge.prototype.connection = originalConnection;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureSessionStatusFrames();
  console.log(`${result.frames.length} session status real OpenTUI frames: remote status/compact, unknown metrics, no-op/error, approval isolation and redaction passed`);
}
// opentui 渲染器保活计时器：直接运行时必须显式退出（被 capture 脚本 import 时不退出）
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) process.exit(0);
