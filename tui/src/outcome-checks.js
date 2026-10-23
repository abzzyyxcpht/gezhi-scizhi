// 成果验收只呈现后端事实；异常回执不能开启人工复核。
import { redactText, redactSettings } from './sensitive-text.js'
//
// 状态与整体结论的措辞是硬约束：pending/unknown 绝不能说成完成，
// all_satisfied 也必须声明"只核对记录中的事实，不代表科学结论正确"。

export const OUTCOME_STATE_LABELS = {
  satisfied: { symbol: '✓', label: '已核实' },
  failed: { symbol: '✗', label: '未通过' },
  stale: { symbol: '⚠', label: '已过期' },
  pending: { symbol: '○', label: '待人工确认' },
  unknown: { symbol: '?', label: '无法核验' },
}

export const OUTCOME_VERDICT_LABELS = {
  all_satisfied: '全部检查项已有记录支持',
  needs_review: '需要复核',
  incomplete: '尚未完成',
  unknown: '无法给出结论',
}

export const OUTCOME_BOUNDARY = '边界：只核对任务记录中已有的事实，不判断科学结论是否正确；未通过只表示现有记录不足以支持「已完成」'
export const OUTCOME_STALE_HINT = '原结论需要复核'

const CONTROL = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]/u
const CONTROLS = new RegExp(CONTROL.source, 'gu')
const TASK_ID = /^[A-Za-z0-9_-]{1,128}$/
const REVISION = /^[a-f0-9]{64}$/
const STATES = Object.keys(OUTCOME_STATE_LABELS)
const DECISIONS = { approved: '通过复核', changes_requested: '要求修改' }
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const filled = value => typeof value === 'string' && value.trim().length > 0 && !CONTROL.test(value)

export function outcomeText(value, limit = Infinity) {
  if (typeof value !== 'string') return ''
  const clean = redactText(value).replace(CONTROLS, ' ').replace(/\s+/g, ' ').trim()
  if (!clean) return ''
  const chars = Array.from(clean)
  return chars.length > limit ? `${chars.slice(0, Math.max(0, limit - 1)).join('')}…` : clean
}

function stateLine(item, clean) {
  const meta = OUTCOME_STATE_LABELS[item?.state] || { symbol: '?', label: '无法核验' }
  const title = clean(item?.title || item?.id) || '（未命名检查项）'
  const detail = clean(item?.detail)
  let line = `${meta.symbol} ${meta.label} · ${title}`
  if (detail) line += ` · ${detail}`
  if (item?.state === 'stale') line += `（${OUTCOME_STALE_HINT}）`
  return line
}

// 只校验回执内部是否一致，不从文字描述重新推断科研结论。
export function validOutcomeChecks(checks) {
  if (!object(checks) || typeof checks.revision !== 'string' || !REVISION.test(checks.revision)
    || !Array.isArray(checks.items) || checks.items.length === 0 || !object(checks.counts)) return false
  const counts = Object.fromEntries(STATES.map(state => [state, 0]))
  for (const item of checks.items) {
    if (!object(item) || !STATES.includes(item.state) || !filled(item.id) || !filled(item.title)
      || typeof item.detail !== 'string' || !Array.isArray(item.evidence)) return false
    counts[item.state]++
  }
  if (checks.total !== checks.items.length || checks.satisfied !== counts.satisfied
    || STATES.some(state => checks.counts[state] !== counts[state])) return false
  const verdict = counts.satisfied === checks.total ? 'all_satisfied'
    : counts.failed || counts.stale ? 'needs_review' : 'incomplete'
  return checks.verdict === verdict
}

const EVIDENCE_LABELS = {
  artifact: '产物', material: '材料', sha256: '材料哈希', recorded_sha256: '记录哈希', current_sha256: '当前哈希',
  citation: '参考文献', citations: '参考文献数', deficits: '缺口数', code: '缺口编号', message: '说明',
  by: '复核人', note: '复核说明', at: '复核时间', decision: '复核意见', revision: '复核版本',
}

function evidenceText(evidence, clean) {
  const safe = redactSettings(evidence)
  if (!object(safe)) return clean(typeof safe === 'string' ? safe : JSON.stringify(safe))
  return Object.entries(safe).map(([key, value]) => {
    const label = EVIDENCE_LABELS[key] || key
    const text = key === 'decision' && DECISIONS[value] ? DECISIONS[value]
      : value === null ? '未记录' : typeof value === 'object' ? JSON.stringify(value) : String(value)
    return `${clean(label)}：${clean(text)}`
  }).join('；')
}

/** 完整文本交给转写区按终端列宽折行；不在此截掉依据。 */
export function outcomeCheckLines(checks, options = {}) {
  const redact = typeof options.redact === 'function' ? options.redact : (value) => value
  const clean = (value, limit) => outcomeText(typeof value === 'string' ? redact(value) : value, limit)
  if (!validOutcomeChecks(checks)) return ['成果验收：读取失败（清单缺失、为空或字段不一致，无法核验）', OUTCOME_BOUNDARY]
  const lines = [`成果验收：${OUTCOME_VERDICT_LABELS[checks.verdict]}（${checks.satisfied}/${checks.total} 项已有记录支持）`]
  for (const item of checks.items) {
    lines.push(`  ${stateLine(item, clean)}`)
    if (options.details) {
      if (item.criterion) lines.push(`    检查标准：${clean(item.criterion)}`)
      for (const evidence of item.evidence) lines.push(`    依据：${evidenceText(evidence, clean)}`)
      if (!item.evidence.length) lines.push('    依据：尚无记录')
    }
  }
  lines.push(`统计：${STATES.map(state => `${OUTCOME_STATE_LABELS[state].label} ${checks.counts[state]}`).join(' · ')}`)
  lines.push(OUTCOME_BOUNDARY)
  return lines
}

// 专用词法解析，支持单双引号及引号内的转义，不执行 shell 展开。
function tokensFor(text) {
  const tokens = []
  let token = '', quote = '', started = false
  for (let i = 0; i < text.length; i++) {
    const char = text[i]
    if (quote) {
      if (char === quote) quote = ''
      else if (char === '\\' && [quote, '\\'].includes(text[i + 1])) token += text[++i]
      else token += char
    } else if (char === '"' || char === "'") { quote = char; started = true }
    else if (/\s/u.test(char)) {
      if (started) { tokens.push(token); token = ''; started = false }
    } else { token += char; started = true }
  }
  if (quote) return null
  if (started) tokens.push(token)
  return tokens
}

/** 显式决策必填；旧位置参数语法不能默认为通过。 */
export function parseCheckArgs(raw) {
  const usage = error => ({ mode: 'usage', error })
  if (typeof raw === 'string' && CONTROL.test(raw)) return usage('命令不能包含控制字符或不可见方向标记')
  const text = typeof raw === 'string' ? raw.trim() : ''
  if (!text) return { mode: 'read', taskId: '' }
  const tokens = tokensFor(text)
  if (!tokens) return usage('引号未闭合；姓名和说明请使用成对引号')
  if (tokens[0] !== 'review') {
    const details = tokens.at(-1) === '--details'
    const args = details ? tokens.slice(0, -1) : tokens
    if (args.length > 1 || (args.length && !TASK_ID.test(args[0]))) return usage('请提供有效的任务 ID；展开依据可加 --details')
    return { mode: 'read', taskId: args[0] || '', ...(details ? { details: true } : {}) }
  }
  const taskId = tokens[1]
  if (!taskId || !TASK_ID.test(taskId)) return usage('任务 ID 格式无效')
  const options = new Map()
  for (let i = 2; i < tokens.length; i += 2) {
    const key = tokens[i], value = tokens[i + 1]
    if (!['--decision', '--by', '--note'].includes(key)) return usage('仅支持 --decision、--by、--note；包含空格的值请加引号')
    if (options.has(key)) return usage(`参数 ${key} 不能重复`)
    if (!value?.trim() || value.startsWith('--')) return usage(`参数 ${key} 缺少内容`)
    options.set(key, value.trim())
  }
  const decision = options.get('--decision'), by = options.get('--by'), note = options.get('--note')
  if (!Object.hasOwn(DECISIONS, decision)) return usage('请明确选择 --decision approved（通过）或 changes_requested（要求修改）')
  if (!by || Array.from(by).length > 80) return usage('复核人需为 1–80 字')
  if (!note || Array.from(note).length > 2000) return usage('复核说明需为 1–2000 字')
  return { mode: 'review', taskId, decision, by, note }
}

export function reviewResultLines(result) {
  const review = result?.review
  if (result?.ok === true && filled(result.task_id) && object(review) && filled(review.by)
    && filled(review.note) && typeof review.revision === 'string' && REVISION.test(review.revision)
    && filled(review.at) && Number.isFinite(Date.parse(review.at)) && Object.hasOwn(DECISIONS, review.decision)) {
    return [`已记录：${DECISIONS[review.decision]}（${outcomeText(review.by)}）；请重新 /checks ${outcomeText(result.task_id)} 查看清单`]
  }
  const reason = outcomeText(result?.error, 120) || '后端未返回有效回执'
  return [`未确认复核结果：${reason}。请重新读取 /checks，确认记录后再操作；本次不会自动重发。`]
}

export function checkUsageLines() {
  return [
    '用法：/checks [任务ID] [--details]；--details 展开检查标准和完整依据',
    '复核：/checks review <任务ID> --decision approved|changes_requested --by "姓名" --note "说明"',
    'approved 表示通过，changes_requested 表示要求修改；先查看清单再复核。/tasks 查看任务 ID。',
  ]
}
