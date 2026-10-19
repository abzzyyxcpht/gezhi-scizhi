import assert from 'node:assert/strict';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

await upgradeFixture(async t => {
  const original = Bridge.prototype.call;
  const requests = [];
  Bridge.prototype.call = async function (request) {
    if (request.cmd !== 'session_plan') return original.call(this, request);
    requests.push(request);
    return { ok: true, plan: { session_id: request.session_id, has_plan: true, completed: 1, total: 2, revision: 4,
      steps: [{ id: 'read', title: '核对研究材料', criterion: '读取来源', status: 'completed',
        evidence_kind: 'tool_receipt', evidence_details: [{ tool: 'file.read', call_id: 'receipt-42' }] },
      { id: 'review', title: '复核限制条件', status: 'blocked', reason: '还需实验参数' }] } };
  };
  try {
    t.mode = 'remote';
    await t.command('/progress existing');
    await t.wait(() => t.frame().includes('receipt-42') && t.frame().includes('还需实验参数'), 'persistent plan receipts in terminal');
    const frame = t.frame();
    assert.ok(frame.includes('1/2'));
    assert.ok(frame.includes('不等同'));
    assert.deepEqual(requests, [{ cmd: 'session_plan', session_id: 'existing' }]);
    assert.equal(t.calls.filter(row => ['agent_run', 'agent_launch', 'agent_continue'].includes(row.cmd)).length, 0);
    t.capture('execution-plan', 100, 32);
  } finally { Bridge.prototype.call = original; }
}, { width: 100, height: 32 });
console.log('Native OpenTUI progress view shows actual receipt IDs without launching or resuming a task');
process.exit(0);
