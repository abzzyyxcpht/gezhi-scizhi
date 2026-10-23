export const REVISION = 'c'.repeat(64);
export function checklist(overrides = {}) {
  return { revision: REVISION, verdict: 'needs_review', total: 3, satisfied: 1,
    counts: { satisfied: 1, failed: 0, stale: 1, pending: 1, unknown: 0 },
    items: [
      { id: 'artifact:lit_filter', title: '文献池', state: 'satisfied', criterion: '保留筛选后的文献',
        detail: '已记录该产物', evidence: [{ artifact: 'lit_filter' }] },
      { id: 'material:integrity', title: '材料完整性', state: 'stale', criterion: '材料哈希一致',
        detail: '1 份材料已变化', evidence: [{ material: 'material01', recorded_sha256: 'a'.repeat(64), current_sha256: 'b'.repeat(64) }] },
      { id: 'review:manual', title: '人工复核', state: 'pending', criterion: '人工检查本版本的成果',
        detail: '尚未记录人工复核', evidence: [] },
    ], ...overrides };
}
export const reviewCommand = (task = 'task01', decision = 'approved') =>
  `/checks review ${task} --decision ${decision} --by "Jane Doe" --note "已核对引用与图表"`;
export function controllerBridge() {
  const state = { mode: 'local', generation: 1, calls: [], checks: checklist(), readReply: null,
    writeReply: null, readError: null, writeError: null, holdRead: null };
  const bridge = {
    get localGeneration() { return state.generation; },
    connection: () => ({ mode: state.mode, url: state.mode === 'remote' ? 'http://fixture.invalid' : '' }),
    whenReady: async () => true,
    async call(request) {
      state.calls.push(request);
      if (request.cmd === 'outcome_checks') {
        if (state.readError) throw new Error(state.readError);
        if (state.holdRead) return state.holdRead(request);
        return state.readReply || { ok: true, checks: state.checks };
      }
      if (request.cmd === 'outcome_review') {
        if (state.writeError) throw new Error(state.writeError);
        return state.writeReply || { ok: true, task_id: request.task_id,
          review: { ...request.record, revision: request.record.expected_revision, at: '2026-10-23T12:00:00+08:00' } };
      }
      if (request.cmd === 'command' && request.text.startsWith('/open ')) {
        const task_id = request.text.slice(6);
        return { ok: true, task_id, text: `已打开任务 ${task_id}` };
      }
      return { ok: true };
    },
  };
  return { state, bridge };
}
