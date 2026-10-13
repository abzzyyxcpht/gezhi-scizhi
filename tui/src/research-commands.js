import { credentialValues, redactSettings, redactText } from './sensitive-text.js';
import { sessionStatusLines, compactResultLines } from './session-status.js';

export const MODEL_PROTOCOLS = ['chat_completions', 'responses', 'anthropic_messages', 'gemini', 'ollama'];
export const THINKING_STYLES = ['auto', 'none', 'openai_effort', 'anthropic_budget', 'anthropic_adaptive',
  'gemini_budget', 'gemini_level', 'deepseek_thinking', 'ollama_think'];
export const REASONING_EFFORTS = ['auto', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
export const MODEL_STAGES = ['default', 'fast', 'strong', 'planning', 'extraction', 'synthesis', 'review'];

/** 审批参数摘要：工具名 + 关键参数（file 类给 path，shell.run 给 command 原文，mcp 给 server/tool）。 */
export function approvalSummaryLine(pending) {
  const tool = String(pending?.tool || '');
  const args = pending?.arguments || {};
  let detail = '';
  if (tool.startsWith('file.')) detail = args.path || args.file_path || '';
  else if (tool === 'shell.run') detail = args.command || '';
  else if (/^mcp[._]/.test(tool)) detail = [args.server, args.tool].filter(Boolean).join(' / ');
  if (!detail) detail = Object.values(args).find(v => typeof v === 'string' && v.trim()) || '';
  return detail ? `${tool} — ${detail}` : tool;
}

/** Shared result presentation also keeps the selected Agent session in sync. */
export function presentAgentResult(result, context, fallbackSession) {
  const sessionId = result.session_id || fallbackSession;
  if (sessionId) context.setCurrentAgentSession?.(sessionId);
  context.onAgentResult?.(result, sessionId);
  context.out(`Agent 状态：${result.status || '未知'}${sessionId ? ` · 会话 ${sessionId}` : ''}`);
  if (result.pending) {
    context.out(`待审批：${result.pending.tool || ''} · ${result.pending.capability || ''}`, 'yellow');
    context.out(`参数：${approvalSummaryLine(result.pending)}`, 'yellow');
    context.out(`批准：/approve ${sessionId} ${result.pending.id}`);
    context.out(`拒绝：/deny ${sessionId} ${result.pending.id}`);
  }
  if (result.error) context.out(redactText(result.error), 'red');
  if (result.budget) context.out(`模型调用 ${result.budget.model_calls}/${result.budget.max_steps} · 报送 token 剩余预算 ${result.budget.remaining_tokens}${result.usage_complete === false ? '（用量报送不完整）' : ''}`);
  if (result.context) {
    const state = result.context;
    context.out(state.measurement === 'heuristic_tokens'
      ? `上下文 ${state.tokens ?? '未知'}/${state.limit_tokens ?? '未知'} 估算 token · 压缩 ${state.compressions ?? 0} 次（非模型报送消耗）`
      : `上下文 ${state.bytes ?? '未知'}/${state.limit_bytes ?? '未知'} 字节 · 压缩 ${state.compressions ?? 0} 次（字节检查，不是 token 数）`);
  }
  const answer = result.answer || result.message || result.content;
  if (typeof answer === 'string' && !context.onAgentContent?.(answer, result, sessionId)) {
    answer.split('\n').forEach(line => context.out(redactText(line)));
  }
  if (result.workspace) {
    const workspace = result.workspace;
    context.out(`工作区：文献 ${workspace.pool_count || 0} · 抽取 ${workspace.record_count || 0} · 引用 ${workspace.citation_count || 0}`);
  }
  if (sessionId) context.out('会话续跑 /continue · 取消会话 /cancel-agent · 查看 /session ' + sessionId);
}

export async function runResearchCommand(name, args, context) {
  const { bridge, out } = context;
  const parts = (args || '').trim().split(/\s+/).filter(Boolean);
  const usage = value => { out(`用法：${value}`, 'yellow'); return true; };
  const localOnly = () => {
    if (bridge.connection?.().mode !== 'remote') return false;
    out('模型配置请在主机本机 TUI 或桌面操作；远程连接不发送配置或密钥。', 'yellow');
    return true;
  };
  const call = async request => {
    try {
      const result = await bridge.call(request);
      if (result.ok === false) throw new Error(result.error || '操作失败');
      return result;
    } catch (error) { throw new Error(redactText(error.message || error, credentialValues(request))); }
  };
  const showObject = value => JSON.stringify(redactSettings(value), null, 2).split('\n').forEach(line => out(line));
  if (name === 'session-status' || name === 'compact') {
    const sessionId = parts[0] || context.currentAgentSession;
    if (parts.length > 1 || !/^[A-Za-z0-9_-]{1,128}$/.test(sessionId || '')) {
      return usage(`/${name} [会话ID]（无参数使用当前会话；/sessions 查看档案）`);
    }
    const result = await call({ cmd: name === 'compact' ? 'session_compact' : 'session_status', session_id: sessionId });
    if (name === 'compact') {
      compactResultLines(result.result || {}).forEach(line => out(line));
      if (sessionId === context.currentAgentSession) await context.refreshSessionStatus?.(sessionId);
    }
    sessionStatusLines(result.status || { session_id: sessionId }).forEach(line => out(line));
    return true;
  }
  const showRoutes = registry => {
    for (const stage of registry.stages || MODEL_STAGES) {
      const resolved = registry.resolved?.[stage] || {};
      out(`${stage} → ${registry.routes?.[stage] || resolved.profile_id || 'legacy'}${resolved.model ? ` · ${resolved.model}` : ''}`);
    }
  };
  const saveSetting = async patch => {
    await call({ cmd: 'settings_save', settings: patch });
    context.onSettings?.(patch, { saved: true });
    out('模型推理设置已保存。');
  };

  if (name === 'connections' || name === 'models') {
    const { registry = {} } = await call({ cmd: 'model_registry' });
    if (name === 'connections') {
      out('模型连接（密钥隐藏；修改仅限主机）');
      for (const connection of registry.connections || []) out(redactText(
        `${connection.id} · ${connection.label || connection.id} · ${connection.protocol || 'chat_completions'} · ${connection.base_url || '未配置地址'} · 密钥${connection.has_api_key || connection.api_key ? '已配置' : '未配置'}`));
      if (!(registry.connections || []).length) out('暂无独立连接；默认 legacy 连接使用 /model endpoint 与 /model key 设置。');
      out('保存 /connection save <JSON> · 删除 /connection delete <id>');
    } else {
      out('模型配置与科研阶段路由');
      for (const profile of registry.profiles || []) {
        out(redactText(`${profile.id} · ${profile.connection_id}/${profile.model} · ${profile.thinking_style || 'auto'}/${profile.reasoning_effort || 'auto'}`));
        out(`  输出上限 ${profile.max_output_tokens ?? '自动'} · 思考预算 ${profile.thinking_budget ?? '自动'} · 上下文 ${profile.context_window ?? '自动'} · 缓存提示${profile.prompt_cache === false ? '关' : profile.prompt_cache === true ? '开' : '默认'}`);
      }
      if (!(registry.profiles || []).length) out('暂无独立模型配置，当前使用 legacy 默认配置。');
      showRoutes(registry);
      out('保存 /profile save <JSON> · 路由 /route <阶段> <profile_id> · 能力 /model capabilities');
    }
    return true;
  }

  if (name === 'connection' || name === 'profile') {
    if (localOnly()) return true;
    const kind = name === 'connection' ? 'connection' : 'profile';
    if (parts[0] === 'delete' && parts.length === 2) {
      await call({ cmd: `model_${kind}_delete`, id: parts[1] });
      out(`${name === 'connection' ? '连接' : '模型配置'}已删除：${parts[1]}`);
      return true;
    }
    if (parts[0] !== 'save') return usage(`/${name} save <JSON 对象> | delete <id>`);
    let value;
    try { value = JSON.parse(args.trim().replace(/^save\s*/, '')); }
    catch { out('配置 JSON 无效；为保护密钥，不回显输入片段。', 'red'); return true; }
    if (!value || typeof value !== 'object' || Array.isArray(value) || typeof value.id !== 'string') return usage(`/${name} save <包含 id 的 JSON 对象>`);
    await call({ cmd: `model_${kind}_save`, [kind]: value });
    out(`${name === 'connection' ? '连接' : '模型配置'}已保存（敏感内容隐藏）。`);
    return true;
  }

  if (name === 'route') {
    if (!parts.length) { showRoutes((await call({ cmd: 'model_registry' })).registry || {}); return true; }
    if (localOnly()) return true;
    if (parts.length !== 2 || !MODEL_STAGES.includes(parts[0])) return usage(`/route <${MODEL_STAGES.join('|')}> <profile_id>`);
    await call({ cmd: 'model_route_set', stage: parts[0], profile_id: parts[1] });
    out(`路由已保存：${parts[0]} → ${parts[1]}`);
    return true;
  }

  if (name === 'reasoning') {
    if (!parts.length) {
      const { settings = {} } = await call({ cmd: 'settings_get' });
      out(`推理强度 ${settings.llm_reasoning_effort || 'auto'} · 模式 ${settings.llm_thinking_style || 'auto'} · 思考预算 ${settings.llm_thinking_budget ?? '自动'}`);
      out(`/reasoning <${REASONING_EFFORTS.join('|')}> · 更改作用于 legacy 配置；独立模型请用 /profile save。`);
      return true;
    }
    if (localOnly()) return true;
    if (parts.length !== 1 || !REASONING_EFFORTS.includes(parts[0])) return usage(`/reasoning <${REASONING_EFFORTS.join('|')}>`);
    await saveSetting({ llm_reasoning_effort: parts[0] });
    return true;
  }

  if (name === 'model') {
    if (parts[0] === 'capabilities') {
      const { capabilities = {} } = await call({ cmd: 'model_capabilities' });
      showObject(capabilities);
      return true;
    }
    if (!['protocol', 'reasoning', 'thinking', 'budget'].includes(parts[0])) return false;
    if (localOnly()) return true;
    const keys = { protocol: 'llm_protocol', reasoning: 'llm_reasoning_effort', thinking: 'llm_thinking_style', budget: 'llm_thinking_budget' };
    const choices = { protocol: MODEL_PROTOCOLS, reasoning: REASONING_EFFORTS, thinking: THINKING_STYLES };
    const valid = parts[0] === 'budget' ? Number.isSafeInteger(Number(parts[1])) && Number(parts[1]) >= 0 && Number(parts[1]) <= 2000000
      : choices[parts[0]].includes(parts[1]);
    if (parts.length !== 2 || !valid) return usage(`/model ${parts[0]} <${choices[parts[0]]?.join('|') || '0–2000000 整数'}>`);
    await saveSetting({ [keys[parts[0]]]: parts[0] === 'budget' ? Number(parts[1]) : parts[1] });
    return true;
  }

  if (name === 'usage') {
    const result = await call({ cmd: 'usage' });
    // Backend text preserves reported/unknown cache values; never synthesize a rate.
    const content = redactText(result.text || '暂无用量记录；缓存命中率未知。');
    if (parts[0] === 'read' && context.openReport) context.openReport('模型用量与缓存', content);
    else content.split('\n').forEach(line => out(line));
    return true;
  }

  if (name === 'audit') {
    if (parts.length > 1 || (parts[0] && parts[0] !== 'export')) return usage('/audit [export]');
    if (!context.taskId) { out('请先 /open <任务ID> 选择科研任务，再运行 /audit。', 'yellow'); return true; }
    const result = await call({ cmd: 'research_audit', task_id: context.taskId, export: parts[0] === 'export' });
    const content = redactText(typeof result.report === 'string' ? result.report : JSON.stringify(redactSettings(result.analysis || {}), null, 2));
    const report = content + (result.path ? `\n\n导出文件：${result.path}` : '');
    if (context.openReport) context.openReport('科研证据审计', report);
    else report.split('\n').forEach(line => out(line));
    return true;
  }

  if (name === 'skills-selection') {
    if (parts.length !== 1 || !/^[a-zA-Z0-9_-]{1,64}$/.test(parts[0])) return usage('/skills-selection <planning|literature|extraction|audit|experiment|其他阶段>');
    const result = await call({ cmd: 'skills_selection', stage: parts[0] });
    const selection = result.selection || {};
    out(`技能选择 · 阶段 ${selection.stage || parts[0]}`);
    for (const skill of selection.selected || []) out(typeof skill === 'string' ? `● ${skill}` : `● ${skill.name} · ${skill.reason || '已选择'} · ${skill.cost_chars ?? '未知'} 字符`);
    if (!(selection.selected || []).length) out('该阶段未选择技能。');
    for (const skill of selection.deferred || []) out(`暂缓 ${skill.name} · ${skill.status || '预算限制'}`);
    if (selection.missing?.length) out(`未找到：${selection.missing.join(', ')}`);
    if (selection.disabled?.length) out(`已停用：${selection.disabled.join(', ')}`);
    if (selection.budget) out(`上下文预算：${selection.budget.used_chars ?? '未知'}/${selection.budget.limit_chars ?? '未知'} 字符`);
    return true;
  }

  if (name === 'continue' || name === 'cancel-agent') {
    const sessionId = parts[0] || context.currentAgentSession;
    if (parts.length > 1 || !sessionId) return usage(`/${name} <会话ID>（可先 /session <id> 选择会话）`);
    const result = await call({ cmd: name === 'continue' ? 'agent_continue' : 'agent_cancel', session_id: sessionId });
    presentAgentResult(result.result || {}, context, sessionId);
    return true;
  }
  return false;
}
