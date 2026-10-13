import assert from 'node:assert/strict';
import { runWorkbenchCommand } from '../src/workbench-commands.js';
import { COMMANDS } from '../src/commands.js';
const calls = [], output = [], reports = [];
let remote = false;
const brief = { scope: { task_id: 'task-current' }, boundary: '材料关系尚未进行科学验证。', records_truncated: false,
  records: [{ title: '范围核验', id: 'record-1', version: 2, question: '是否成立？',
    counts: { supports: 1, challenges: 0, context: 0 },
    groups: { supports: [{ source: { id: 'paper-1' }, scope: '仅摘要', quote: 'fixture evidence', synthetic: true, quote_modified: true }], challenges: [], context: [] },
    gaps: ['缺少反驳材料'], followup_draft: '请继续核验', evidence_truncated: false }] };
const context = { taskId: 'task-current', bridge: { connection: () => ({ mode: remote ? 'remote' : 'local' }),
  call: async request => { calls.push(request); return { ok: true, text: '本机证据对照 · 只读', brief }; } },
  out: text => output.push(text), openReport: (title, text) => reports.push({ title, text }) };
assert.equal(await runWorkbenchCommand('evidence', '', context), true, 'evidence command must be registered and dispatched');
assert.deepEqual(calls.at(-1), { cmd: 'command', text: '/evidence ', task_id: 'task-current' });
remote = true;
await runWorkbenchCommand('evidence', '', context);
assert.deepEqual(calls.at(-1), { cmd: 'evidence_brief', task_id: 'task-current' });
const text = reports.at(-1)?.text || output.join('\n');
for (const phrase of ['支持', '反驳', '背景', '合成', '仅摘要', '未发送', '请继续核验', '尚未进行科学验证']) assert.ok(text.includes(phrase), phrase);
assert.ok(!calls.some(call => ['queue_add', 'chat_turn', 'agent_run'].includes(call.cmd)));
await runWorkbenchCommand('evidence', 'historic-task', context);
assert.equal(calls.at(-1).task_id, 'historic-task');
const count = calls.length;
await runWorkbenchCommand('evidence', 'one two', context);
assert.equal(calls.length, count);
assert.ok(COMMANDS.some(row => row.name === 'evidence'));
console.log('Local/remote read-only evidence summary preserves scope, synthetic markers and unsent follow-up draft');
