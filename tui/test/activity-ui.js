import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { activityLines, activityReaderPayload } from '../src/activity.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureActivityFrames = () => upgradeFixture(async t => {
  t.activity = { session_id: 'session-root', status: 'running', truncated: true,
    pressure: { pressure_tokens: 7200, limit_tokens: 12000 },
    items: [{ id: 'tool-1', type: 'tool', label: '读取 paper.md', summary: '读取摘要', status: 'completed' },
      { id: 'agent-1', type: 'agent', label: '文献猎手', status: 'running', steps: 3, tokens: 900 }],
    children: [{ session_id: 'child-real', parent_id: 'session-root', agent: '检索子任务', status: 'waiting', steps: 2, tokens: 200 }] };
  const lines = activityLines(t.activity, { sessionId: 'session-root' }).join('\n');
  assert.ok(lines.includes('读取 paper.md') && lines.includes('读取摘要') && lines.includes('child-real'));
  assert.ok(!lines.includes('child-1'), 'missing child IDs are never invented');
  assert.ok(activityReaderPayload({ ok: true, activity: t.activity }, 'session-root').lines.length > 3);
  await t.command('/activity session-root');
  await t.wait(() => t.frame().includes('执行活动') && t.frame().includes('读取 paper.md') && t.frame().includes('上下文压力'), 'activity opens read-only reader');
  t.capture('activity-reader', 80, 24, false);
  assert.equal(t.calls.filter(call => call.cmd === 'session_activity').length, 1);
  await t.press('\x1b'); await t.press('保留活动草稿');
  assert.ok(t.frame().includes('❯ 保留活动草稿'));
  assert.ok(!t.calls.some(call => ['agent_run', 'agent_approve', 'agent_deny', 'workflow_run'].includes(call.cmd)));
  t.activity = { session_id: 'session-root', status: 'done', items: [], children: [] };
  await t.command('/activity session-root');
  await t.wait(() => t.frame().includes('暂无可读活动'), 'empty activity is explicit');
  await t.press('\x1b');
  t.hold = 'session_activity';
  const pendingCommand = t.command('/activity session-root');
  await t.wait(() => t.held.length === 1, 'activity request is held');
  await t.press('\u0010');
  await t.wait(() => t.frame().includes('命令面板'), 'palette takes ownership while activity is pending');
  const pending = t.held.shift(); pending.resolve(pending.answer()); t.hold = '';
  await pendingCommand;
  assert.ok(t.frame().includes('命令面板') && !t.frame().includes('执行活动'), 'late activity response cannot replace palette');
  await t.press('\x1b');
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureActivityFrames(); console.log(`${result.frames.length} activity frames passed`);
  process.exit(0);
}
