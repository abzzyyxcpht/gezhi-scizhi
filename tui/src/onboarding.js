import { redactText } from './sensitive-text.js';

const modelLabels = { configured: '已配置', not_configured: '未配置', disabled: '已停用', invalid: '配置需修正' };
const checkLabels = { ready: '就绪', action_needed: '需处理', info: '提示' };
const recorded = value => typeof value === 'string' && value.trim() ? redactText(value) : '未记录';

export function readinessText(readiness = {}, remote = false) {
  const model = readiness.model || {}, research = readiness.research || {}, suggestions = readiness.suggestions || {};
  const lines = [
    `格致 SciZhi ${recorded(readiness.version)} · 首次运行检查`, '',
    `当前执行：${readiness.agent_mode === 'model' ? '模型智能体' : readiness.agent_mode === 'rules' ? '规则引擎' : '未记录'}`,
    `模型配置：${modelLabels[model.status] || '未记录'} · 执行配置${model.execution_ready === true ? '可用' : model.execution_ready === false ? '需处理' : '未记录'}`,
    `模型 ${recorded(model.model)} · 协议 ${recorded(model.protocol)}`,
    '未验证连接：此检查只读取本地配置，不访问模型服务。',
    recorded(model.reason),
    remote ? '当前为远程工作台：以上是主机检查结果，模型配置请在主机操作。'
      : '检查后可选择离线体验，或配置模型处理自己的研究任务。',
    `离线样例：${research.offline_available === true ? '可用' : research.offline_available === false ? '暂不可用，请先处理检查项' : '未记录'}`, '',
    '开始使用：',
    `${recorded(suggestions.offline_command)}  离线合成样例入口，不修改模型或联网设置`,
    `${recorded(suggestions.settings_command)}  查看模型配置与路由`,
    remote ? '/model  请在主机配置默认模型与连接' : '/model  配置默认模型与连接；/connections 查看已有连接',
    `研究请求示例：${recorded(suggestions.example_request)}`, '',
    '检查结果：',
    ...(readiness.checks || []).map(check => `${checkLabels[check.status] || '提示'} · ${recorded(check.title)}：${recorded(check.detail)}`),
    '', `科研模式：${research.default_offline === true ? '默认离线' : research.default_offline === false ? '按当前联网设置执行' : '未记录'}`,
    recorded(research.notice),
    '/demo 使用内置合成样例，仅供体验，不可作为真实科研证据。',
  ];
  return lines.join('\n');
}

/** 离线示例结果校验：必须显式 offline 且带回任务 ID，否则 throw（调用方决定呈现方式）。 */
export function assertOfflineDemo(result) {
  if (result?.offline !== true || typeof result.task_id !== 'string' || !result.task_id) {
    throw new Error('离线示例未返回有效的离线任务，请检查引擎版本。');
  }
  return result.task_id;
}

export async function runOnboardingCommand(name, args, context) {
  if (!['setup', 'demo'].includes(name)) return false;
  if (String(args || '').trim()) { context.out(`用法：/${name}`, 'yellow'); return true; }
  const result = await context.bridge.call({ cmd: name === 'setup' ? 'product_readiness' : 'product_demo' });
  if (result?.ok !== true) throw new Error(redactText(result?.error || '引擎未返回有效结果'));
  if (name === 'setup') {
    const text = readinessText(result.readiness, context.bridge.connection?.().mode === 'remote');
    if (context.openReport) context.openReport('首次运行检查', text);
    else text.split('\n').forEach(line => context.out(line));
  } else {
    const taskId = assertOfflineDemo(result);
    context.openTask?.(taskId);
    context.out(`离线示例已启动：${taskId}`);
    context.out(redactText(result.notice || '内置合成样例，仅供体验；不可作为真实科研证据。'), 'yellow');
  }
  return true;
}
