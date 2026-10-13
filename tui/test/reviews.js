import assert from 'node:assert/strict';
import { runWorkbenchCommand } from '../src/workbench-commands.js';
const calls = [], output = [];
let lost = true, remote = false;
const job = { id: 'job-one', record_id: 'record-one', record_version: 3, mode: 'local', status: 'ready', title: '待核查假设', stale: true };
const context = { out: text => output.push(text), bridge: { connection: () => ({ mode: remote ? 'remote' : 'local' }),
  call: async req => {
    calls.push(req);
    if (req.cmd === 'review_start') {
      if (lost) { lost = false; throw new Error('lost receipt'); }
      return { ok: true, job: { ...job, request_id: req.review_request_id } };
    }
    if (req.cmd === 'review_accept') return { ok: false, error: '原研究记录已变更' };
    if (req.cmd === 'review_list') return { ok: true, enabled: true, jobs: [job], total: 1 };
    return { ok: true, job };
  } } };
const run = args => runWorkbenchCommand('reviews', args, context);
await run('list');
assert.ok(output.some(line => line.includes('原记录已变化')));
await assert.rejects(run('start record-one 3 local'), /lost receipt/);
const key = calls.at(-1).review_request_id;
await run('start record-one 3 local');
assert.equal(calls.at(-1).review_request_id, key);
await run('start record-one 3 local');
assert.notEqual(calls.at(-1).review_request_id, key, 'explicit new review after acknowledged start uses a new identity');
await assert.rejects(run('accept job-one'), /已变更/);
remote = true;
const count = calls.length;
await run('enable');
assert.equal(calls.length, count);
await run('start record-one 0 local');
assert.equal(calls.length, count, 'version is required and positive');
console.log('Review inbox scope, explicit local/model mode, receipt retry and stale acceptance contracts passed');
