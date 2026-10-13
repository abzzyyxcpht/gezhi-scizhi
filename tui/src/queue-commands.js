import { randomUUID } from 'node:crypto';
import { redactText } from './sensitive-text.js';

const FLAG = 'experimental_message_queue';
const labels = { queued: '等待下一轮', dispatching: '正在投递', delivered: '已交给会话',
  needs_review: '需要重新核对', removed: '已移除', discarded: '已放弃' };
const idsByBridge = new WeakMap();

export function queueLines(queue = {}) {
  const items = Array.isArray(queue.items) ? queue.items : [];
  const lines = [
    `实验性下一轮队列 · 会话 ${queue.session_id || '未选择'} · ${queue.enabled ? '已启用' : '未启用'}`,
    `${queue.paused || queue.resume_required ? '已暂停' : '等待当前轮完成后投递'} · 工作线程${queue.worker_active === true ? '运行中' : queue.worker_active === false ? '空闲' : '状态未记录'}`,
    '排队内容属于下一轮；审批等待期间不会放行，也不会改写当前请求。',
  ];
  if (queue.pause_reason) lines.push(`暂停原因：${queue.pause_reason}`);
  if (queue.resume_required) lines.push('需要显式 /queue resume 恢复。');
  if (!items.length) lines.push('暂无队列条目。');
  if (queue.history_truncated) lines.push(`显示 ${items.length}/${queue.total_items ?? '未记录'} 项；较早历史已省略，待处理项全部保留。`);
  for (const item of items) {
    const route = item.offline === true ? '离线配置' : item.offline === false ? '在线配置' : '执行方式未记录';
    lines.push(`${item.id} · ${labels[item.state] || item.state || '状态未记录'} · ${item.agent || '未记录'}/${item.mode || '未记录'} · ${route}`);
    lines.push(`  ${item.request || ''}`);
    if (item.error) lines.push(`  ${item.error}`);
  }
  lines.push('remove 仅移除 queued；discard 可放弃 queued/needs_review；投递中由引擎拒绝修改。');
  return lines.map(line => redactText(line));
}

export function queueCanRunWhileBusy(args) {
  const action = String(args || '').trim().split(/\s+/)[0] || 'list';
  return ['list', 'add', 'pause', 'resume', 'remove', 'discard', 'reorder', 'enable', 'disable'].includes(action);
}

export async function runQueueCommand(name, args, context) {
  if (name !== 'queue') return false;
  const { bridge, out } = context;
  const parts = String(args || '').trim().split(/\s+/).filter(Boolean), action = parts[0] || 'list';
  const usage = () => { out('用法：/queue [list|add <文本>|pause|resume|remove <ID>|discard <ID>|reorder <全部queued ID...>|enable|disable]', 'yellow'); return true; };
  const call = async request => {
    const result = await bridge.call(request);
    if (result?.ok !== true) throw new Error(redactText(result?.error || '队列操作未返回有效结果'));
    return result;
  };
  if (['enable', 'disable'].includes(action)) {
    if (parts.length !== 1) return usage();
    if (bridge.connection?.().mode === 'remote') { out('请在主机启用或关闭实验性下一轮队列；远程可查看和操作已启用的队列。', 'yellow'); return true; }
    const settings = { [FLAG]: action === 'enable' };
    await call({ cmd: 'settings_save', settings });
    context.onSettings?.(settings);
    out(action === 'enable' ? '实验性下一轮队列已启用；/queue add <文本> 明确加入后续任务。'
      : '实验性下一轮队列已关闭；存量由引擎保留，当前运行不因此取消。');
    return true;
  }
  const sessionId = context.currentAgentSession;
  if (!sessionId) { out('请先 /session <ID> 选择 Agent 会话，再使用下一轮队列。', 'yellow'); return true; }
  let request, retryKey, requestIds, requestId;
  if (action === 'add') {
    const text = String(args || '').trim().replace(/^add\s*/, '');
    if (!text) return usage();
    requestIds = context.queueRequestIds || idsByBridge.get(bridge);
    if (!requestIds) { requestIds = new Map(); idsByBridge.set(bridge, requestIds); }
    const connection = bridge.connection?.() || {};
    retryKey = JSON.stringify([context.queueConnectionKey || connection.mode, connection.url || '', sessionId, text]);
    requestId = requestIds.get(retryKey) || randomUUID();
    requestIds.set(retryKey, requestId);
    request = { cmd: 'queue_add', session_id: sessionId, request: text, queue_request_id: requestId };
  } else if (['list', 'pause', 'resume'].includes(action) && parts.length <= 1) {
    request = { cmd: `queue_${action}`, session_id: sessionId };
  } else if (['remove', 'discard'].includes(action) && parts.length === 2 && /^[A-Za-z0-9_-]{1,160}$/.test(parts[1])) {
    request = { cmd: `queue_${action}`, session_id: sessionId, ticket_id: parts[1] };
  } else if (action === 'reorder' && parts.length > 1 && parts.slice(1).every(id => /^[A-Za-z0-9_-]{1,160}$/.test(id))) {
    request = { cmd: 'queue_reorder', session_id: sessionId, ordered_ids: parts.slice(1) };
  } else return usage();
  let result;
  try {
    result = await call(request);
    if (action === 'add' && (result.queue?.session_id !== sessionId || result.ticket?.request_id !== requestId
      || !/^[A-Za-z0-9_-]{1,128}$/.test(result.ticket?.id || '')
      || !['queued', 'dispatching', 'delivered', 'needs_review', 'removed'].includes(result.ticket?.state))) {
      throw new Error('服务器未返回可核验的入队回执；请重试相同消息确认。');
    }
  }
  catch (error) {
    if (action === 'add') out('投递结果尚未确认；重试相同 /queue add 文本会沿用本次投递标识。', 'yellow');
    throw new Error(redactText(error.message || error));
  }
  if (requestIds && requestIds.get(retryKey) === requestId) requestIds.delete(retryKey);
  if (['remove', 'discard'].includes(action) && result.queue?.session_id === sessionId
    && result.ticket?.id === request.ticket_id && result.ticket?.state === 'removed' && result.ticket?.request_id) {
    const pendingIds = context.queueRequestIds || idsByBridge.get(bridge);
    for (const [key, id] of pendingIds || []) if (id === result.ticket.request_id) pendingIds.delete(key);
  }
  const lines = queueLines(result.queue || { session_id: sessionId });
  if (action === 'list' && context.openReport) context.openReport('下一轮队列（实验）', lines.join('\n'));
  else {
    lines.slice(0, 12).forEach(line => out(line));
    if (lines.length > 12) out('部分条目未展开；/queue list 查看队列详情。');
  }
  if (action !== 'list') context.refreshSessionStatus?.(sessionId);
  return true;
}
