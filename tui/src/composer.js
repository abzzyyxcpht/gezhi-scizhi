// Pure draft editing. Offsets are UTF-16 boundaries between complete graphemes.
import stringWidth from 'string-width';

export const MAX_INPUT_CHARS = 32000;
const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
// A few recent drafts support editing/undo without retaining an unbounded
// history. Caret moves reuse segmentation; terminal resizes reuse the same text
// and keep at most two layouts. Neither cache depends on the visible caret.
const drafts = new Map();
const glyphWidths = new Map();
const TEXT_CACHE_LIMIT = 3;
const LAYOUT_CACHE_LIMIT = 2;
const GLYPH_CACHE_LIMIT = 1024;
const retain = (cache, key, value, limit) => {
  cache.delete(key);
  cache.set(key, value);
  if (cache.size > limit) cache.delete(cache.keys().next().value);
  return value;
};
const getDraft = text => {
  let draft = drafts.get(text);
  if (!draft) {
    const items = [];
    let logicalLines = 1;
    for (const { segment, index } of segmenter.segment(text)) {
      if (segment === '\n') logicalLines++;
      items.push({ index, segment, display: segment === '\t' ? '    ' : segment });
    }
    draft = { items, boundaries: [...items.map(item => item.index), text.length], logicalLines, layouts: new Map() };
  }
  return retain(drafts, text, draft, TEXT_CACHE_LIMIT);
};
const lowerBound = (values, value, select = item => item) => {
  let low = 0, high = values.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (select(values[middle]) < value) low = middle + 1;
    else high = middle;
  }
  return low;
};
const snapCaret = (text, caret) => {
  const offsets = getDraft(text).boundaries;
  return offsets[lowerBound(offsets, Math.max(0, Math.min(text.length, caret)))];
};
const edited = (text, caret) => ({ text, caret: snapCaret(text, caret) });
const glyphWidth = display => {
  // Common single-code-unit glyphs need no ANSI/emoji regex machinery. These
  // two unified ideograph blocks are East Asian Wide, including their reserved
  // codepoints; combining or variation sequences still use string-width.
  if (display.length === 1) {
    const code = display.charCodeAt(0);
    if (code >= 0x20 && code <= 0x7e) return 1;
    if ((code >= 0x3400 && code <= 0x4dbf) || (code >= 0x4e00 && code <= 0x9fff)) return 2;
  }
  const cached = glyphWidths.get(display);
  if (cached !== undefined) return cached;
  const width = stringWidth(display);
  // A single grapheme may contain thousands of combining codepoints. Do not
  // retain those strings in a cache shared across otherwise-evicted drafts.
  if (display.length <= 64) retain(glyphWidths, display, width, GLYPH_CACHE_LIMIT);
  return width;
};
const itemWidth = item => item.width ??= glyphWidth(item.display);

export function previousBoundary(text, caret) {
  const offsets = getDraft(text).boundaries;
  return offsets[Math.max(0, lowerBound(offsets, caret) - 1)];
}
export function nextBoundary(text, caret) {
  const offsets = getDraft(text).boundaries;
  const index = lowerBound(offsets, caret);
  return offsets[index + (offsets[index] === caret ? 1 : 0)] ?? text.length;
}
const stripOsc = text => {
  const parts = [];
  let index = 0, start = 0;
  while (index < text.length) {
    const prefix = text[index] === '\x9d' ? 1 : text[index] === '\x1b' && text[index + 1] === ']' ? 2 : 0;
    if (!prefix) { index++; continue; }
    let end = index + prefix, terminator = 0;
    for (; end < text.length; end++) {
      if (text[end] === '\x07' || text[end] === '\x9c') { terminator = 1; break; }
      if (text[end] === '\x1b' && text[end + 1] === '\\') { terminator = 2; break; }
    }
    // No terminator remains anywhere after this point. Keep printable text
    // rather than swallowing it; the control-byte pass below removes ESC/C1.
    // Breaking also avoids quadratic rescanning of many unfinished OSC starts.
    if (!terminator) break;
    parts.push(text.slice(start, index));
    index = end + terminator;
    start = index;
  }
  parts.push(text.slice(start));
  return parts.join('');
};
export function normalizePaste(value) {
  return stripOsc(String(value || '').replace(/\r\n?/g, '\n'))
    .replace(/(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]/g, '')
    .replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, '');
}
export function insertText(state, value) {
  const inserted = normalizePaste(value);
  if (state.text.length + inserted.length > MAX_INPUT_CHARS) return { ...state, rejected: true };
  const caret = snapCaret(state.text, state.caret);
  return edited(state.text.slice(0, caret) + inserted + state.text.slice(caret), caret + inserted.length);
}
const lineStart = (text, caret) => caret <= 0 ? 0 : text.lastIndexOf('\n', caret - 1) + 1;
const lineEnd = (text, caret) => { const end = text.indexOf('\n', caret); return end < 0 ? text.length : end; };

export function editText(state, action) {
  const { text } = state;
  const caret = snapCaret(text, state.caret);
  if (action === 'left') return { text, caret: previousBoundary(text, caret) };
  if (action === 'right') return { text, caret: nextBoundary(text, caret) };
  if (action === 'home') return { text, caret: lineStart(text, caret) };
  if (action === 'end') return { text, caret: lineEnd(text, caret) };
  if (action === 'start') return { text, caret: 0 };
  if (action === 'finish') return { text, caret: text.length };
  if (action === 'backspace') {
    const before = previousBoundary(text, caret);
    return edited(text.slice(0, before) + text.slice(caret), before);
  }
  if (action === 'delete') return edited(text.slice(0, caret) + text.slice(nextBoundary(text, caret)), caret);
  if (action === 'kill-start') return edited(text.slice(0, lineStart(text, caret)) + text.slice(caret), lineStart(text, caret));
  if (action === 'kill-end') return edited(text.slice(0, caret) + text.slice(lineEnd(text, caret)), caret);
  if (action === 'kill-word') {
    const left = text.slice(0, caret).replace(/\s*\S*$/, '');
    return edited(left + text.slice(caret), left.length);
  }
  if (action === 'up' || action === 'down') {
    const start = lineStart(text, caret), end = lineEnd(text, caret);
    const targetStart = action === 'up' ? (start ? lineStart(text, start - 1) : start) : (end < text.length ? end + 1 : start);
    if (targetStart === start) return state;
    const draft = getDraft(text);
    const first = lowerBound(draft.boundaries, start), current = lowerBound(draft.boundaries, caret);
    const desired = state.goalColumn ?? draft.items.slice(first, current).reduce((sum, item) => sum + itemWidth(item), 0);
    const targetEnd = lineEnd(text, targetStart);
    let width = 0, position = targetStart;
    for (let index = lowerBound(draft.boundaries, targetStart); index < draft.items.length; index++) {
      const item = draft.items[index];
      if (item.index >= targetEnd) break;
      const next = itemWidth(item);
      if (width + next > desired) break;
      width += next;
      position += item.segment.length;
    }
    return { text, caret: position, goalColumn: desired };
  }
  return state;
}

export function visibleDraft(text, caret, width, maxRows = 4) {
  const available = Math.max(2, width - 1); // Reserve one cell for an end-of-line caret.
  const draft = getDraft(text);
  let all = draft.layouts.get(available);
  if (!all) {
    all = [{ chunks: [], start: 0, width: 0 }];
    for (const item of draft.items) {
      let row = all.at(-1);
      if (item.segment === '\n') {
        all.push({ chunks: [], start: item.index + 1, width: 0 });
        continue;
      }
      const size = itemWidth(item);
      if (row.width + size > available && row.chunks.length) {
        row = { chunks: [], start: item.index, width: 0 };
        all.push(row);
      }
      row.chunks.push(item);
      row.width += size;
    }
  }
  retain(draft.layouts, available, all, LAYOUT_CACHE_LIMIT);
  const boundary = snapCaret(text, caret);
  const found = lowerBound(all, boundary, row => row.start);
  const focus = found < all.length && all[found].start === boundary ? found : Math.max(0, found - 1);
  const begin = Math.max(0, Math.min(focus - maxRows + 1, all.length - maxRows));
  const end = Math.min(all.length, begin + maxRows);
  return { totalRows: all.length, firstRow: begin + 1, lastRow: end, logicalLines: draft.logicalLines,
    rows: all.slice(begin, end).map((row, index) => {
      if (index + begin !== focus) return { before: row.chunks.map(chunk => chunk.display).join(''), cursor: null, after: '' };
      const split = row.chunks.findIndex(chunk => chunk.index >= boundary);
      if (split < 0) return { before: row.chunks.map(chunk => chunk.display).join(''), cursor: ' ', after: '' };
      return { before: row.chunks.slice(0, split).map(chunk => chunk.display).join(''), cursor: row.chunks[split].display,
        after: row.chunks.slice(split + 1).map(chunk => chunk.display).join('') };
    }) };
}
