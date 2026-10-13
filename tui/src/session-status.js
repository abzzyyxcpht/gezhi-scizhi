import { redactText } from './sensitive-text.js';
import stringWidth from 'string-width';

const statusNames = { running: '运行中', paused: '已暂停', done: '已完成', error: '失败', failed: '失败',
  cancelled: '已取消', cancelling: '取消中', waiting_approval: '待审批', waiting_task: '等待科研任务',
  waiting_children: '等待子任务', waiting_human: '等待人工确认', planned: '已规划', draft: '草稿', blocked: '受阻' };
const recorded = value => typeof value === 'number' && Number.isFinite(value) ? String(value)
  : typeof value === 'string' && value.trim() ? redactText(value) : '未记录';
const reported = value => value === true ? '完整' : value === false ? '不完整' : '未记录';
const sourceStates = { captured: '已冻结', included: '已纳入', budget_deferred: '因预算延期', disabled: '已停用',
  manual_only: '禁止模型调用（手动入口待支持）', unsupported: '不支持', unapproved: '未获批准', missing: '缺失', error: '读取失败',
  loaded: '正文已读取', partial: '部分正文已读取' };
const purposes = { general: '通用', research: '科研' };
const executions = { agent: '执行任务', plan: '只读规划', review: '证据审查' };
const sourceKinds = { instructions: '项目规则', skill_index: '技能索引', skill_body: '技能正文', memory: '记忆' };
const sourceScopes = { user: '用户', project: '项目', local: '项目本地', stage: '阶段', session: '会话', global: '用户',
  application: '程序目录', working_directory: '工作目录' };
const SOURCE_LIMIT = 24;
const RESEARCH_CONTEXT_LIMIT = 4;
const manifestValue = value => Number.isSafeInteger(value) ? value.toLocaleString('en-US')
  : typeof value === 'string' && value.trim() ? redactText(value.trim()).slice(0, 180) : '未记录';

export function executionContextIdentity(status = {}) {
  return `用途 ${purposes[status.workspace_mode] || manifestValue(status.workspace_mode)}`
    + ` · 角色 ${manifestValue(status.agent)} · 执行 ${executions[status.mode] || manifestValue(status.mode)}`;
}

/** Persisted metadata only: never infer historical context from current settings. */
export function contextManifestLines(manifest) {
  if (!manifest || typeof manifest !== 'object' || manifest.schema_version !== 1) {
    return ['执行上下文来源：未记录（旧会话或规则流程）'];
  }
  const basis = manifest.basis === 'frozen_prefix' ? '冻结前缀' : manifestValue(manifest.basis);
  const lines = [
    `执行上下文来源：${sourceStates[manifest.status] || '未记录'} · 基于${basis} · prompt v${manifestValue(manifest.prompt_version)}`,
    `记录时间 ${manifestValue(manifest.captured_at)}`,
    `用途 ${purposes[manifest.workspace_mode] || manifestValue(manifest.workspace_mode)} · 角色 ${manifestValue(manifest.agent)}`
      + ` · 阶段 ${manifestValue(manifest.stage)} · 项目 ${manifestValue(manifest.project_id)}`,
  ];
  const budget = manifest.budget;
  lines.push(budget && typeof budget === 'object'
    ? `预算：${{ characters: '字符', chars: '字符', tokens: 'token' }[budget.unit] || manifestValue(budget.unit)} ${manifestValue(budget.used_chars)}/${manifestValue(budget.limit_chars)}`
    : '预算：未记录');
  const sources = Array.isArray(manifest.sources) ? manifest.sources : [];
  if (!sources.length) lines.push('  · 来源：未记录');
  for (const source of sources.slice(0, SOURCE_LIMIT)) {
    if (!source || typeof source !== 'object') continue;
    const state = source.kind === 'skill_index' && source.status === 'included' ? '索引已纳入（正文按需读取）'
      : sourceStates[source.status] || '未记录';
    let line = `  · ${sourceKinds[source.kind] || manifestValue(source.kind)} · ${manifestValue(source.name)}`
      + ` · ${sourceScopes[source.scope] || manifestValue(source.scope)} · ${state}`
      + ` · 字符 ${manifestValue(source.used_chars)}/${manifestValue(source.total_chars)}`;
    if (typeof source.path === 'string' && source.path.trim()) line += ` · 路径 ${manifestValue(source.path)}`;
    if (typeof source.reason === 'string' && source.reason.trim()) line += ` · ${manifestValue(source.reason)}`;
    lines.push(line);
  }
  const omitted = Math.max(0, sources.length - SOURCE_LIMIT)
    + (Number.isSafeInteger(manifest.omitted_sources) && manifest.omitted_sources > 0 ? manifest.omitted_sources : 0);
  if (omitted) lines.push(`另有 ${omitted} 个来源未显示（有界快照）`);
  for (const notice of (Array.isArray(manifest.notices) ? manifest.notices : []).slice(0, 8)) {
    if (typeof notice === 'string' && notice.trim()) lines.push('提示：' + redactText(notice.trim()).slice(0, 220));
  }
  return lines;
}

/** Internal request outcomes describe returned/failed calls, not validated results. */
export function sessionContextLines(status = {}) {
  const main = contextManifestLines(status?.context_manifest);
  const research = status?.research_contexts;
  if (!research || typeof research !== 'object' || research.schema_version !== 1) return main;
  const entries = Array.isArray(research.entries)
    ? research.entries.filter(entry => entry && typeof entry === 'object' && !Array.isArray(entry)) : [];
  const issue = typeof research.issue === 'string' && research.issue.trim() ? manifestValue(research.issue) : '';
  if (!entries.length && !issue) return main;
  const lines = ['主会话前缀', ...main, '', '近期工具内部模型请求'];
  if (issue) lines.push(issue);
  for (const entry of entries.slice(-RESEARCH_CONTEXT_LIMIT)) {
    const outcome = { returned: '请求已返回', error: '请求失败' }[entry.outcome] || '请求状态未记录';
    lines.push(`请求 ${manifestValue(entry.request_id)} · 节点 ${manifestValue(entry.node)}`
      + ` · 提示 ${manifestValue(entry.prompt_key)} · ${outcome}`);
    lines.push(`请求记录 ${manifestValue(entry.observed_at)}`);
    lines.push(...contextManifestLines(entry.context_manifest), '');
  }
  const omitted = Math.max(0, entries.length - RESEARCH_CONTEXT_LIMIT)
    + (Number.isSafeInteger(research.omitted_entries) && research.omitted_entries > 0 ? research.omitted_entries : 0);
  if (omitted) lines.push(`另有 ${omitted} 条工具内部模型请求未显示（有界快照）`);
  else if (lines.at(-1) === '') lines.pop();
  return lines;
}

/** Source paths and explanations remain fully readable after a narrow resize. */
export function wrapContextLines(lines, width) {
  const limit = Number.isFinite(width) ? Math.max(12, Math.floor(width)) : 72;
  const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
  return lines.flatMap(line => String(line).split('\n').flatMap(paragraph => {
    let text = '', used = 0;
    const wrapped = [];
    for (const { segment } of segmenter.segment(paragraph)) {
      const size = stringWidth(segment);
      if (used + size > limit && text) { wrapped.push(text); text = ''; used = 0; }
      text += segment; used += size;
    }
    wrapped.push(text);
    return wrapped;
  }));
}

/** Whitelist session measurements; never substitute global model/usage values. */
export function sessionStatusLines(status = {}) {
  const context = status.context, budget = status.budget, usage = status.session_usage;
  const lines = [
    `会话 ${recorded(status.session_id)} · ${statusNames[status.status] || recorded(status.status)}`,
    `模型 ${recorded(status.model?.model)} · 协议 ${recorded(status.model?.protocol)} · ${status.driver === 'adaptive' ? '模型执行' : status.driver === 'planned' ? '计划执行' : '执行方式未记录'}`,
    context?.measurement === 'heuristic_tokens'
      ? `上下文 ${recorded(context.tokens)}/${recorded(context.limit_tokens)} 估算 token · ${recorded(context.messages)} 条消息 · 压缩 ${recorded(context.compressions)} 次`
      : '上下文：未记录',
    `本轮报送 token ${recorded(status.tokens)} · 剩余额度 ${recorded(budget?.remaining_tokens)}/${recorded(budget?.token_limit)}`,
    `本轮模型调用 ${recorded(budget?.model_calls)}/${recorded(budget?.max_steps)} · 剩余 ${recorded(budget?.remaining_steps)} 步 · 用量报送${reported(status.usage_complete)}`,
    `会话累计报送 token ${recorded(usage?.tokens)} · 模型调用 ${recorded(usage?.model_calls)} · 用量报送${reported(usage?.usage_complete)}`,
    `等待原因：${recorded(status.waiting_reason)}`,
    status.can_compact === true ? `手动压缩可用：/compact ${recorded(status.session_id)}（确定性压缩，不调用模型）`
      : `手动压缩不可用：${recorded(status.compact_unavailable_reason)}`,
  ];
  if (context?.measurement === 'heuristic_tokens' && Object.hasOwn(context, 'schema_tokens')) {
    lines.splice(3, 0,
      `输入估算：消息 ${recorded(context.tokens)} · 工具schema ${recorded(context.schema_tokens)} · 总输入 ${recorded(context.input_tokens)} · 输出预留 ${recorded(context.output_reserve_tokens)}`,
      `上下文压力 ${recorded(context.pressure_tokens)}/${recorded(context.limit_tokens)} 估算 token（总输入 + 输出预留）`);
  }
  if (status.updated_at) lines.push(`记录更新：${recorded(status.updated_at)}`);
  lines.splice(1, 0, executionContextIdentity(status));
  lines.push(...sessionContextLines(status));
  return lines;
}

export function compactResultLines(result = {}) {
  return [
    result.changed === true ? '上下文已压缩（确定性压缩）' : '上下文未改动',
    `估算 token ${recorded(result.before_tokens)} → ${recorded(result.after_tokens)} · 移除 ${recorded(result.removed_messages)} 条消息 · 模型调用 ${recorded(result.model_calls)}`,
    recorded(result.message),
  ];
}
