import { randomUUID } from 'node:crypto';
import { redactText } from './sensitive-text.js';
const pending = new WeakMap();
const labels = { queued: '排队中', running: '复核中', cancelling: '正在取消', ready: '待研究者核查',
  interrupted: '已中断，需明确恢复', error: '复核失败', cancelled: '已取消', accepted: '已保存复核备注', dismissed: '已忽略' };

export async function runReviewCommand(name, args, context) {
  if (name !== 'reviews') return false;
  const { bridge, out } = context;
  const words = String(args || '').trim().split(/\s+/).filter(Boolean), action = words[0] || 'list';
  const call = async request => {
    const result = await bridge.call(request);
    if (result?.ok !== true) throw new Error(redactText(result?.error || '后台复核结果未确认'));
    return result;
  };
  const usage = () => { out('用法：/reviews [list [记录ID]|start <记录ID> <版本> <local|model>|show|events|cancel|resume|accept|dismiss <任务ID>|enable|disable]'); return true; };
  const line = job => `${job.id} · ${labels[job.status] || job.status} · ${job.title} · 版本 ${job.record_version} · ${job.mode === 'local' ? '本地检查' : '模型复核'}${job.stale ? ' · 原记录已变化' : ''}`;
  if (['enable', 'disable'].includes(action) && words.length === 1) {
    if (bridge.connection?.().mode === 'remote') { out('请在主机开启或关闭后台证据复核实验。'); return true; }
    const settings = { experimental_evidence_reviews: action === 'enable' };
    await call({ cmd: 'settings_save', settings });
    context.onSettings?.(settings);
    out(action === 'enable' ? '后台证据复核已启用，创建任务需要明确记录版本与复核方式。' : '后台复核实验已关闭；已有执行可显式取消，读取不会启动新任务。');
    return true;
  }
  if (action === 'list' && words.length <= 2) {
    const result = await call({ cmd: 'review_list', ...(words[1] ? { record_id: words[1] } : {}) });
    if (!Array.isArray(result.jobs)) throw new Error('收件箱未返回有效的任务列表');
    out(`后台复核收件箱 · ${result.enabled ? '实验已启用' : '实验未启用'} · 显示 ${result.jobs.length}/${result.total}`);
    result.jobs.forEach(job => out(redactText(line(job))));
    (result.warnings || []).forEach(row => out(redactText(`${row.job_id} · ${row.error}`), 'yellow'));
    return true;
  }
  if (action === 'start' && words.length === 4 && /^[1-9]\d*$/.test(words[2]) && ['local', 'model'].includes(words[3])) {
    const record = words[1], version = Number(words[2]), mode = words[3];
    if (!Number.isSafeInteger(version)) return usage();
    let keys = pending.get(bridge);
    if (!keys) { keys = new Map(); pending.set(bridge, keys); }
    const connection = bridge.connection?.() || {};
    const signature = JSON.stringify([connection.mode, connection.url, record, version, mode]);
    const key = keys.get(signature) || randomUUID();
    keys.set(signature, key);
    try {
      const result = await call({ cmd: 'review_start', record_id: record, expected_version: version, mode, review_request_id: key });
      const job = result.job;
      if (!job?.id || job.request_id !== key || job.record_id !== record || job.record_version !== version || job.mode !== mode) throw new Error('复核创建回执与请求不匹配');
      keys.delete(signature);
      out(redactText(line(job)));
      out('可继续其他研究；/reviews show <任务ID> 查看。accept 仅保存复核备注，不验证科学结论。');
    } catch (error) {
      out('创建结果未确认，重试相同记录、版本与方式会沿用本次请求标识。', 'yellow');
      throw new Error(redactText(error.message || error));
    }
    return true;
  }
  if (['show', 'events', 'cancel', 'resume', 'accept', 'dismiss'].includes(action) && words.length === 2) {
    if (action === 'events') {
      const result = await call({ cmd: 'review_events', job_id: words[1], limit: 100, tail: true });
      const text = JSON.stringify(result.events, null, 2);
      if (context.openReport) context.openReport('最近100条复核执行记录', redactText(text)); else out(redactText(text));
      return true;
    }
    const result = await call({ cmd: action === 'show' ? 'review_get' : `review_${action}`, job_id: words[1] });
    const job = result.job;
    if (job?.id !== words[1] || !Object.hasOwn(labels, job.status)) throw new Error('复核回执与目标任务不匹配');
    if (action === 'accept' && job.status !== 'accepted') throw new Error('尚未确认复核备注已保存');
    const text = [line(job), job.phase || '', job.error || '', '', job.result?.text || '尚无报告。', '',
      '复核结果是建议，未验证来源真实性或科学支持；原记录变化后不能采纳旧结果。'].join('\n');
    if (action === 'show' && context.openReport) context.openReport('后台证据复核', redactText(text)); else out(redactText(action === 'show' ? text : line(job)));
    return true;
  }
  return usage();
}
