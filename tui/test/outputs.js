import assert from 'node:assert/strict';
import { runOutputCommand } from '../src/output-commands.js';

const id = 'out_' + 'a'.repeat(32);
let requests = [], pages = [], notices = [];
let response = { ok: true, output: { session_id: 's', items: [{ output_id: id, tool: 'file.read', total_bytes: 30000 }], total: 1, next_offset: null } };
const context = { currentAgentSession: 's', bridge: { call: async r => { requests.push(r); return response; }, connection: () => ({ mode: 'local' }) },
  out: value => notices.push(value), openReport: (title, body) => pages.push({ title, body }) };
assert.equal(await runOutputCommand('else', '', context), false);
await runOutputCommand('outputs', '', { ...context, currentAgentSession: null });
assert.equal(requests.length, 0);
await runOutputCommand('outputs', '', context);
assert.equal(requests[0].cmd, 'session_tool_outputs');
assert.match(pages[0].body, new RegExp(id));
response = { ok: true, output: { session_id: 's', output_id: id, text: '\x1b[31m正文\x1b[0m', offset: 0, next_offset: 6, total_bytes: 12, eof: false } };
await runOutputCommand('outputs', `read ${id}`, context);
assert.equal(requests.at(-1).limit, 4000);
assert.match(pages.at(-1).body, /正文/);
assert.ok(!pages.at(-1).body.includes('\x1b'));
assert.match(pages.at(-1).body, /下一段/);
response = { ok: true, output: { session_id: 's', output_id: id, matches: [{ offset: 24, preview: '尾部证据' }], next_offset: null, eof: true } };
await runOutputCommand('outputs', `search ${id} 0 尾部证据`, context);
assert.equal(requests.at(-1).query, '尾部证据');
assert.match(pages.at(-1).body, /24/);
const count = requests.length;
for (const bad of ['read ../keys', `read ${id} -1`, `read ${id} 99999999999999999999`, `search ${id} 0 ${'x'.repeat(201)}`])
  await runOutputCommand('outputs', bad, context);
assert.equal(requests.length, count);
response = { ok: true, output: { session_id: 'other', items: [] } };
await assert.rejects(runOutputCommand('outputs', '', context), /身份不匹配/);
response = { ok: false, error: 'missing output' };
await assert.rejects(runOutputCommand('outputs', `read ${id}`, context), /missing output/);
response = { ok: true, output: { session_id: 's', items: [] } };
const pageCount = pages.length;
await runOutputCommand('outputs', '', { ...context, isCurrentSession: () => false });
assert.equal(pages.length, pageCount);
console.log('Output list/read/search, opaque IDs, byte cursors, protocol identities, stale views and safe terminal text passed');
