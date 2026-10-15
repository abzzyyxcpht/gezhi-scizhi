// 研究命令面：密钥配置输入掩码 + 历史不落敏感命令、
// agent 会话选择/继续/取消、/usage 未知缓存保留、/session 打开后跨会话继续。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureResearchFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const secret = 'fixture-private-api-key';
  Bridge.prototype.call = async function (request) {
    t.calls.push(request);
    if (request.cmd === 'command' && String(request.text || '').startsWith('/connection save')) {
      return { ok: true, text: '连接已保存' };
    }
    if (request.cmd === 'command' && String(request.text || '') === '/connections') {
      return { ok: true, text: '连接档案：fixture · 密钥已配置' };
    }
    if (request.cmd === 'command' && String(request.text || '').startsWith('/audit')) {
      return { ok: true, text: '# 科研证据审计\n证据不足，待复核。' };
    }
    if (request.cmd === 'usage') return { ok: true, usage: { model: {} }, text: '连接 fixture / model fixture\n缓存读取：未报送 · 命中率：未知' };
    if (request.cmd === 'model_connection_save') return { ok: true, registry: {} };
    if (request.cmd === 'agent_launch') return { ok: true, result: { session_id: 'agent-fixture', status: 'done', answer: 'Fixture answer' } };
    if (request.cmd === 'agent_continue') return { ok: true, result: { session_id: request.session_id, status: 'waiting_human', pending: { id: 'call1', tool: 'search', capability: 'network' } } };
    if (request.cmd === 'agent_cancel') return { ok: true, result: { session_id: request.session_id, status: 'cancelled' } };
    if (request.cmd === 'session_snapshot') return { ok: true, snapshot: { session_id: request.session_id, status: 'paused' } };
    return originalCall.call(this, request);
  };
  try {
    // 连接档案 + 密钥 JSON 输入掩码：回显脱敏、帧零泄漏
    await t.command('/connections');
    await t.wait(() => t.frame().includes('密钥已配置'), 'connection registry');
    // /connections 现为窗口化面板：↑↓ 归面板，先 Esc 收起再验证历史召回
    await t.press('\x1b');
    await t.wait(() => !t.frame().includes('写操作走 /connection'), 'registry panel closed');
    await t.press(`/connection save {"id":"fixture","protocol":"responses","base_url":"https://example.invalid","api_key":"${secret}"}`);
    await t.press('\r');
    await t.wait(() => t.frame().includes('连接已保存'), 'save connection via bridge');
    assert.ok(!t.frame().includes(secret), 'JSON input masking');
    assert.ok(t.frames.every(frame => !String(frame.text).includes(secret)), 'credentials never render in any captured frame');
    // 敏感命令不进历史：↑ 召回到上一条（/connections），而非保存命令
    await t.press('\x1b[A');
    await t.wait(() => t.frame().includes('/connections'), 'credential command omitted from history');
    await t.press('\u0015');
    // agent 会话：发起 → 继续 → 取消
    await t.command('/agent research fixture');
    await t.wait(() => t.frame().includes('会话 agent-fixture'), 'agent session opened');
    await t.command('/continue');
    await t.wait(() => t.frame().includes('/approve agent-fixture call1'), 'continue selected session');
    assert.equal(t.calls.filter(request => request.cmd === 'agent_continue').at(-1).session_id, 'agent-fixture');
    await t.command('/cancel-agent');
    await t.wait(() => t.frame().includes('已取消'), 'cancel selected agent');
    assert.equal(t.calls.filter(request => request.cmd === 'agent_cancel').at(-1).session_id, 'agent-fixture');
    // /usage：未知缓存措辞保留
    await t.command('/usage');
    await t.wait(() => t.frame().includes('命中率：未知'), 'unknown cache preserved');
    // /session 打开后跨会话继续
    await t.command('/session session-selected');
    await t.wait(() => t.frame().includes('会话 session-selected'), 'session selected');
    await t.press('q');
    await t.command('/continue');
    await t.wait(() => t.calls.filter(request => request.cmd === 'agent_continue').at(-1)?.session_id === 'session-selected',
      'continue opened session');
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureResearchFrames();
  console.log(`Model JSON privacy/history, agent lifecycle, unknown cache and cross-session continuation passed (${result.frames.length} frames)`);
  process.exit(0);
}
