import { redactText } from './sensitive-text.js';

const states = { pending: '待处理', in_progress: '进行中', completed: '已完成', blocked: '受阻', cancelled: '已取消' };
const text = (value, max = 240) => redactText(String(value ?? '')).replace(/[\x00-\x1f\x7f-\x9f]/g, ' ').slice(0, max);
const count = value => Number.isInteger(value) && value >= 0 ? value : '?';

export function executionPlanLines(plan, { compact = false } = {}) {
  if (plan?.error) return [`执行计划：${text(plan.error)}`];
  if (!plan?.has_plan) return ['本轮尚未建立执行计划；简单问答无需计划。'];
  const lines = [`执行计划 ${count(plan.completed)}/${count(plan.total)} 项完成 · 版本 ${count(plan.revision)}`];
  const steps = Array.isArray(plan.steps) ? plan.steps.slice(0, 12) : [];
  for (const step of compact ? steps.filter(row => row.status === 'in_progress' || row.status === 'blocked').slice(0, 2) : steps) {
    lines.push(`${states[step.status] || '未知'} · ${text(step.title, 180)}`);
    if (!compact) {
      if (step.criterion) lines.push(`  完成标准：${text(step.criterion)}`);
      if (step.reason) lines.push(`  说明：${text(step.reason)}`);
      if (step.evidence_kind === 'model_statement') lines.push('  依据：模型声明，未关联执行回执');
      for (const item of (Array.isArray(step.evidence_details) ? step.evidence_details : []).slice(0, 8)) {
        lines.push(`  工具回执：${text(item.tool, 80)} · ${text(item.call_id, 128)}`);
        if (item.summary) lines.push(`    ${text(item.summary)}`);
      }
    }
  }
  if (!compact) lines.push('工具回执证明调用已记录，不等同于科研结论或产物质量已经验证。');
  return lines;
}
