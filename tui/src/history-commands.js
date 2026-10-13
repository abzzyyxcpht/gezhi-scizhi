import { redactText } from './sensitive-text.js';
const views = new WeakMap();

export async function runHistoryCommand(name, args, context) {
  if (name !== 'history') return false;
  const { bridge, out, currentAgentSession: sid } = context;
  if (!sid) { out('请先 /session <ID> 选择要阅读的会话。'); return true; }
  const raw = String(args || '').trim();
  const connection = bridge.connection?.() || {};
  const key = JSON.stringify([connection.mode, connection.url, sid]);
  let saved = views.get(bridge);
  if (!saved) { saved = new Map(); views.set(bridge, saved); }
  const prior = saved.get(key);
  const call = async request => {
    const reply = await bridge.call(request);
    if (reply?.ok !== true) throw new Error(redactText(reply?.error || '历史读取失败'));
    return reply;
  };
  if (raw.startsWith('message ')) {
    const match = raw.match(/^message ([1-9]\d*)(?: (\d+))?$/);
    if (!match || !prior) { out('先用 /history 打开历史，再 /history message <消息ID> [字符偏移] 分段阅读。'); return true; }
    const id = Number(match[1]), offset = Number(match[2] || 0);
    if (!Number.isSafeInteger(id) || !Number.isSafeInteger(offset)) throw new Error('消息位置无效');
    const result = (await call({ cmd: 'session_history_message', session_id: sid, message_id: id,
      generation: prior.generation, offset, limit: 20000 })).message;
    if (result?.session_id !== sid || result.message_id !== id || result.generation !== prior.generation) throw new Error('消息身份不匹配');
    const text = [`${result.kind} · 字符 ${result.offset + 1}–${result.offset + [...result.content].length}/${result.characters}`, '', result.content,
      '', result.next_offset == null ? '消息已读至结尾。' : `下一段：/history message ${id} ${result.next_offset}`].join('\n');
    if (context.openReport) context.openReport(`历史消息 ${id}`, redactText(text)); else out(redactText(text));
    return true;
  }
  const match = raw.match(/^([1-9]\d*)(?:\s+(.*))?$/s);
  const number = match ? Number(match[1]) : undefined;
  if (number && !Number.isSafeInteger(number)) throw new Error('页码无效');
  const query = match ? (match[2] ?? prior?.query ?? '') : raw.startsWith('search ') ? raw.slice(7).trim()
    : raw === 'refresh' || !raw ? '' : raw;
  const result = (await call({ cmd: 'session_history', session_id: sid, query, page_size: 40,
    ...(number ? { page: number } : {}), ...(number && prior?.query === query ? { snapshot: prior.snapshot } : {}) })).history;
  if (result?.session_id !== sid || result.query !== query || !Array.isArray(result.messages)) throw new Error('历史分页身份不匹配');
  saved.set(key, { query, snapshot: result.snapshot, generation: result.generation });
  const lines = [`会话 ${sid} · 第 ${result.page}/${result.pages} 页 · ${result.total} 条${query ? ` · 搜索 ${query}` : ''}`,
    result.reset ? '历史已改写，当前页使用新的快照。' : '已保存历史快照。',
    result.new_messages ? '有新增消息；/history refresh 查看最新范围。' : '', ''];
  for (const row of result.messages) {
    lines.push(`#${row.id} · ${row.kind === 'steer_applied' ? '本轮补充' : row.role === 'user' ? '用户' : '智能体'} · ${row.timestamp || ''}`, row.content);
    if (row.truncated) lines.push(`展示已截断；/history message ${row.id} 分段查看完整 ${row.characters} 字符。`);
    lines.push('');
  }
  lines.push(`换页：/history <页码>　新搜索：/history search <关键词>　刷新：/history refresh`);
  const text = redactText(lines.filter(line => line != null).join('\n'));
  if (context.openReport) context.openReport('对话记录', text); else out(text);
  return true;
}
