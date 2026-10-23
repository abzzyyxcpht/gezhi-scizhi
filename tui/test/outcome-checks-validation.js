import assert from 'node:assert/strict';
import test from 'node:test';
import { outcomeCheckLines, parseCheckArgs, reviewResultLines, validOutcomeChecks } from '../src/outcome-checks.js';
import { checklist, REVISION } from './outcome-checks-fixture.js';

test('checklist revision must be a lowercase SHA-256 digest', () => {
  for (const revision of ['token=QA_ONLY_SECRET_MARKER', 'a'.repeat(63), 'a'.repeat(65), 'A'.repeat(64), 'z'.repeat(64), `${'a'.repeat(64)}\n`]) {
    assert.equal(validOutcomeChecks(checklist({ revision })), false);
    const rendered = outcomeCheckLines(checklist({ revision })).join('\n');
    assert.match(rendered.split('\n')[0], /读取失败|无法核验/);
    assert.ok(!rendered.includes('QA_ONLY_SECRET_MARKER'));
  }
});

test('review receipts without a valid timestamp cannot be called recorded', () => {
  for (const at of [undefined, '', '   ', 'not-a-timestamp', 42]) {
    const rendered = reviewResultLines({ ok: true, task_id: 'task01', review: {
      by: 'Jane Doe', note: '核对完成', decision: 'approved', revision: 'c'.repeat(64), at } }).join('\n');
    assert.ok(!rendered.includes('已记录'));
    assert.match(rendered, /未确认/);
  }
});

test('malformed or contradictory checklists never claim success or throw', () => {
  for (const report of [null, false, [], {}, checklist({ items: [null] }),
    checklist({ verdict: 'all_satisfied' }), checklist({ total: -1 }),
    checklist({ items: [], total: 0, satisfied: 0, verdict: 'all_satisfied' }),
    checklist({ items: [{}], total: 1, satisfied: 1, verdict: 'all_satisfied' }),
    checklist({ revision: '' }), checklist({ counts: { satisfied: 3 } })]) {
    const rendered = outcomeCheckLines(report).join('\n');
    assert.match(rendered, /读取失败|无法核验/);
    assert.ok(!rendered.includes('全部检查项已有记录支持') && !rendered.includes('✓'));
  }
});
test('detail view preserves long evidence and every item', () => {
  const note = `起点${'材料细节'.repeat(100)}终点`;
  const items = Array.from({ length: 20 }, (_, i) => ({ id: `x${i}`, title: `检查项 ${i}`, state: 'unknown',
    detail: note, criterion: '标准', evidence: [{ note, by: 'Jane Doe' }] }));
  const text = outcomeCheckLines(checklist({ items, total: 20, satisfied: 0, verdict: 'incomplete',
    counts: { satisfied: 0, failed: 0, stale: 0, pending: 0, unknown: 20 } }), { details: true }).join('\n');
  assert.ok(text.includes('检查项 19') && text.includes(note) && text.includes('Jane Doe'));
});
test('external evidence and failed review receipts are redacted by default', () => {
  const report = checklist();
  report.items[0].detail = 'token=sk-secret123456';
  report.items[0].evidence = [{ authorization: 'secret-token', note: 'password=secretpass' }];
  const text = outcomeCheckLines(report, { details: true }).join('\n');
  for (const secret of ['sk-secret123456', 'secret-token', 'secretpass']) assert.ok(!text.includes(secret));
  assert.ok(!reviewResultLines({ ok: false, error: 'token=sk-secret123456' }).join('\n').includes('sk-secret123456'));
});
test('review requires an explicit decision and preserves quoted fields', () => {
  assert.deepEqual(parseCheckArgs('review task01 --decision changes_requested --by "Jane Doe" --note "核对 A 与 B"'),
    { mode: 'review', taskId: 'task01', decision: 'changes_requested', by: 'Jane Doe', note: '核对 A 与 B' });
  assert.deepEqual(parseCheckArgs('review task01 --note \'引文 "A" 需补全\' --by 王明 --decision approved'),
    { mode: 'review', taskId: 'task01', decision: 'approved', by: '王明', note: '引文 "A" 需补全' });
  assert.deepEqual(parseCheckArgs('task01 --details'), { mode: 'read', taskId: 'task01', details: true });
  assert.deepEqual(parseCheckArgs('--details'), { mode: 'read', taskId: '', details: true });
});
test('unknown and duplicate flags, invalid ids and control characters are rejected', () => {
  for (const input of ['bad/id', 'task01 --unknown', 'task01 --details --details',
    'review task01 张三 已核对', 'review task01 --by 张三 --note 说明',
    'review task01 --decision yes --by 张三 --note 说明',
    'review task01 --decision approved --by 张三 --note 说明 --force',
    'review task01 --decision approved --by 张三 --by 李四 --note 说明',
    'review task01 --decision approved --by "Jane Doe --note 说明',
    'review task01 --decision approved --by 张三 --note ""',
    `review task01 --decision approved --by ${'名'.repeat(81)} --note 说明`,
    `review task01 --decision approved --by 张三 --note ${'长'.repeat(2001)}`,
    'review task01 --decision approved --by 张三 --note "\u001b[31m"',
    'review task01 --decision approved --by 张三 --note "\u202e反向"', '\ttask01']) {
    assert.equal(parseCheckArgs(input).mode, 'usage', input);
  }
});
test('review success requires an actual record and distinguishes changes requested', () => {
  for (const receipt of [null, { ok: true }, { ok: true, review: {} }, { ok: true, review: { by: '张三' } }]) {
    assert.ok(!reviewResultLines(receipt).join('\n').includes('已记录'));
  }
  const text = reviewResultLines({ ok: true, task_id: 'task01', review: { by: 'Jane Doe', note: '需补引用',
    decision: 'changes_requested', revision: REVISION, at: '2026-10-23T12:00:00+08:00' } }).join('\n');
  assert.ok(text.includes('要求修改') && !text.includes('通过复核'));
});
