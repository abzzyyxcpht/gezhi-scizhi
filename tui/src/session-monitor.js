// One in-flight status read per selection; terminal sessions retain a cheap heartbeat.
export const TERMINAL_SESSION = new Set(['done', 'error', 'blocked', 'cancelled', 'draft', 'unknown', 'planned']);
export const agentCanCancel = snapshot => Boolean(snapshot?.session_id && new Set([
  'running', 'paused', 'waiting_approval', 'waiting_task', 'waiting_children', 'waiting_human', 'cancelling',
]).has(snapshot.status));

export function monitorSession({ read, onSnapshot, onError, isCurrent = () => true,
  isTerminal = value => TERMINAL_SESSION.has((value.result || value.snapshot || {}).status)
    && (!value.result?.launch_pending || value.finished || value.worker_missing),
  initialMs = 400, activeMs = 1500, terminalMs = 5000, retryMs = 3000,
  schedule = setTimeout, unschedule = clearTimeout }) {
  let stopped = false, timer;
  const current = () => !stopped && isCurrent();
  const later = delay => { if (current()) timer = schedule(tick, delay); };
  const tick = async () => {
    if (!current()) return;
    try {
      const result = await read();
      if (!current()) return;
      onSnapshot(result);
      later(isTerminal(result) ? terminalMs : activeMs);
    } catch (error) {
      if (!current()) return;
      onError?.(error);
      later(retryMs);
    }
  };
  later(initialMs);
  return () => { stopped = true; unschedule(timer); };
}
