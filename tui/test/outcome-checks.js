// 五档状态、诚实措辞、完整依据和显式人工复核合同。
import assert from 'node:assert/strict';
import { outcomeCheckLines, parseCheckArgs, reviewResultLines, checkUsageLines,
  OUTCOME_STATE_LABELS, OUTCOME_VERDICT_LABELS, OUTCOME_BOUNDARY, OUTCOME_STALE_HINT } from '../src/outcome-checks.js';
import { checklist, REVISION } from './outcome-checks-fixture.js';

assert.deepEqual(Object.keys(OUTCOME_STATE_LABELS), ['satisfied', 'failed', 'stale', 'pending', 'unknown']);
assert.deepEqual(Object.values(OUTCOME_STATE_LABELS).map(row => row.symbol), ['✓', '✗', '⚠', '○', '?']);
assert.equal(OUTCOME_VERDICT_LABELS.all_satisfied, '全部检查项已有记录支持');
assert.equal(OUTCOME_VERDICT_LABELS.needs_review, '需要复核');
assert.equal(OUTCOME_VERDICT_LABELS.incomplete, '尚未完成');
assert.equal(OUTCOME_VERDICT_LABELS.unknown, '无法给出结论');
for (const state of Object.keys(OUTCOME_STATE_LABELS)) {
  const verdict = state === 'satisfied' ? 'all_satisfied' : ['failed', 'stale'].includes(state) ? 'needs_review' : 'incomplete';
  const report = checklist({ verdict, total: 1, satisfied: state === 'satisfied' ? 1 : 0,
    counts: Object.fromEntries(Object.keys(OUTCOME_STATE_LABELS).map(key => [key, key === state ? 1 : 0])),
    items: [{ id: 'item01', title: '检查项', state, detail: '当前记录', evidence: [] }] });
  const lines = outcomeCheckLines(report);
  assert.ok(lines[0].includes(OUTCOME_VERDICT_LABELS[verdict]));
  assert.ok(lines.some(line => line.includes(OUTCOME_STATE_LABELS[state].label)));
  assert.equal(lines.at(-1), OUTCOME_BOUNDARY);
  if (state === 'stale') assert.ok(lines.join('\n').includes(OUTCOME_STALE_HINT));
  if (['pending', 'unknown'].includes(state)) assert.ok(!lines.slice(0, -1).join('\n').includes('已完成'));
}
assert.ok(outcomeCheckLines(checklist()).join('\n').includes('统计：已核实 1'));
assert.ok(outcomeCheckLines(null)[0].includes('读取失败'));
assert.ok(outcomeCheckLines(checklist({ total: 0, items: [], counts: null }))[0].includes('读取失败'));
const report = checklist();
report.items[0].detail = 'token=sk-abcdef0123456789\u0007';
const secret = outcomeCheckLines(report, { redact: value => String(value).replace(/sk-[A-Za-z0-9]+/g, '[REDACTED]') });
assert.ok(!secret.join('\n').includes('sk-abcdef0123456789'));
assert.ok(!secret.join('\n').includes('\u0007'));
assert.deepEqual(parseCheckArgs(''), { mode: 'read', taskId: '' });
assert.deepEqual(parseCheckArgs('  task01 '), { mode: 'read', taskId: 'task01' });
assert.equal(parseCheckArgs('a b').mode, 'usage');
assert.deepEqual(parseCheckArgs('review task01 --decision approved --by 张三 --note 已核对引用与图表'),
  { mode: 'review', taskId: 'task01', decision: 'approved', by: '张三', note: '已核对引用与图表' });
assert.equal(parseCheckArgs('review task01 张三 已核对').mode, 'usage');
assert.ok(reviewResultLines({ ok: true, task_id: 'task01', review: {
  by: '张三', note: '已核对', decision: 'approved', revision: REVISION, at: '2026-10-23T12:00:00+08:00' } })[0].includes('已记录：通过复核'));
assert.ok(reviewResultLines({ ok: false, error: '缺少任务 ID' })[0].includes('未确认复核结果'));
assert.ok(reviewResultLines(null)[0].includes('后端未返回有效回执'));
assert.equal(checkUsageLines().length, 3);
console.log('outcome-checks: five states, honest wording, explicit review and redaction passed');
