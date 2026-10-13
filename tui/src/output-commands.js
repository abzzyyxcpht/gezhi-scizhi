import { redactText } from './sensitive-text.js';
import { normalizePaste } from './composer.js';

export const OUTPUT_ID = /^out_[a-f0-9]{32}$/;
const number = value => /^\d+$/.test(value || '') && Number.isSafeInteger(Number(value));

export const OUTPUT_USAGE = '用法：/outputs [列表偏移] · /outputs read <输出ID> [字节偏移] · /outputs search <输出ID> <字节偏移> <原文>';

/**
 * 解析 /outputs 参数为 bridge 请求（含 session_id）；无法识别时返回 null，调用方给用法提示。
 * Ink 与 OpenTUI 两个引擎共用同一解析，避免字节偏移/ID 校验口径漂移。
 * @param raw 命令参数原文
 * @param sessionId 当前会话 ID
 * @returns {{request: object, format: 'list'|'read'|'search'} | null}
 */
export function parseOutputRequest(raw, sessionId) {
  const text = String(raw || '').trim();
  const read = text.match(/^read (\S+)(?: (\d+))?$/);
  const search = text.match(/^search (\S+) (\d+) ([\s\S]+)$/);
  if (read && OUTPUT_ID.test(read[1]) && (read[2] === undefined || number(read[2]))) {
    return { request: { cmd: 'session_tool_output_read', session_id: sessionId, output_id: read[1], offset: Number(read[2] || 0), limit: 4000 }, format: 'read' };
  }
  if (search && OUTPUT_ID.test(search[1]) && number(search[2]) && search[3].length <= 200) {
    return { request: { cmd: 'session_tool_output_search', session_id: sessionId, output_id: search[1], offset: Number(search[2]), query: search[3], limit: 10 }, format: 'search' };
  }
  if (!text || number(text)) {
    return { request: { cmd: 'session_tool_outputs', session_id: sessionId, offset: Number(text || 0), limit: 20 }, format: 'list' };
  }
  return null;
}

/**
 * 工具输出阅读行：身份核对 + 结构校验（失败 throw，由调用方决定呈现方式）。
 * 输出是待核查材料；读取不会重放原工具。
 * @param value bridge 回复的 output 字段
 * @param format 'list'|'read'|'search'
 * @param sessionId 发起时选中的会话 ID
 * @param request parseOutputRequest 给出的请求（用于 output_id 核对）
 * @returns {string[]} 原始行（脱敏与归一化由调用方按各自呈现口径处理）
 */
export function toolOutputLines(value, format, sessionId, request) {
  if (value?.session_id !== sessionId || (request.output_id && value.output_id !== request.output_id)) throw new Error('工具输出身份不匹配');
  const lines = [`会话 ${sessionId} · 已归档工具输出`, '输出是待核查材料；读取不会重放原工具。', ''];
  if (format === 'list') {
    if (!Array.isArray(value.items)) throw new Error('输出列表结构无效');
    for (const row of value.items) {
      if (!OUTPUT_ID.test(row.output_id)) throw new Error('输出 ID 无效');
      lines.push(`${row.output_id} · ${row.tool} · ${row.total_bytes} 字节`, `/outputs read ${row.output_id}`, '');
    }
    if (!value.items.length) lines.push('当前范围没有已归档输出。');
    for (const error of value.errors || []) lines.push(`无法读取：${error.output_id || ''} ${error.error || error.code || ''}`);
    if (value.next_offset != null) lines.push(`下一页：/outputs ${value.next_offset}`);
  } else if (format === 'read') {
    if (typeof value.text !== 'string') throw new Error('输出正文结构无效');
    lines.push(`${value.output_id} · 字节偏移 ${value.offset}/${value.total_bytes}`, value.text, '');
    if (!value.eof && value.next_offset != null) lines.push(`下一段：/outputs read ${value.output_id} ${value.next_offset}`);
    else lines.push('已读至结尾。');
  } else {
    if (!Array.isArray(value.matches)) throw new Error('输出搜索结构无效');
    for (const match of value.matches) lines.push(`字节 ${match.offset}：${match.preview}`, `/outputs read ${value.output_id} ${match.offset}`, '');
    if (!value.matches.length) lines.push('当前范围未命中字面文本。');
    if (!value.eof && value.next_offset != null) lines.push(`继续搜索：/outputs search ${value.output_id} ${value.next_offset} ${request.query}`);
  }
  return lines;
}

export async function runOutputCommand(name, args, context) {
  if (name !== 'outputs') return false;
  const { bridge, out, currentAgentSession: sid } = context;
  if (!sid) { out('先用 /session <ID> 选择会话。'); return true; }
  const parsed = parseOutputRequest(args, sid);
  if (!parsed) { out(OUTPUT_USAGE); return true; }
  const { request, format } = parsed;
  const connection = JSON.stringify(bridge.connection?.() || {});
  const reply = await bridge.call(request);
  if (reply?.ok !== true) throw new Error(redactText(reply?.error || '工具输出读取失败'));
  const lines = toolOutputLines(reply.output, format, sid, request);
  if (connection !== JSON.stringify(bridge.connection?.() || {}) || context.isCurrentSession?.(sid) === false) return true;
  const text = normalizePaste(redactText(lines.join('\n')));
  if (context.openReport) context.openReport('工具输出', text); else out(text);
  return true;
}
