import assert from 'node:assert/strict';
import stringWidth from 'string-width';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';
import { prepareTranscriptRows } from '../src/opentui/components/markup-lines.js';
import { outcomeCheckLines } from '../src/outcome-checks.js';
import { checklist, reviewCommand, REVISION } from './outcome-checks-fixture.js';

for (const width of [28, 42, 80]) {
  const report = checklist();
  report.items[1].detail = `材料变化：${'需核对原始记录'.repeat(8)}；原结论需要复核`;
  const lines = outcomeCheckLines(report, { details: true }).map(text => ({ text, kind: 'outcome' }));
  const wrapped = prepareTranscriptRows(lines, width - 4);
  assert.equal(wrapped.map(row => row.text).join(''), lines.map(row => row.text).join(''), `${width}: every evidence character survives wrapping`);
  assert.ok(wrapped.every(row => stringWidth(row.text) <= width - 4));

  await upgradeFixture(async t => {
    const original = Bridge.prototype.call;
    Bridge.prototype.call = async function (request) {
      if (request.cmd === 'outcome_checks') {
        t.calls.push(request);
        return { ok: true, checks: report };
      }
      if (request.cmd === 'outcome_review') {
        t.calls.push(request);
        return { ok: true, task_id: request.task_id, review: {
          ...request.record, revision: request.record.expected_revision, at: '2026-10-23T12:00:00+08:00' } };
      }
      return original.call(this, request);
    };
    try {
      t.resize(width, 24);
      await t.command('/checks task01 --details');
      await t.wait(() => t.frame().replace(/\s/g, '').includes('复核需明确'), `${width}: checks rendered`);
      const frames = [];
      for (let step = 0; step < Math.ceil(wrapped.length / 3) + 10; step++) {
        const frame = t.frame();
        assert.ok(frame.split('\n').every(line => stringWidth(line) <= width),
          JSON.stringify(frame.split('\n').filter(line => stringWidth(line) > width).map(line => ({ width: stringWidth(line), line }))));
        frames.push(frame.replace(/\s/g, ''));
        t.capture(`checks-${width}-${step}`, width, 24, false);
        await t.scrollWheel('up');
      }
      const visible = frames.join('\n');
      for (const expected of ['成果验收', REVISION, '原结论需要复核', '不判断科学结论是否正确',
        'a'.repeat(64), 'b'.repeat(64)]) assert.ok(visible.includes(expected), `${width}: can scroll to ${expected}`);
      await t.command(reviewCommand('task01', 'changes_requested'));
      await t.wait(() => t.frame().replace(/\s/g, '').includes('已记录：要求修改'), `${width}: explicit decision receipt`);
      assert.equal(t.calls.filter(call => call.cmd === 'outcome_review').length, 1);
      assert.equal(t.calls.find(call => call.cmd === 'outcome_review').record.by, 'Jane Doe');
      t.capture(`review-${width}`, width, 24, false);
    } finally { Bridge.prototype.call = original; }
  });
}

await upgradeFixture(async t => {
  t.mode = 'remote';
  await t.command('/checks task01');
  await t.wait(() => t.frame().includes('主机') && t.frame().includes('/checks task01'), 'remote gate preserves the command draft');
  assert.equal(t.calls.filter(call => ['outcome_checks', 'outcome_review'].includes(call.cmd)).length, 0);
});

console.log('outcome-checks-ui: 28/42/80-column evidence, scrolling, explicit review and remote draft preservation passed');
await new Promise(resolve => setTimeout(resolve, 150));
process.exit(0);
