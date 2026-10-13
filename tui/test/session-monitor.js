import assert from 'node:assert/strict';
import { monitorSession, agentCanCancel } from '../src/session-monitor.js';

const jobs = new Map();
let sequence = 0;
const schedule = (run, delay) => { const id = ++sequence; jobs.set(id, { run, delay }); return id; };
const unschedule = id => jobs.delete(id);
const next = async delay => {
  assert.equal(jobs.size, 1, 'only one scheduled read at a time');
  const [id, job] = jobs.entries().next().value;
  assert.equal(job.delay, delay);
  jobs.delete(id);
  await job.run();
};

let reads = 0, failures = 0;
const observations = [];
const stop = monitorSession({ schedule, unschedule,
  read: async () => { reads++; if (reads === 1) throw new Error('temporary'); return { snapshot: { status: reads === 2 ? 'done' : 'running' } }; },
  onSnapshot: value => observations.push(value.snapshot.status), onError: () => failures++,
});
await next(400);
assert.equal(failures, 1);
await next(3000);
assert.deepEqual(observations, ['done']);
await next(5000);
assert.deepEqual(observations, ['done', 'running'], 'terminal heartbeat observes external continuation');
assert.equal(jobs.values().next().value.delay, 1500, 'active cadence resumes');
stop();
assert.equal(jobs.size, 0);

let resolveRead, currentGeneration = 1;
const stale = [];
const stopStale = monitorSession({ schedule, unschedule, isCurrent: () => currentGeneration === 1,
  read: () => new Promise(resolve => { resolveRead = resolve; }), onSnapshot: value => stale.push(value),
});
const pending = next(400);
assert.equal(jobs.size, 0, 'no overlapping timer while snapshot is in flight');
currentGeneration = 3; // A → B → A must still invalidate A's first request.
resolveRead({ snapshot: { status: 'waiting_approval' } });
await pending;
assert.deepEqual(stale, []);
assert.equal(jobs.size, 0, 'obsolete selection cannot reschedule itself');
stopStale();

for (const status of ['running', 'waiting_approval', 'waiting_children', 'waiting_task', 'paused', 'cancelling']) {
  assert.equal(agentCanCancel({ session_id: 'current', status }), true, `cancellable: ${status}`);
}
for (const status of ['done', 'cancelled', 'blocked', 'error', 'planned', 'unknown']) {
  assert.equal(agentCanCancel({ session_id: 'current', status }), false, `terminal/read-only: ${status}`);
}
assert.equal(agentCanCancel(null), false);
console.log('Session monitor retry, heartbeat, non-overlap, stale generation and cancellation states passed');
