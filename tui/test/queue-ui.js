// 下一轮队列（Ink queue-ui 的 OpenTUI 移植）：忙时队列/历史/复核仍立即可用、
// 入队重试沿用同一投递标识、busy 所有权不被清除、普通语言在忙时排队。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureQueueFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const calls = [];
  let lostReply = true;
  const held = [];
  Bridge.prototype.call = async function (request) {
    t.calls.push(request); // 包装层也要记账：夹具只在 originalCall 里 push
    if (request.cmd === 'agent_launch') return { ok: true, result: { session_id: 'current', status: 'paused' } };
    if (request.cmd === 'agent_continue') return new Promise(resolve => held.push(resolve));
    if (request.cmd?.startsWith('steer_')) return { ok: true,
      steering: { session_id: 'current', turn_id: 'turn-1', enabled: true, state: 'open', status: 'running', items: [] },
      ...(request.cmd === 'steer_add' ? { ticket: { id: 'correction-1', request_id: request.steer_request_id, state: 'pending' } } : {}) };
    if (request.cmd === 'review_list') return { ok: true, jobs: [], total: 0, enabled: true };
    if (request.cmd === 'session_history') return { ok: true, history: { session_id: 'current', query: '', messages: [], total: 0,
      page: 1, pages: 1, snapshot: 'history-snapshot', generation: 'history-generation', new_messages: false, reset: false } };
    if (request.cmd === 'queue_add' && lostReply) { lostReply = false; throw new Error('fixture lost reply'); }
    if (request.cmd?.startsWith('queue_')) return { ok: true, queue: { session_id: 'current', enabled: true,
      paused: request.cmd === 'queue_pause', pause_reason: request.cmd === 'queue_pause' ? '用户暂停' : '',
      resume_required: false, worker_active: false, revision: 1, items: [{ id: 'ticket-1', state: 'queued', request: '下一轮核验', mode: 'agent', agent: 'research', offline: true }] },
      ...(request.cmd === 'queue_add' ? { ticket: { id: 'ticket-1', request_id: request.queue_request_id, state: 'queued' } } : {}) };
    return originalCall.call(this, request);
  };
  try {
    await t.command('/session current');
    await t.wait(() => t.frame().includes('「会话 current」'), 'session selected');
    await t.press('q');
    await t.wait(() => !t.frame().includes('「会话 current」'), 'session reader closed');
    await t.command('/continue');
    await t.wait(() => held.length > 0, 'active continuation');
    // 忙时：历史阅读器仍可用（本地命令优先于排队）
    await t.command('/history');
    await t.wait(() => t.frame().includes('对话记录'), 'history reader remains available during active turn');
    await t.press('q');
    // 忙时：复核收件箱仍可用
    await t.command('/reviews list');
    await t.wait(() => t.frame().includes('后台复核收件箱'), 'inbox remains accessible while the main turn is busy');
    // 忙时：同轮补充指导（/steer 命令路径，绑定当前回合）
    await t.command('/steer add 优先反例');
    await t.wait(() => t.frame().includes('correction-1'), 'same-turn correction available while original request is busy');
    const steerCall = t.calls.find(call => call.cmd === 'steer_add');
    assert.equal(steerCall?.expected_turn_id, 'turn-1', 'correction binds to the active turn');
    // 忙时：入队失败如实呈现；重试相同文本沿用同一投递标识
    await t.command('/queue add 下一轮核验');
    await t.wait(() => t.frame().includes('fixture lost reply'), 'uncertain submission surfaced while busy');
    await t.command('/queue add 下一轮核验');
    await t.wait(() => t.frame().includes('ticket-1'), 'queue add remains reachable while busy');
    const added = t.calls.filter(call => call.cmd === 'queue_add');
    assert.equal(added.length, 2);
    assert.equal(added[0].queue_request_id, added[1].queue_request_id, 'retry reuses the delivery identity');
    assert.equal(added[0].request_id, undefined, 'phone allowlist respected');
    assert.ok(t.frame().includes('处理中'), 'queue completion does not clear the original busy state');
    // 忙时：队列开关/暂停/重排仍可达
    await t.command('/queue disable');
    await t.wait(() => t.calls.some(call => call.cmd === 'settings_save' && call.settings.experimental_message_queue === false),
      'busy turn can stop future queue dispatch');
    await t.command('/queue enable');
    await t.wait(() => t.calls.some(call => call.cmd === 'settings_save' && call.settings.experimental_message_queue === true),
      'explicit queue enable stays reachable');
    await t.command('/queue pause');
    assert.ok(t.calls.some(call => call.cmd === 'queue_pause' && call.session_id === 'current'));
    await t.command('/queue reorder ticket-1');
    assert.ok(t.calls.some(call => call.cmd === 'queue_reorder' && call.ordered_ids[0] === 'ticket-1'));
    await t.command('/queue list');
    await t.wait(() => t.frame().includes('下一轮队列（实验）'), 'read-only queue reader while current request remains active');
    await t.press('q');
    // 忙时：普通语言进消息队列（OpenTUI 语义：排队而非拒绝），原回合完成后冲刷
    await t.press('ordinary draft');
    await t.press('\r');
    await t.wait(() => t.frame().includes('已排队'), 'ordinary language queues while busy');
    assert.ok(t.frame().includes('处理中'), 'original busy state preserved');
    held.forEach(resolve => resolve({ ok: true, result: { session_id: 'current', status: 'done', answer: '当前轮完成' } }));
    await t.wait(() => t.frame().includes('当前轮完成'), 'original response still arrives');
  } finally {
    held.forEach(resolve => resolve({ ok: true, result: { session_id: 'current', status: 'done', answer: '' } }));
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureQueueFrames();
  console.log(`Queue submission during continuation, retry identity, busy ownership passed (${result.frames.length} frames)`);
  process.exit(0);
}
