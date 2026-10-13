// Agent 会话 JSONL → 工作台转写行（子代理进度等事件，纯函数便于单测）。
import fs from 'node:fs';
import path from 'node:path';
import { redactText } from './sensitive-text.js';
import { normalizePaste } from './composer.js';

/** 读取本地会话事件流（data/sessions/<sid>.jsonl）；缺失、损坏或 ID 非法时返回 []。 */
export function readSessionEvents(dir, sid) {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(String(sid || ''))) return [];
  try {
    const text = fs.readFileSync(path.join(dir, 'sessions', `${sid}.jsonl`), 'utf-8');
    return text.split('\n').filter(Boolean).map((line) => {
      try { return JSON.parse(line); } catch { return null; }
    }).filter(event => event && typeof event === 'object');
  } catch {
    return [];
  }
}

/**
 * 子代理进度行：记号风格与现有事件行一致（◆ 流程 / ✔ 成功 / ✘ 失败）。
 * 返回单色 span 数组（{t,c,b}）；非 subagent_status 事件返回 null。
 */
export function subagentLine(event) {
  if (event?.type !== 'subagent_status') return null;
  const p = event.payload || {};
  const agent = p.agent || '子代理';
  const ts = event.timestamp ? `${String(event.timestamp).slice(11, 19)} ` : '';
  const head = { t: ts, c: '#5A6577' };
  if (p.status === 'running') {
    return [head, { t: '◆ ', c: 'cyan', b: true }, { t: `子代理 ${agent} 运行中…`, c: 'cyan' }];
  }
  if (p.status === 'done') {
    return [head, { t: '✔ ', c: 'green', b: true },
      { t: `${agent} 完成（${p.steps ?? 0} 步/${p.tokens ?? 0} tokens）`, c: 'green' }];
  }
  if (['error', 'failed', 'blocked'].includes(p.status)) {
    return [head, { t: '✘ ', c: 'red', b: true }, { t: `${agent} 失败`, c: 'red' }];
  }
  const labels = { waiting_approval: '待审批', waiting_task: '等待科研任务', waiting_children: '等待子任务',
    waiting_human: '等待人工确认', paused: '已暂停', cancelled: '已取消', cancelling: '取消中', pending: '排队中' };
  return [head, { t: '· ', c: 'yellow' }, { t: `${agent} ${labels[p.status] || p.status || '状态未知'}`, c: 'yellow' }];
}

/** Read at most four pages. The cursor is opaque and committed only after success. */
export async function readEventPages(bridge, sessionId, cursor = null) {
  let next = cursor, reset = false, more = false, events = [];
  for (let index = 0; index < 4; index++) {
    let page;
    try {
      page = await bridge.call({ cmd: 'session_events', session_id: sessionId, cursor: next, limit: 200, tail: true }, { timeoutMs: 10000 });
      if (page?.ok === false) throw new Error(page.error || '会话事件读取失败');
    } catch (error) {
      if (/未知命令|unknown command|不支持此命令|not supported|HTTP 403/i.test(String(error.message))) return { unsupported: true };
      throw error;
    }
    // Older bridge mocks/proxies may silently ignore unknown commands.
    if (!Array.isArray(page?.events)) return { unsupported: true };
    if (page.reset) { events = []; reset = true; }
    events.push(...page.events);
    more = Boolean(page.has_more);
    const before = next;
    next = page.cursor ?? null;
    if (!more || next === before) break;
  }
  return { events, cursor: next, reset, has_more: more };
}

const MAX_TEXT = 32000;
const boundedText = value => {
  const text = redactText(String(value ?? ''));
  return text.length > MAX_TEXT ? '…较早内容已省略，请用 /session 查看完整记录…\n' + text.slice(-MAX_TEXT) : text;
};
export const newAgentTranscript = () => ({ turn: 0, serial: 0, open: null, latest: null, blocks: {}, tools: {}, fallback: null });
const copyState = state => ({ ...state, blocks: { ...state.blocks }, tools: { ...state.tools } });
const rememberBlock = (state, key, value) => {
  state.blocks[key] = value;
  const keys = Object.keys(state.blocks);
  if (keys.length > 32) delete state.blocks[keys[0]];
};
const toolText = value => normalizePaste(redactText(String(value ?? '')));
const toolKey = (state, id) => typeof id === 'string' && id ? `tool-${state.turn}-${JSON.stringify(id)}` : null;
const rememberTool = (state, key, tool) => {
  state.tools[key] = tool;
  const keys = Object.keys(state.tools);
  if (keys.length > 32) delete state.tools[keys[0]];
};
const toolChange = (key, tool) => ({ id: key,
  text: `${tool.complete ? tool.failed ? '✘' : '✔' : '⚙'} ${tool.name} · ${tool.status}`
    + (tool.detail ? `\n${tool.detail.trimEnd()}` : '') });

function reduceToolEvent(state, event, changes) {
  const payload = event.payload || {};
  const key = toolKey(state, event.type === 'shell_output' ? payload.tool_call_id : payload.id);
  if (!key) return;
  const previous = state.tools[key];
  let tool;
  if (event.type === 'tool_call') {
    if (previous) return;
    tool = { name: toolText(payload.name || '工具').slice(0, 120), detail: '',
      status: '执行中…', complete: false, failed: false };
  } else {
    // Tail reads may start after tool_call; never attach them to another turn.
    if (!previous || previous.complete) return;
    tool = { ...previous };
    if (event.type === 'shell_output') {
      if (tool.name !== 'shell.run' || !['stdout', 'stderr'].includes(payload.stream)
          || typeof payload.text !== 'string' || !payload.text) return;
      tool.detail = toolText(tool.detail + payload.text).slice(-1000);
      if (tool.detail === previous.detail) return;
    } else {
      const result = payload.result || {};
      tool.complete = true;
      tool.failed = result.ok !== true || result.cancelled || result.timed_out
        || (result.exit_code != null && result.exit_code !== 0);
      tool.status = result.cancelled ? '已停止' : result.timed_out ? '已超时'
        : result.exit_code != null && result.exit_code !== 0 ? `退出码 ${toolText(result.exit_code)}`
          : tool.failed ? '执行失败' : result.capture_complete === false ? '已完成 · 部分输出' : '已完成';
      if (!tool.detail) tool.detail = toolText(result.error || result.stdout || result.stderr || '').slice(-1000);
    }
  }
  rememberTool(state, key, tool);
  changes.set(key, toolChange(key, tool));
}

/** Event pages become block replacements, never one transcript row per token. */
export function reduceAgentEvents(previous, events, { reset = false } = {}) {
  const state = reset ? newAgentTranscript() : copyState(previous);
  const changes = new Map();
  for (const event of events || []) {
    const payload = event?.payload || {};
    if (event.type === 'user_message') { state.turn++; state.open = null; state.latest = null; continue; }
    if (['tool_call', 'shell_output', 'tool_result'].includes(event.type)) {
      reduceToolEvent(state, event, changes);
      continue;
    }
    if (event.type === 'steer_applied') {
      const key = `steer-${payload.steer_id}`;
      changes.set(key, { id: key, text: '本轮补充已应用：' + boundedText(payload.text) });
      state.open = null;
      continue;
    }
    if (event.type === 'subagent_status') {
      const spans = subagentLine(event);
      changes.set(`child-${payload.child || payload.agent || 'unknown'}`, { id: `child-${payload.child || payload.agent || 'unknown'}`, spans });
      continue;
    }
    if (!['assistant_delta', 'assistant_message'].includes(event.type)) continue;
    const complete = event.type === 'assistant_message';
    let key = payload.step != null ? `assistant-${state.turn}-${payload.step}`
      : state.open || `assistant-${state.turn}-message-${++state.serial}`;
    const old = state.blocks[key];
    if (!complete && old?.complete) continue;
    const text = boundedText(complete ? payload.content : (old?.text || '') + String(payload.delta || ''));
    if (!text && !old) continue;
    rememberBlock(state, key, { text, complete });
    state.latest = key;
    state.open = complete ? null : key;
    const fallback = state.fallback;
    if (fallback && fallback.text === text && complete) {
      if (key !== fallback.key) changes.set(key, { id: key, remove: true });
      changes.set(fallback.key, { id: fallback.key, text });
      state.latest = fallback.key;
      state.fallback = null;
    } else if (fallback && fallback.text.startsWith(text) && !complete) {
      // A fast result may precede its durable event page. Keep its full block.
    } else if (old?.text !== text || reset) {
      changes.set(key, { id: key, text });
    }
  }
  return { state, changes: [...changes.values()] };
}

/** Reconcile a final reply even when a legacy server has no event endpoint. */
export function reduceAgentResult(previous, result) {
  const answer = result.answer || result.message || result.content;
  if (typeof answer !== 'string' || !answer) return { state: previous, changes: [] };
  const text = boundedText(answer);
  const latest = previous.blocks[previous.latest];
  if (latest?.text === text || previous.fallback?.text === text) return { state: previous, changes: [] };
  const state = copyState(previous);
  const key = latest && text.startsWith(latest.text) ? state.latest : `result-${state.turn}-${++state.serial}`;
  state.fallback = { key, text };
  // Keep raw accumulated deltas intact until assistant_message seals that step.
  if (!state.blocks[key]) rememberBlock(state, key, { text, complete: true });
  return { state, changes: [{ id: key, text }] };
}

function wrapText(text, width) {
  const lines = [];
  for (const paragraph of text.split('\n')) {
    let current = '', used = 0;
    for (const char of paragraph) {
      const size = char.codePointAt(0) > 0x2e7f ? 2 : 1;
      if (used + size > width && current) { lines.push(current); current = ''; used = 0; }
      current += char; used += size;
    }
    lines.push(current);
  }
  return lines;
}

/** Preserve command output and replace only the affected session's tagged rows. */
export function mergeAgentLines(previous, changes, { sessionKey, reset = false, width = 72 }) {
  if (!reset && !changes.length) return previous;
  let lines = reset ? previous.filter(row => row[0]?.agentSession !== sessionKey) : previous.slice();
  for (const change of changes) {
    const matches = row => row[0]?.agentSession === sessionKey && row[0]?.agentBlock === change.id;
    const index = lines.findIndex(matches);
    const old = index < 0 ? [] : lines.filter(matches);
    const raw = change.remove ? [] : change.spans ? [change.spans]
      : wrapText(change.text || '', Math.max(12, width)).map(text => [{ t: text }]);
    const rows = raw.map(spans => spans.map((span, position) => ({ ...span, t: redactText(span.t),
      ...(position === 0 ? { agentSession: sessionKey, agentBlock: change.id } : {}) })));
    if (JSON.stringify(old) === JSON.stringify(rows)) continue;
    lines = lines.filter(row => !matches(row));
    lines.splice(index < 0 ? lines.length : index, 0, ...rows);
  }
  const result = lines.slice(-200);
  return result.length === previous.length && result.every((row, index) => row === previous[index]) ? previous : result;
}
