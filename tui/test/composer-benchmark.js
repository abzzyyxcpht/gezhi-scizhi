// Opt-in microbenchmark: reports timings, never gates correctness on wall time.
// Run from tui/: node test/composer-benchmark.js
import { performance } from 'node:perf_hooks';
import { editText, insertText, visibleDraft } from '../src/composer.js';

const cases = {
  ASCII: 'a'.repeat(32000),
  repeated_CJK: '测'.repeat(32000),
  diverse_CJK: Array.from({ length: 32000 }, (_, i) => String.fromCharCode(0x4e00 + i % 20992)).join(''),
  emoji: '👨‍👩‍👧‍👦'.repeat(2909),
  research_multiline: '实验组 A：Δ=0.15，p<0.05；效应量与置信区间需要复核。\n\tcontrol e\u0301 🧪\n'.repeat(560).slice(0, 31000),
};
const measure = operation => {
  const start = performance.now();
  operation();
  return performance.now() - start;
};
const summarize = samples => {
  const sorted = [...samples].sort((a, b) => a - b);
  return Object.fromEntries(Object.entries({ mean: samples.reduce((sum, x) => sum + x, 0) / samples.length,
    p50: sorted[Math.floor(sorted.length * .5)], p95: sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * .95))] })
    .map(([key, value]) => [`${key}_ms`, Number(value.toFixed(3))]));
};
const records = [];
for (const [name, text] of Object.entries(cases)) {
  let state = { text, caret: text.length };
  const cold_layout_ms = measure(() => visibleDraft(text, state.caret, 74, 3));
  const moves = [];
  for (let i = 0; i < 200; i++) moves.push(measure(() => {
    state = editText(state, 'left');
    visibleDraft(state.text, state.caret, 74, 3);
  }));
  const resize_layout_ms = measure(() => visibleDraft(text, state.caret, 40, 3));
  // Alternate cached widths as a busy indicator or terminal resize would do.
  const widthChanges = [];
  for (let i = 0; i < 50; i++) widthChanges.push(measure(() => visibleDraft(text, state.caret, i % 2 ? 40 : 74, 3)));
  // Genuine text mutations also rebuild segmentation/layout. Report them
  // separately so cached navigation cannot imply constant-time typing.
  const mutations = [];
  state = { text, caret: text.length };
  for (let i = 0; i < 20; i++) mutations.push(measure(() => {
    state = editText(state, 'backspace');
    state = insertText(state, String.fromCharCode(0x61 + i));
    visibleDraft(state.text, state.caret, 74, 3);
  }));
  records.push({ name, utf16_units: text.length, cold_layout_ms: Number(cold_layout_ms.toFixed(3)),
    left_and_view: summarize(moves), resize_layout_ms: Number(resize_layout_ms.toFixed(3)),
    cached_width_switch: summarize(widthChanges), delete_insert_and_view: summarize(mutations) });
}
console.log(JSON.stringify({ node: process.version, platform: process.platform,
  method: 'single process; cold layout, 200 caret moves, two widths, 20 actual text mutations; synthetic drafts; no rendering/network', records }, null, 2));
