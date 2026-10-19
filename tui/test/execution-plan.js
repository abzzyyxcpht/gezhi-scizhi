import assert from 'node:assert/strict';
import { executionPlanLines } from '../src/execution-plan.js';
import { createSessionController } from '../src/opentui/session-controller.js';
import { commandsForMode } from '../src/workspace-mode.js';
import { COMMANDS } from '../src/commands.js';

const plan = { session_id: 'one', has_plan: true, completed: 1, total: 2, revision: 3,
  steps: [{ id: 'a', title: '读取材料', criterion: '记录实际文件', kind: 'execution', status: 'completed',
    evidence_kind: 'tool_receipt', evidence_details: [{ call_id: 'call-one', tool: 'file.read', summary: 'api_key=fixture-secret' }] },
  { id: 'b', title: '核对结果', criterion: '检查输出', status: 'blocked', reason: '等待补充材料' }] };
const display = executionPlanLines(plan).join('\n');
for (const value of ['1/2', '版本 3', 'file.read', 'call-one', '受阻', '等待补充材料', '不等同']) assert.ok(display.includes(value), value);
assert.ok(!display.includes('fixture-secret'));
assert.ok(executionPlanLines(null).join('').includes('尚未'));
assert.ok(executionPlanLines({ error: '暂不可读' }).join('').includes('暂不可读'));
assert.ok(executionPlanLines(plan, { compact: true }).join('').includes('核对结果'));
assert.ok(!executionPlanLines(plan, { compact: true }).join('').includes('call-one'));

const calls = [];
const controller = createSessionController({ bridge: { call: async request => {
  calls.push(request); return { ok: true, plan };
} } });
const result = await controller.submit('/progress one');
assert.deepEqual(calls, [{ cmd: 'session_plan', session_id: 'one' }]);
assert.ok(JSON.stringify(result).includes('读取材料'));
await controller.submit('/progress ../escape');
assert.equal(calls.length, 1);
assert.equal(commandsForMode(COMMANDS, 'general', { busy: true, remote: true })
  .find(row => row.name === 'progress').available, true);
let complete;
const late = createSessionController({ bridge: { call: () => new Promise(resolve => { complete = resolve; }) } });
late.setSession('one');
const pending = late.submit('/progress');
late.setSession('two');
complete({ ok: true, plan });
assert.deepEqual((await pending).lines, []);
console.log('Execution plan progress shows bounded receipts and uses read-only session requests');
