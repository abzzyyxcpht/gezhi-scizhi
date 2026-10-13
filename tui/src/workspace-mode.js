// Workspace preference is distinct from execution mode and local/remote transport.
import { FEATURE_COMMANDS } from './feature-commands.js';
import { queueCanRunWhileBusy } from './queue-commands.js';
export const normalizeWorkspaceMode = value => value === 'general' ? 'general' : 'research';
export const workspaceModeLabel = value => normalizeWorkspaceMode(value) === 'general' ? '通用' : '科研';
export const workspaceModeSymbol = value => normalizeWorkspaceMode(value) === 'general' ? '◇' : '◎';

const researchCommands = new Set(['new', 'tasks', 'workflows', 'workflow', 'loop', 'open', 'status', 'nodes',
  'pause', 'resume', 'confirm', 'cancel', 'rerun', 'delete', 'drop', 'pool', 'digest', 'outline', 'cites',
  'export', 'stats', 'style', 'board', 'graph', 'audit', 'skills-selection', 'read', 'ledger', 'evidence',
  'swarm', 'notebook', 'reviews', 'automations', 'schedule', 'handoff',
  'hypotheses', 'consensus', 'matrix', 'experiments', 'novelty', 'goal', 'facts', 'fact', 'peer-review', 'reproduce', 'config']);
const sessionCommands = new Set(['agent', 'plan', 'review', 'sessions', 'continue', 'cancel-agent', 'session',
  'session-status', 'compact', 'queue', 'steer', 'history', 'outputs', 'session-export', 'approve', 'deny',
  'agents', 'spawn', 'activity', 'chat', 'rewind']);
const workspaceCommands = new Set(['project', 'terminal', 'pdf', 'memory', 'commands', 'command-save',
  'command-delete', 'run', 'browser', 'computer', 'todo', 'notifications', 'context', 'init']);

const busyAllowed = new Set(['mode', 'cancel-agent', 'steer', 'reviews', 'history', 'outputs', 'queue']);
const remoteDisabled = new Set(['mode', 'agent', 'spawn', 'review', 'plan', 'model', 'models', 'reasoning',
  'connection', 'profile', 'settings', 'toggle', 'plugins', 'skills', 'skill', 'mcp', 'git', 'doctor',
  'init', 'context', 'rerun', 'delete', 'drop', 'export', 'stats', 'agents', 'rewind', 'remote',
  'board', 'graph', 'ledger', 'audit', 'skills-selection', 'hypotheses', 'consensus', 'matrix', 'experiments', 'goal', 'facts', 'peer-review', 'reproduce', 'config',
  ...FEATURE_COMMANDS.filter(row => row.name !== 'evidence').map(row => row.name)]);
const effects = {
  mode: '新会话默认用途；已有会话保持原角色/用途', palette: '打开命令搜索', help: '显示帮助', clear: '清空转写',
  agent: '启动或继续 Agent 会话', plan: '只读规划', review: '只读审计',
  approve: '批准工具调用', deny: '拒绝工具调用', 'cancel-agent': '取消 Agent 会话',
  steer: '补充当前回合', queue: '管理下一轮队列', history: '读取会话历史', outputs: '读取工具输出',
};
const shortcuts = { mode: 'Ctrl+G', palette: 'Ctrl+P', 'cancel-agent': 'Esc', help: '/help' };

/**
 * Attach one stable command contract to every command row. The context is deliberately
 * small and presentation-only; execution still goes through app.js busy/remote gates.
 */
export function commandsForMode(commands = [], mode = 'research', context = {}) {
  const general = normalizeWorkspaceMode(mode) === 'general';
  const busy = context.busy === true;
  const remote = context.remote === true;
  return commands.map(row => {
    const args = String(context.command?.name === row.name ? context.command.args || '' : context.args || '').trim();
    const action = args.split(/\s+/)[0] || 'list';
    const research = researchCommands.has(row.name);
    const group = research ? '科研工作流' : sessionCommands.has(row.name) ? '会话与执行'
      : workspaceCommands.has(row.name) ? '项目与工具' : '设置与帮助';
    const relevance = row.name === 'mode' ? 0 : research ? general ? 4 : 1
      : sessionCommands.has(row.name) ? general ? 1 : 2 : workspaceCommands.has(row.name) ? general ? 2 : 3 : 3;
    const desc = ['agent', 'plan', 'review'].includes(row.name)
      ? { agent: `新建${general ? '通用' : '科研'}用途 Agent，可显式指定角色`,
        plan: '只读规划：拆解请求，不执行副作用', review: '只读审计：检查结果、证据与风险' }[row.name]
      : row.desc;
    const busy_allowed = row.name === 'queue' ? queueCanRunWhileBusy(args) : busyAllowed.has(row.name);
    const remoteSupportedArgument = row.name === 'model' && action === 'capabilities';
    const remoteBlocked = remote && (remoteDisabled.has(row.name) && !remoteSupportedArgument
      || ['queue', 'steer', 'reviews'].includes(row.name) && ['enable', 'disable'].includes(action)
      || row.name === 'route' && Boolean(args)
      || row.name === 'read' && ['board', 'graph', 'ledger'].includes(action));
    const available = !remoteBlocked && (!busy || busy_allowed);
    const disabled_reason = remoteBlocked
      ? row.name === 'mode' ? '远程主机未开放模式设置，请在主机本地 TUI 操作'
        : row.name === 'agent' || row.name === 'spawn' || row.name === 'review' || row.name === 'plan'
          ? '远程主机未开放 Agent 启动/规划，请在主机本地 TUI 操作'
          : '远程连接不支持此操作，请在主机本地 TUI 操作'
      : !available ? busy_allowed ? '' : '上一命令仍在执行；可先取消、补充或排队'
        : '';
    return { ...row, desc, group, scope: research ? '科研' : '两种模式', relevance,
      busy_allowed, shortcut: shortcuts[row.name] || '', effect: effects[row.name] || (research ? '科研工作流操作' : '工作台操作'),
      available, disabled_reason };
  }).sort((left, right) => left.relevance - right.relevance);
}
