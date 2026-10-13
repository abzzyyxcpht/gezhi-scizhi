import { redactSettings, redactText } from './sensitive-text.js';
import { presentAgentResult } from './research-commands.js';

export const FEATURE_COMMANDS = [
  ['evidence', '[任务ID]', '对照支持/反驳材料与范围，查看未发送的下一问草稿'],
  ['tower', '', '控制塔：任务、审批、用量与团队状态'],
  ['swarm', 'plan <目标>|start/get/cancel <ID>|list', '并行科研团队：先计划后执行'],
  ['pdf', '<路径> [字符数]', '提取 PDF 全文文本'],
  ['memory', '[list|save <JSON>|delete <ID>]', '长期研究记忆'],
  ['commands', '', '自定义提示词命令'],
  ['command-save', '<JSON>', '保存提示词命令'],
  ['command-delete', '<名称>', '删除提示词命令'],
  ['run', '<模板名> <输入>', '执行提示词命令'],
  ['notebook', '[list|schema|get|save|export|delete]', '假设与实验记录'],
  ['mcp-config', '[list|save <JSON>|delete|probe <名称>]', 'MCP 服务配置'],
  ['browser', '[status|bind|on|off|call]', '浏览器控制连接'],
  ['computer', '[status|bind|on|off|call]', '电脑控制连接'],
  ['hooks', '[list|save <JSON>|delete <ID>]', '运行时事件钩子'],
  ['shortcuts', '[save <JSON>]', '桌面快捷键'],
  ['automations', '[list|start|stop|resume]', '有界研究自动化'],
  ['subagents', '[on|off|parallel <1–4>]', '子智能体开关与并行'],
  ['preferences', '[save <JSON>]', '通用与外观偏好'],
  ['project', '[list|open|create|select|files|preview|git|diff|commit|push]', '项目、文件与代码版本'],
  ['chat', '[new|list|get|send|attach|detach|fork|side|archive|restore|share]', '连续研究会话'],
  ['todo', '[list|save <JSON>|delete <ID>]', '研究待办'],
  ['schedule', '[list|save <JSON>|delete|run <ID>]', '定时科研任务'],
  ['notifications', '[read <ID>|read-all]', '完成提示'],
  ['handoff', '[项目ID]', '导出科研交接包'],
  ['package', '[任务ID]', '导出可复现研究包（manifest+产物+工作流）'],
  ['policy', '[read-only|workspace-write|full-access]', '查看或设置科研权限三档门'],
].map(([name, args, desc]) => ({ name, args, desc }));

export async function runFeatureCommand(name, args, context) {
  if (!FEATURE_COMMANDS.some(c => c.name === name)) return false;
  if (name === 'evidence' && context.bridge.connection?.().mode === 'remote') {
    const taskId = String(args || context.taskId || '').trim();
    if (taskId && !/^[A-Za-z0-9_-]{1,160}$/.test(taskId)) { context.out('用法：/evidence [任务ID]', 'yellow'); return true; }
    const result = await context.bridge.call({ cmd: 'evidence_brief', ...(taskId ? { task_id: taskId } : {}) });
    if (result.ok === false) throw new Error(redactText(result.error || '证据对照读取失败'));
    const brief = result.brief || {}, lines = [`证据对照 · ${brief.scope?.task_id || '全部研究记录'}`,
      brief.boundary || '材料关系尚未进行科学验证。', ''];
    for (const row of brief.records || []) {
      lines.push(`${row.title || ''} · ${row.id || ''} · 版本 ${row.version ?? '未记录'}`, row.question || '');
      for (const [relation, label] of Object.entries({ supports: '支持', challenges: '反驳', context: '背景' })) {
        lines.push(`${label}材料 ${row.counts?.[relation] ?? '未记录'} 条`);
        for (const item of row.groups?.[relation] || []) {
          const source = item.source || {};
          lines.push(`  ${source.doi || source.id || source.url || '来源未记录'} · 范围声明：${item.scope || '未记录'}${item.synthetic ? ' · 合成样例' : ''}`, `  ${item.quote || ''}`);
          if (item.quote_modified || item.quote_truncated) lines.push('  [展示已截断、脱敏或清理控制字符；完整原记录另存。]');
        }
      }
      if (row.gaps?.length) lines.push(`待补：${row.gaps.join('；')}`);
      if (row.evidence_truncated) lines.push('部分证据未展开；完整材料请查看研究记录。');
      lines.push('下一问草稿（未发送）：', row.followup_draft || '未生成', '');
    }
    if (!brief.records?.length) lines.push('当前范围尚无研究记录，可先录入支持/反驳材料。');
    if (brief.records_truncated) lines.push(`显示 ${brief.visible_records ?? '部分'} 条，共 ${brief.total_records ?? '未记录'} 条；完整记录请查看研究记录。`);
    const text = lines.map(line => redactText(line)).join('\n');
    if (context.openReport) context.openReport('证据对照', text);
    else text.split('\n').forEach(line => context.out(line));
    return true;
  }
  if (context.bridge.connection?.().mode === 'remote') {
    context.out('此设置命令请在主机操作；远程端可用 /usage、/sessions、/continue 和 /cancel-agent。', 'yellow');
    return true;
  }
  const result = await context.bridge.call({ cmd: 'command', text: `/${name} ${args || ''}`, task_id: context.taskId || undefined });
  if (result.ok === false) throw new Error(redactText(result.error || '操作失败'));
  if (name === 'chat' && /^send(?:\s|$)/.test((args || '').trim())) {
    const sessionId = result.result?.session_id || result.session_id;
    if (sessionId) context.onAgentUserTurn?.(sessionId);
  }
  if (result.swarm?.session_id && !['planned', 'cancelled'].includes(result.swarm.status)) context.setCurrentAgentSession?.(result.swarm.session_id);
  if (result.result?.session_id) presentAgentResult(result.result, context);
  else if (result.session_id && result.status) presentAgentResult(result, context);
  else {
    if (result.session?.session_id) context.setCurrentAgentSession?.(result.session.session_id);
    const value = result.text || JSON.stringify(redactSettings(result), null, 2);
    redactText(value).split('\n').forEach(line => context.out(line));
  }
  return true;
}
