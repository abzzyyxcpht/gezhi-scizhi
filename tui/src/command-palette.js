// 命令面板纯逻辑（Ink/OpenTUI 双引擎共用；展示组件见 command-palette-view.js）
// 命令元数据分离：数据逻辑与渲染解耦。
import { commandsForMode } from './workspace-mode.js';

export const CONTROL_COMMANDS = [
  { name: 'palette', args: '', desc: '搜索命令名称、中文说明与插件命令' },
  { name: 'activity', args: '[会话ID]', desc: '只读工具活动、子任务与上下文压力' },
];

export function paletteCommands(commands = [], plugins = [], query = '', mode = 'research', context = {}) {
  const seen = new Set();
  const words = query.trim().replace(/^\//, '').toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const rows = [...commandsForMode(commands, mode, context).map(row => ({ ...row, desc: row.desc || row.description || '' })),
    ...plugins.map(row => ({ ...row, args: row.args || '', desc: row.help || row.description || '插件命令', group: '插件', scope: '插件', relevance: 3,
      busy_allowed: false, shortcut: '', effect: '插件操作', available: !context.busy && !context.remote,
      disabled_reason: context.busy ? '上一命令仍在执行；插件操作将在完成后可用'
        : context.remote ? '远程连接不执行插件操作，请在主机本地操作' : '' }))]
    .filter(row => {
      if (!row.name || seen.has(row.name)) return false;
      seen.add(row.name);
      const searchable = `${row.name} ${row.desc} ${row.args || ''} ${row.group} ${row.scope}`.toLocaleLowerCase();
      return words.every(word => searchable.includes(word));
    });
  return rows.sort((left, right) => {
    const leftText = `${left.name} ${left.desc} ${left.args || ''}`.toLocaleLowerCase();
    const rightText = `${right.name} ${right.desc} ${right.args || ''}`.toLocaleLowerCase();
    const leftScore = words.reduce((sum, word) => sum + Math.max(0, leftText.indexOf(word)), 0);
    const rightScore = words.reduce((sum, word) => sum + Math.max(0, rightText.indexOf(word)), 0);
    const leftExact = words.length === 1 && left.name.toLocaleLowerCase() === words[0];
    const rightExact = words.length === 1 && right.name.toLocaleLowerCase() === words[0];
    const leftDirect = words.filter(word => leftText.includes(word)).length;
    const rightDirect = words.filter(word => rightText.includes(word)).length;
    return Number(rightExact) - Number(leftExact) || left.relevance - right.relevance
      || rightDirect - leftDirect || leftScore - rightScore || left.name.localeCompare(right.name);
  });
}
