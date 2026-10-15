// Line-oriented rendering keeps long research sessions scrollable without
// mounting a full Markdown document for every streaming update.
import stringWidth from 'string-width'
import { clipToWidth } from '../layout/width-budget.js'
import { normalizeAppearance } from '../theme/preferences.js'

function wrapCells(text, width) {
  const rows = []
  let row = '', used = 0
  for (const ch of text) {
    const size = stringWidth(ch)
    if (row && used + size > width) { rows.push(row); row = ''; used = 0 }
    row += ch
    used += size
  }
  rows.push(row)
  return rows
}

export function prepareTranscriptRows(lines = [], width = 80, preferences = {}) {
  const prefs = normalizeAppearance(preferences)
  let fence = null, codeLine = 0
  const rows = []
  for (const line of lines) {
    if (line.kind === 'diff') { rows.push(line); fence = null; continue }
    const prose = !line.user && !line.dim && !line.kind
    if (!prose) fence = null
    for (const raw of String(line.text ?? '').split('\n')) {
      const match = prose && raw.match(/^\s*(`{3,}|~{3,})(.*)$/)
      if (match && (!fence || (match[1][0] === fence[0] && match[1].length >= fence.length))) {
        fence = fence ? null : match[1]
        codeLine = 0
        rows.push({ ...line, text: clipToWidth(raw, width), fence: true, code: false, gutter: '' })
        continue
      }
      const code = Boolean(fence && prose)
      if (code) codeLine++
      const gutter = code && prefs.code_show_line_numbers ? `${String(codeLine).padStart(3)} ` : ''
      const available = Math.max(4, width - stringWidth(gutter))
      const visualRows = code && prefs.code_wrap_long_lines ? wrapCells(raw.replaceAll('\t', '    '), available) : [clipToWidth(raw, available)]
      visualRows.forEach((text, i) => rows.push({ ...line, text, code, gutter: i === 0 ? gutter : ' '.repeat(gutter.length) }))
    }
  }
  return rows
}

const CODE_PATTERN = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(#.*|\/\/.*)|(\b(?:def|class|return|import|from|as|async|await|if|else|elif|for|while|try|except|finally|with|yield|lambda|const|let|var|function|export|default|new|throw|true|false|null|None|True|False)\b)|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_]\w*(?=\s*\())|(\b[A-Z]\w*\b)|([+*=<>!&|%/-]+)|([()[\]{},.:;])/g

function splitTokens(text, pattern, tokenFor, fallback) {
  const spans = []
  let end = 0
  for (const match of text.matchAll(pattern)) {
    if (match.index > end) spans.push({ text: text.slice(end, match.index), token: fallback })
    spans.push({ text: match[0], token: tokenFor(match) })
    end = match.index + match[0].length
  }
  if (end < text.length || !spans.length) spans.push({ text: text.slice(end), token: fallback })
  return spans
}

export function markupSpans(row) {
  const text = row.text || ''
  if (row.dim) return [{ text, token: 'textMuted' }]
  if (row.user || (row.kind && row.kind !== 'markdown')) return [{ text, token: 'text' }]
  if (row.fence) return [{ text, token: 'syntaxComment' }]
  if (row.code) {
    const names = ['syntaxString', 'syntaxComment', 'syntaxKeyword', 'syntaxNumber', 'syntaxFunction', 'syntaxType', 'syntaxOperator', 'syntaxPunctuation']
    return splitTokens(text, CODE_PATTERN, (match) => names[match.slice(1).findIndex((part) => part !== undefined)], 'syntaxVariable')
  }
  if (/^\s{0,3}#{1,6}\s/.test(text)) return [{ text, token: 'markdownHeading', bold: true }]
  if (/^\s*>/.test(text)) return [{ text, token: 'markdownBlockQuote' }]
  if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(text)) return [{ text, token: 'markdownHorizontalRule' }]
  const spans = splitTokens(text, /(`[^`]+`)|(\[[^\]]+\]\([^)]+\)|https?:\/\/\S+)|(\*\*[^*]+\*\*)|(\*[^*]+\*)/g,
    (match) => match[1] ? 'markdownCode' : match[2] ? 'markdownLink' : match[3] ? 'markdownStrong' : 'markdownEmph', 'markdownText')
  if (/^\s*(?:[-*+]\s|\d+\.\s)/.test(text)) spans[0] = { ...spans[0], token: 'markdownListItem' }
  return spans.map((span) => ({ ...span, bold: span.bold || span.token === 'markdownStrong' }))
}
