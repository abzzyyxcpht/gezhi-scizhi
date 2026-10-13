import assert from 'node:assert/strict';
import { insertText, editText, visibleDraft, previousBoundary, nextBoundary, normalizePaste } from '../src/composer.js';

assert.equal(normalizePaste('行一\r\n\t行二\r行三'), '行一\n\t行二\n行三');
let draft = insertText({ text: '', caret: 0 }, '🧪abc');
assert.equal(draft.text, '🧪abc'); assert.equal(draft.caret, 5);
draft = editText(draft, 'home');
draft = editText(draft, 'right');
assert.equal(draft.caret, 2);
draft = editText(draft, 'backspace');
assert.equal(draft.text, 'abc');
for (const glyph of ['👨‍👩‍👧‍👦', 'e\u0301', '🇨🇳']) {
  assert.equal(previousBoundary(glyph, glyph.length), 0);
  assert.equal(nextBoundary(glyph, 0), glyph.length);
  assert.equal(editText({ text: glyph + 'a', caret: glyph.length }, 'backspace').text, 'a');
}
draft = { text: '第一行\n第二行\nthird', caret: 6 };
assert.equal(editText(draft, 'home').caret, 4);
assert.equal(editText(draft, 'end').caret, 7);
assert.equal(editText(draft, 'up').caret, 2);
assert.equal(editText(draft, 'down').caret, 12);
const short = visibleDraft('abc', 1, 8, 4);
assert.equal(short.rows[0].before, 'a'); assert.equal(short.rows[0].cursor, 'b');
const long = visibleDraft('测'.repeat(300), 300, 18, 4);
assert.ok(long.rows.length <= 4 && long.rows.some(row => row.cursor !== null));
assert.equal(insertText({ text: 'keep', caret: 4 }, 'x'.repeat(32000)).text, 'keep');
assert.equal(insertText({ text: 'a', caret: 1 }, '\u001b[31mred\u001b[0m\u0007').text, 'ared');

// An edit can merge the glyphs on either side; its new caret must follow the
// resulting complete cluster, rather than point into a ZWJ/combining sequence.
assert.deepEqual(insertText({ text: '👩👧', caret: 2 }, '\u200d'), { text: '👩‍👧', caret: 5 });
assert.deepEqual(insertText({ text: '🇦🇧', caret: 0 }, '🇨'), { text: '🇨🇦🇧', caret: 4 });
assert.deepEqual(editText({ text: 'a\n\u0301', caret: 2 }, 'backspace'), { text: 'a\u0301', caret: 2 });
assert.deepEqual(editText({ text: 'a\n\u0301', caret: 1 }, 'delete'), { text: 'a\u0301', caret: 2 });
assert.deepEqual(editText({ text: '🇦 🇧', caret: 3 }, 'kill-word'), { text: '🇦🇧', caret: 4 });
const glyphBoundaries = value => new Set([0, ...[...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(value)].map(item => item.index + item.segment.length)]);
for (const text of ['a\n\u0301', '👩\n👧', '🇦 x🇧', '\u0301 abc\n👩‍👧', 'e\u0301\tword 🇨🇳']) {
  for (const caret of glyphBoundaries(text)) {
    for (const action of ['backspace', 'delete', 'kill-start', 'kill-end', 'kill-word']) {
      const changed = editText({ text, caret }, action);
      assert.ok(glyphBoundaries(changed.text).has(changed.caret), `${action} keeps a valid resulting caret: ${JSON.stringify({ text, caret, changed })}`);
    }
  }
}
// OSC hyperlink/color sequences must stop at their FIRST terminator, preserving
// ordinary text between two independently terminated controls.
assert.equal(normalizePaste('pre\x1b]8;;https://example.test\x1b\\label\x1b]8;;\x1b\\post'), 'prelabelpost');
assert.equal(normalizePaste('pre\x1b]0;title\x07middle\x1b]0;other\x07post'), 'premiddlepost');
assert.equal(normalizePaste('pre\x9d8;;https://example.test\x9clabel\x9d8;;\x9cpost'), 'prelabelpost');
assert.equal(normalizePaste('\x9b31mred\x9b0m [31m'), 'red [31m');
assert.equal(normalizePaste('a\x1b]unfinished\nbody'), 'a]unfinished\nbody');
assert.equal(normalizePaste('\x1b]'.repeat(16000)), ']'.repeat(16000));
assert.equal(normalizePaste('text [200~ literal\n\tline'), 'text [200~ literal\n\tline');

// Cached layouts must depend on BOTH text and width and return caller-owned
// rows: resizing, changing drafts, or editing a returned row cannot corrupt it.
const resizeText = '测'.repeat(60) + '\n\tend👩‍👧';
const wide = visibleDraft(resizeText, resizeText.length, 74, 3);
const narrow = visibleDraft(resizeText, resizeText.length, 16, 3);
assert.ok(narrow.totalRows > wide.totalRows);
visibleDraft('replacement', 4, 16, 3);
assert.deepEqual(visibleDraft(resizeText, resizeText.length, 74, 3), wide);
wide.rows[0].before = 'caller changed this';
assert.notEqual(visibleDraft(resizeText, resizeText.length, 74, 3).rows[0].before, wide.rows[0].before);
for (let i = 0; i < 8; i++) visibleDraft(`${i} ${resizeText}`, 2, 16 + i, 3);
assert.deepEqual(visibleDraft(resizeText, resizeText.length, 16, 3), narrow);
console.log('Grapheme editing, multiline boundaries, paste normalization, bounded viewport and non-destructive limits passed');
