import assert from 'node:assert/strict';
import test from 'node:test';
import { createSessionController } from '../src/opentui/session-controller.js';
import { commandsForMode } from '../src/workspace-mode.js';
import { COMMANDS } from '../src/commands.js';
import { checklist, controllerBridge, reviewCommand, REVISION } from './outcome-checks-fixture.js';
const text = result => (result.lines || []).map(line => line.text).join('\n');
const setup = () => { const fixture = controllerBridge(); return { ...fixture,
  controller: createSessionController({ bridge: fixture.bridge }) }; };

test('invalid revision is neither displayed nor cached nor sent with a review', async () => {
  for (const revision of ['token=QA_ONLY_SECRET_MARKER', 'short-revision', 'A'.repeat(64)]) {
    const { state, controller } = setup();
    await controller.submit('/checks task01');
    state.checks = checklist({ revision });
    const output = text(await controller.submit('/checks task01'));
    assert.match(output.split('\n')[0], /读取失败|无法核验/);
    assert.ok(!output.includes('QA_ONLY_SECRET_MARKER'));
    state.calls.length = 0;
    assert.match(text(await controller.submit(reviewCommand())), /先.*查看/);
    assert.equal(state.calls.length, 0);
  }
});

test('a matching review without its recorded time remains unconfirmed', async () => {
  const { state, controller } = setup();
  await controller.submit('/checks task01');
  state.writeReply = { ok: true, task_id: 'task01', review: {
    by: 'Jane Doe', note: '已核对引用与图表', decision: 'approved', revision: state.checks.revision } };
  const output = text(await controller.submit(reviewCommand()));
  assert.ok(!output.includes('已记录'));
  assert.match(output, /未确认/);
});

test('checks routes explicit/current tasks directly, and reports the displayed revision', async () => {
  const { state, controller } = setup();
  assert.match(text(await controller.submit('/checks')), /打开.*任务|指定.*任务/);
  assert.equal(state.calls.length, 0);
  const first = await controller.submit('/checks task01');
  assert.deepEqual(state.calls, [{ cmd: 'outcome_checks', task_id: 'task01' }]);
  assert.ok(text(first).includes(REVISION));
  assert.match(text(first), /task01/);
  await controller.submit('/open task02');
  state.calls.length = 0;
  await controller.submit('/checks');
  assert.deepEqual(state.calls, [{ cmd: 'outcome_checks', task_id: 'task02' }]);
});
test('review consumes one previously displayed revision; never automatically read before write', async () => {
  const { state, controller } = setup();
  assert.match(text(await controller.submit(reviewCommand())), /先.*查看|先.*读取/);
  assert.equal(state.calls.length, 0);
  await controller.submit('/checks task01');
  state.calls.length = 0;
  assert.match(text(await controller.submit(reviewCommand())), /已记录.*通过/);
  assert.deepEqual(state.calls, [{ cmd: 'outcome_review', task_id: 'task01', record: {
    by: 'Jane Doe', note: '已核对引用与图表', decision: 'approved', expected_revision: REVISION } }]);
  await controller.submit(reviewCommand());
  assert.equal(state.calls.length, 1);
});
test('task switching and viewing another checklist invalidate the old review target', async () => {
  const { state, controller } = setup();
  await controller.submit('/checks task01');
  await controller.submit('/open task02');
  state.calls.length = 0;
  assert.match(text(await controller.submit(reviewCommand())), /先.*查看|先.*读取/);
  assert.equal(state.calls.length, 0);
  await controller.submit('/checks task01');
  await controller.submit('/checks task02');
  state.calls.length = 0;
  await controller.submit(reviewCommand());
  assert.equal(state.calls.length, 0);
});
test('failed or malformed read receipts cannot enable a review', async () => {
  for (const readReply of [{ ok: false, error: 'token=private-secret' }, { ok: true },
    { ok: true, checks: checklist({ revision: '' }) }, { ok: true, checks: checklist({ items: [null] }) },
    { ok: true, checks: checklist({ verdict: 'all_satisfied' }) }]) {
    const { state, controller } = setup();
    await controller.submit('/checks task01');
    state.readReply = readReply;
    const result = text(await controller.submit('/checks task01'));
    assert.match(result, /读取失败|无法核验/);
    assert.ok(!result.includes('private-secret'));
    state.calls.length = 0;
    await controller.submit(reviewCommand());
    assert.equal(state.calls.length, 0);
  }
});
test('mismatched write receipts cannot claim saved and never trigger a retry', async () => {
  for (const patch of [{ task_id: 'task02' }, { review: {} }, { review: { by: 'Jane Doe', note: '已核对引用与图表',
    decision: 'approved', revision: 'd'.repeat(64), at: '2026-10-23T12:00:00+08:00' } }]) {
    const { state, controller } = setup();
    await controller.submit('/checks task01');
    state.writeReply = { ok: true, task_id: 'task01', review: { by: 'Jane Doe', note: '已核对引用与图表',
      decision: 'approved', revision: REVISION, at: '2026-10-23T12:00:00+08:00' }, ...patch };
    state.calls.length = 0;
    const result = text(await controller.submit(reviewCommand()));
    assert.ok(!result.includes('已记录'));
    assert.match(result, /重新.*查看|重新.*读取/);
    await controller.submit(reviewCommand());
    assert.equal(state.calls.length, 1);
  }
});
test('a timed out write prompts readback without claiming nothing was saved', async () => {
  const { state, controller } = setup();
  await controller.submit('/checks task01');
  state.writeError = '响应超时 token=private-secret';
  const result = text(await controller.submit(reviewCommand()));
  assert.ok(!result.includes('private-secret') && !result.includes('复核未保存'));
  assert.match(result, /重新.*查看|重新.*读取/);
});
test('late reads after task switching and bridge restarts cannot enable review', async () => {
  const { state, controller } = setup();
  let release;
  state.holdRead = () => new Promise(resolve => { release = resolve; });
  const reading = controller.submit('/checks task01');
  await controller.submit('/open task02');
  release({ ok: true, checks: checklist() });
  assert.match(text(await reading), /已变化|已切换/);
  state.calls.length = 0;
  await controller.submit(reviewCommand());
  assert.equal(state.calls.length, 0);
  state.holdRead = null;
  await controller.submit('/checks task01');
  state.generation++;
  state.calls.length = 0;
  await controller.submit(reviewCommand());
  assert.equal(state.calls.length, 0);
});
test('remote reads/writes are blocked in both catalog and controller', async () => {
  const { state, controller } = setup();
  state.mode = 'remote';
  assert.equal(commandsForMode(COMMANDS, 'research', { remote: true }).find(row => row.name === 'checks').available, false);
  for (const input of ['/checks task01', reviewCommand()]) assert.match(text(await controller.submit(input)), /本机|本地/);
  assert.equal(state.calls.length, 0);
});
test('details preserve hashes, and hidden controls cannot become a review command', async () => {
  const { state, controller } = setup();
  const details = text(await controller.submit('/checks task01 --details'));
  assert.ok(details.includes('a'.repeat(64)) && details.includes('b'.repeat(64)));
  state.calls.length = 0;
  await controller.submit(`${reviewCommand()}\u001b`);
  assert.equal(state.calls.length, 0);
});
