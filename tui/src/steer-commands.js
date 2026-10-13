import { randomUUID } from 'node:crypto';
import { redactText } from './sensitive-text.js';

const retries = new WeakMap();
const labels = { pending: '等待本轮边界', applied: '已进入本轮上下文', expired: '已过期，未应用' };

export async function runSteerCommand(name, args, context) {
  if (name !== 'steer') return false;
  const { bridge, out, currentAgentSession: sid } = context;
  const raw = String(args || '').trim(), action = raw.split(/\s+/)[0] || 'status';
  const call = async request => {
    const result = await bridge.call(request);
    if (result?.ok !== true) throw new Error(redactText(result?.error || '补充结果未确认'));
    return result;
  };
  if (['enable', 'disable'].includes(action) && action === raw) {
    if (bridge.connection?.().mode === 'remote') { out('请在主机启用或关闭运行中干预实验。', 'yellow'); return true; }
    const settings = { experimental_inflight_steering: action === 'enable' };
    await call({ cmd: 'settings_save', settings });
    context.onSettings?.(settings);
    out(action === 'enable' ? '已启用运行中干预；/steer add <补充要求> 作用于当前模型回合。' : '已关闭运行中干预；未应用补充将在下一边界过期。');
    return true;
  }
  if (!sid) { out('请先 /session <ID> 选择模型会话。', 'yellow'); return true; }
  if (!['status', 'add'].includes(action)) { out('用法：/steer [status|add <文本>|enable|disable]'); return true; }
  const status = (await call({ cmd: 'steer_status', session_id: sid })).steering;
  if (status?.session_id !== sid) throw new Error('补充状态的会话身份不匹配');
  if (action === 'status') {
    const lines = [`本轮补充 · ${status.enabled ? '实验已启用' : '实验未启用'} · ${sid}`, `回合 ${status.turn_id || '无模型回合'} · ${status.status}`];
    for (const item of status.items || []) lines.push(`${item.id} · ${labels[item.state] || item.state}`, `  ${item.request}`);
    if (status.resume_required) lines.push(`等待中的回合需显式 /continue ${sid}；不会自动批准原工具。`);
    lines.map(line => redactText(line)).forEach(line => out(line));
    return true;
  }
  const text = raw.replace(/^add\s*/, '');
  if (!text) { out('用法：/steer add <补充要求>'); return true; }
  let pending = retries.get(bridge);
  if (!pending) { pending = new Map(); retries.set(bridge, pending); }
  const connection = bridge.connection?.() || {};
  const key = JSON.stringify([connection.mode, connection.url, sid, text]);
  let identity = pending.get(key);
  if (identity && identity.turn !== status.turn_id) throw new Error('回合已改变；请核查并修改文本后重新提交，或使用下一轮队列。');
  if (!identity) { identity = { turn: status.turn_id, id: randomUUID() }; pending.set(key, identity); }
  try {
    const result = await call({ cmd: 'steer_add', session_id: sid, expected_turn_id: identity.turn,
      steer_request_id: identity.id, request: text });
    if (result.steering?.session_id !== sid || result.steering?.turn_id !== identity.turn
      || result.ticket?.request_id !== identity.id || !result.ticket?.id
      || !['pending', 'applied', 'expired'].includes(result.ticket?.state)) throw new Error('缺少匹配本轮的回执');
    if (result.ticket.state === 'expired') throw new Error('该补充已过期，未应用；请核查后修改重发');
    pending.delete(key);
    out(`本轮补充 ${result.ticket.id}：${labels[result.ticket.state]}。`);
    if (result.steering.resume_required) out(`请显式 /continue ${sid} 处理补充；不会自动批准原工具。`);
  } catch (error) {
    out('未确认应用结果；重试相同文本沿用本次回合与补充标识。', 'yellow');
    throw new Error(redactText(error.message || error));
  }
  context.refreshSessionStatus?.(sid);
  return true;
}
