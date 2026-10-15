import assert from 'node:assert/strict'
import stringWidth from 'string-width'
import { prepareTranscriptRows, markupSpans } from '../src/opentui/components/markup-lines.js'
import { DiffView } from '../src/opentui/components/diff-view.js'
import { applyTheme } from '../src/opentui/theme/theme.js'
import { TOKENS } from '../src/opentui/theme/tokens.js'
import { renderEvents } from '../src/opentui/session-controller.js'

const source = [{ text: '# Results\nRead **carefully** and [source](https://example.org).\n```python\ndef example():\n    return "中文长行" + 1234567890\n```\nDone.' }]
const rows = prepareTranscriptRows(source, 18, { code_show_line_numbers: true, code_wrap_long_lines: true })
const codeRows = rows.filter((row) => row.code)
assert.ok(codeRows.length > 2, 'long code lines wrap into scrollable physical rows')
assert.equal(codeRows[0].gutter.trim(), '1', 'first code row has its own line number')
assert.equal(codeRows[1].gutter.trim(), '2', 'line numbering increments only for source lines')
assert.ok(codeRows.some((row) => !row.gutter.trim()), 'continuation rows do not fabricate line numbers')
const clipped = prepareTranscriptRows(source, 18, { code_show_line_numbers: false, code_wrap_long_lines: false }).filter((row) => row.code)
assert.equal(clipped.length, 2, 'disabling wrap keeps one visual row per source line')
assert.ok(clipped.every((row) => row.gutter === ''), 'line numbers can be removed')
assert.ok(clipped[1].text.endsWith('…'), 'truncated code visibly indicates omitted text')
assert.equal(markupSpans(rows[0])[0].token, 'markdownHeading')
assert.ok(markupSpans({ text: 'Read **carefully**' }).some((span) => span.token === 'markdownStrong'))
assert.ok(markupSpans({ text: '[source](https://example.org)' }).some((span) => span.token === 'markdownLink'))
assert.ok(markupSpans(codeRows[0]).some((span) => span.token === 'syntaxKeyword'))
assert.ok(markupSpans(codeRows[0]).some((span) => span.token === 'syntaxFunction'))
const fenceInUser = prepareTranscriptRows([{ user: true, text: '```python' }, { text: 'plain response' }], 80, {})
assert.equal(fenceInUser.at(-1).code, false, 'user fences cannot colour the next assistant message')
const eventRows = renderEvents([{ kind: 'user_message', payload: { text: '```python' } }, { kind: 'assistant_message', payload: { text: 'plain response' } }])
assert.equal(prepareTranscriptRows(eventRows, 80, {}).at(-1).code, false, 'replayed user fences cannot colour assistant messages')
applyTheme('dark', 'atom-one-dark')
assert.equal(TOKENS.syntaxKeyword, '#c678dd', 'chosen code theme affects the active syntax palette')
applyTheme('light', 'atom-one-light')
assert.equal(TOKENS.syntaxKeyword, '#a626a4')
const diff = DiffView({ diff: '--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b', width: 50, appearance: { code_show_line_numbers: false, code_wrap_long_lines: true } })
assert.equal(diff.props.showLineNumbers, false)
assert.equal(diff.props.wrapMode, 'char')
assert.equal(diff.props.lineNumberFg, TOKENS.diffLineNumber)
// Long responses and submitted prompts must remain readable when scrolling.
for (const user of [false, true]) {
  const text = '科研证据 é 👩‍🔬 ' + 'long research detail '.repeat(7)
  const wrapped = prepareTranscriptRows([{ text, user }], 20, { code_wrap_long_lines: false })
  assert.equal(wrapped.map((row) => row.text).join(''), text, 'prose and user input preserve every character')
  assert.ok(wrapped.length > 1, 'narrow text is exposed as scrollable rows')
  assert.ok(wrapped.every((row) => stringWidth(row.text) <= (user ? 18 : 20)), 'user message padding is included in the row width')
  assert.ok(wrapped.every((row) => !row.text.startsWith('\u200d')), 'wrapping preserves grapheme clusters')
}
const tabbed = 'ABCDEFGHIJKLMNOPQRSTUVWXYZABCD'.split('').join('\t')
const tabRows = prepareTranscriptRows([{ text: tabbed }], 18)
assert.equal(tabRows.map((row) => row.text).join(''), tabbed.replaceAll('\t', '    '), 'prose tab expansion matches the terminal display width')
assert.ok(tabRows.length > 4 && tabRows.every((row) => stringWidth(row.text) <= 18), 'tabbed text has enough physical rows to scroll through every character')
const tabCode = prepareTranscriptRows([{ text: '```text\nA\tB\tC\tD\tE\n```' }], 18, { code_show_line_numbers: false, code_wrap_long_lines: false }).find((row) => row.code)
assert.ok(!tabCode.text.includes('\t') && tabCode.text.endsWith('…'), 'unwrapped code also expands tabs before truncating')
applyTheme('dark')
console.log('opentui-markup: ALL_PASS')
