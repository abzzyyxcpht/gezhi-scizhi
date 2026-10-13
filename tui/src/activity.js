import { redactText } from './sensitive-text.js';

const labels = { tool: '工具', agent: '子代理', stage: '阶段', unknown: '活动' };
const statusText = value => ({ running: '运行中', pending: '等待中', completed: '已完成', done: '已完成', failed: '失败', error: '异常', waiting: '等待处理', waiting_approval: '待审批', cancelled: '已取消' }[value] || value || '状态未记录');
function bounded(value, max = 180) { return redactText(String(value ?? '')).replace(/\s+/g, ' ').slice(0, max); }
export function activityLines(activity = {}, { sessionId = '' } = {}) {
  const rows = Array.isArray(activity.items) ? activity.items : [], children = Array.isArray(activity.children) ? activity.children : [];
  const lines = [`执行活动 · 会话 ${bounded(activity.session_id || sessionId, 80)} · ${statusText(activity.status)}`];
  const pressure = activity.pressure || activity.context_pressure;
  if (pressure && typeof pressure === 'object') {
    lines.push(`上下文压力 ${bounded(pressure.pressure_tokens ?? pressure.input_tokens ?? pressure.tokens ?? '未记录')}/${bounded(pressure.limit_tokens ?? '未记录')} · 估算 token`);
    if (Object.hasOwn(pressure, 'schema_tokens')) lines.push(`消息 ${bounded(pressure.tokens ?? '未记录')} · 工具schema ${bounded(pressure.schema_tokens ?? '未记录')} · 总输入 ${bounded(pressure.input_tokens ?? '未记录')} · 输出预留 ${bounded(pressure.output_reserve_tokens ?? '未记录')}`);
  }
  if (activity.truncated) lines.push('活动已截断；仅显示最近项目，未推断缺失状态。');
  if (!rows.length) lines.push('暂无可读活动；引擎未报告工具或阶段事件。');
  rows.slice(0, 80).forEach((row, index) => {
    const kind = labels[row.type] || labels[row.kind] || labels.unknown;
    const title = row.label || row.title || row.summary || row.tool || row.agent || row.stage || `活动 ${index + 1}`;
    const detail = row.summary || row.error || row.waiting_reason || row.output_id || (row.steps != null ? `${row.steps} 步` : '') || (row.tokens != null ? `${row.tokens} tokens` : '');
    lines.push(`${row.id || row.activity_id || `item-${index + 1}`} · ${kind} · ${bounded(title)} · ${statusText(row.status)}${detail ? ` · ${bounded(detail)}` : ''}`);
  });
  if (children.length) {
    lines.push(`直属/递归子任务 · ${children.length}`);
    children.slice(0, 40).forEach(child => {
      const id = child.session_id || child.child_session_id || child.id;
      const steps = child.steps != null ? ` · ${bounded(child.steps)} 步` : '';
      const tokens = child.tokens != null ? ` · ${bounded(child.tokens)} tokens` : '';
      lines.push(`  ${id ? bounded(id, 72) : '子任务标识未记录'} · ${bounded(child.agent || child.title || child.summary || '子代理')} · ${statusText(child.status)}${steps}${tokens}${child.pending ? ' · 待处理' : ''}`);
    });
  }
  return lines;
}
export function activityReaderPayload(response, sessionId) {
  if (response?.ok !== true) throw new Error(response?.error || '活动读取失败');
  const activity = response.activity || response;
  if (!activity || typeof activity !== 'object') throw new Error('引擎未提供可核验的活动投影');
  return { title: `活动 ${sessionId}`, lines: activityLines(activity, { sessionId }) };
}
