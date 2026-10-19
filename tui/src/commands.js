// 格致工作台 · 版本常量（唯一来源：package.json；本文件与打包脚本均派生自此）
// 格致工作台 · 版本常量（唯一来源：package.json；本文件与打包脚本均派生自此）
import { readFileSync } from 'node:fs';
export const VERSION = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;
import { FEATURE_COMMANDS } from './feature-commands.js';
import stringWidth from 'string-width';

const widthSegments = new Intl.Segmenter(undefined, { granularity: 'grapheme' });

/**
 * 人工确认关口的内联菜单：
 * 进入 waiting_human 时展示，输入对应数字回车即可执行。
 */
export const REVIEW_MENU = [
  { key: '1', label: '确认放行，继续后续节点', cmd: '/confirm' },
  { key: '2', label: '剔除末位文献后再看', cmd: '/drop-last' },
  { key: '3', label: '展开文献池复核', cmd: '/pool 10' },
  { key: '4', label: '就地暂停，稍后处理', cmd: '/pause' },
];

/** 命中的菜单项（仅接受单独数字 + 回车）；未命中返回 null。 */
export function matchMenu(raw, menu) {
  const s = (raw || '').trim();
  if (!/^\d$/.test(s)) return null;
  return (menu || []).find((m) => m.key === s) || null;
}

// 格致工作台 · slash 命令注册表
// 命令体系：/ 开头解析为命令，
// 非 / 开头的整行输入视为研究方向（快速开任务）。命令只带元数据，
// 执行器在 app.js（需要 bridge 与界面状态）。
export const COMMANDS = [
  ...FEATURE_COMMANDS,
  // ---- 会话 ----
  { name: 'mode',    args: '[general|research|toggle]', desc: '切换新会话默认用途（Ctrl+G）；已有会话保持原角色/用途' },
  { name: 'help',    args: '',                desc: '显示全部命令' },
  { name: 'setup',   args: '',                desc: '首次运行检查、离线范例与模型配置入口' },
  { name: 'demo',    args: '',                desc: '启动内置离线合成样例，不修改现有配置' },
  { name: 'menu',    args: '',                desc: '切换到菜单模式' },
  { name: 'clear',   args: '',                desc: '清空工作台输出' },
  { name: 'exit',    args: '',                desc: '退出格致' },
  // ---- 任务生命周期 ----
  { name: 'new',     args: '<方向> [关键词]', desc: '新建科研任务并启动流水线' },
  { name: 'tasks',   args: '',                desc: '列出全部任务档案' },
  { name: 'processes', args: '',              desc: '任务进程与 Loop 状态（别名 /ps）' },
  { name: 'ps', args: '',                     desc: '任务进程与 Loop 状态' },
  { name: 'workflows', args: '',              desc: '科研工作流目录与审批关口' },
  { name: 'workflow', args: '<id> <主题>',      desc: '运行选定科研工作流' },
  { name: 'loop', args: 'start|list|stop|resume', desc: '有限轮次定期工作流，保留人工审批' },
  { name: 'open',    args: '<任务ID|序号>',   desc: '打开（跟踪）一个任务' },
  { name: 'status',  args: '',                desc: '当前任务快照' },
  { name: 'nodes',   args: '',                desc: '工作流节点序列' },
  // ---- 运行控制（F5 暂停键） ----
  { name: 'pause',   args: '',                desc: '暂停（轮次间隙生效）' },
  { name: 'resume',  args: '',                desc: '断点续跑' },
  { name: 'confirm', args: '',                desc: '人工确认审批节点并续跑' },
  { name: 'cancel',  args: '',                desc: '取消（节点边界生效）' },
  { name: 'rerun',   args: '',                desc: '整体重跑当前任务' },
  { name: 'delete',  args: '',                desc: '删除当前任务与断点存档' },
  // ---- 人工干预与产物 ----
  { name: 'drop',    args: '<池内序号>',      desc: '从文献池剔除一篇（版本化留痕）' },
  { name: 'pool',    args: '[前N篇]',         desc: '查看精准文献池' },
  { name: 'digest',  args: '',                desc: '查看摘要整理稿' },
  { name: 'outline', args: '',                desc: '查看综述框架' },
  { name: 'cites',   args: '',                desc: '查看参考文献' },
  { name: 'export',  args: '',                desc: '导出全部产物' },
  // ---- 功能直达 ----
  { name: 'stats',   args: '<数据文件路径>',  desc: '数据速览：统计与可视化建议' },
  { name: 'style',   args: '<gbt7714|apa>',   desc: '设置引用格式（作用于新任务）' },
  // ---- 系统 ----
  { name: 'usage',   args: '[read]',          desc: '按连接/模型查看真实用量与缓存；read 全屏' },
  { name: 'doctor',  args: '',                desc: '系统自检' },
  { name: 'model',   args: '[模型名|protocol|reasoning|thinking|budget]', desc: '默认模型、协议与思考参数' },
  { name: 'connections', args: '',           desc: '模型服务连接（密钥隐藏）' },
  { name: 'connection', args: 'save <JSON>|delete <id>', desc: '保存或删除连接，仅本机' },
  { name: 'models', args: '',                desc: '模型配置与科研阶段路由' },
  { name: 'profile', args: 'save <JSON>|delete <id>', desc: '保存或删除模型配置，仅本机' },
  { name: 'route', args: '[阶段 profile_id]', desc: '科研阶段的模型路由' },
  { name: 'settings', args: '',               desc: '查看运行设置（密钥打码）' },
  { name: 'theme',   args: '',                desc: '切换主题强调色' },
  // ---- 扩展生态 ----
  { name: 'init',    args: '',                desc: '生成 GEZHI.md 研究上下文模板' },
  { name: 'context', args: '',                desc: '当前会话上下文来源；无会话时发现项目规则' },
  { name: 'skills',  args: '',                desc: '技能模式、调用状态与来源' },
  { name: 'skill',   args: '<名> <auto|on|off>', desc: '按阶段选择、明确启用或停用技能' },
  { name: 'plugins', args: '[enable|disable <名>|reload]', desc: '查看、启停、重扫插件' },
  { name: 'mcp',     args: '',                desc: 'MCP 服务器与工具清单' },
  { name: 'git',     args: '[log|status]',    desc: '研究档案库（git 版本管理）' },
  // ---- 科研特性工作台 ----
  { name: 'board',   args: '',                desc: '科研看板（文献池质控与收敛可视化）' },
  { name: 'graph',   args: '',                desc: '证据图谱（锚定率 / 孤儿文献 / 缺证据章节）' },
  { name: 'audit', args: '[export]',           desc: '科研证据矩阵、引用与论断审计' },
  { name: 'skills-selection', args: '<阶段>', desc: '按当前设置预览阶段技能索引' },
  { name: 'read',    args: '<面板>',          desc: '全屏阅读器（digest/outline/cites/board/graph/ledger）' },
  { name: 'reasoning', args: '[推理强度]',    desc: '查看或调整默认模型的推理强度' },
  { name: 'ledger',  args: '',                desc: '研究时间账（跨任务累计省时）' },
  // ---- 科研创新面板----
  { name: 'hypotheses', args: '',              desc: '假设排行榜：证据加权 + 状态加成的竞争排序' },
  { name: 'consensus', args: '',               desc: '共识计量条：支持 / 背景 / 反驳 三段统计' },
  { name: 'matrix',  args: '',                 desc: '证据矩阵：记录 × 关系 × 缺口 × 下一问' },
  { name: 'experiments', args: '',             desc: '实验树：分支探索 / 剪枝 / 晋升可视化' },
  { name: 'novelty', args: '<课题>',           desc: '新颖性检测：是否有人做过 + 差异点 + 风险' },
  { name: 'goal',    args: '<目标>|status|off', desc: '研究目标模式：目标常驻上下文，逐轮自评' },
  { name: 'facts',   args: '',                 desc: '世界模型面板：跨任务共享事实卡片（Enter 一键支持）' },
  { name: 'fact',    args: 'add <论断> [来源] | support|challenge|del <ID> | list', desc: '世界模型事实：录入 / 支持 / 反驳 / 删除' },
  { name: 'peer-review', args: '<科研记录ID>', desc: 'AI 审稿人：结构/证据/可检验/闭合/深度五维评分与建议' },
  { name: 'reproduce', args: '<论文课题或方向>', desc: '论文复现流水线：计划→确认→白名单执行→实验记录' },
  { name: 'config',  args: '',                 desc: '配置面板：模型 / 主题 / 科研 / 界面（即切即存）' },
  { name: 'timeline', args: '',                desc: '科研时间线：流水线阶段 / 空洞 / 证据计量（科研模式·创新模块）' },
  // ---- Agent Harness ----
  { name: 'plan',    args: '<任务请求>',       desc: '只读规划：拆解任务，不执行副作用' },
  { name: 'agent',   args: '[角色] <请求>',  desc: '新建当前模式用途的 Agent；可指定 general/research 等角色' },
  { name: 'approve', args: '[session tool]',   desc: '批准待执行工具调用' },
  { name: 'deny',    args: '[session tool]',   desc: '拒绝待执行工具调用' },
  { name: 'sessions',args: '',                 desc: '列出可恢复 Agent 会话' },
  { name: 'continue', args: '[会话ID]',        desc: '继续当前或指定的 Agent 会话' },
  { name: 'rewind',  args: '[N]',              desc: '回滚当前 Agent 会话 N 个用户轮次（默认 1）' },
  { name: 'cancel-agent', args: '[会话ID]',    desc: '取消当前或指定的 Agent 会话' },
  { name: 'session', args: '<id>',             desc: '全屏阅读完整会话快照' },
  { name: 'session-status', args: '[会话ID]',  desc: '查看会话模型、上下文、预算与等待原因' },
  { name: 'progress', args: '[会话ID]', desc: '查看本轮执行计划、完成标准与工具回执' },
  { name: 'compact', args: '[会话ID]',         desc: '静止会话确定性压缩上下文，不调用模型' },
  { name: 'queue', args: '[list|add <文本>|pause|resume|remove|discard|reorder|enable|disable]', desc: '实验性下一轮队列，保留当前执行与审批边界' },
  { name: 'steer', args: '[status|add <文本>|enable|disable]', desc: '实验性本轮补充，绑定当前回合并保留工具审批' },
  { name: 'history', args: '[页码] [关键词] | search <关键词> | refresh | message <ID> [偏移]', desc: '索引分页读取与搜索完整对话；含本轮补充' },
  { name: 'outputs', args: '[偏移] | read <ID> [字节偏移] | search <ID> <字节偏移> <原文>', desc: '读取、搜索本会话归档的完整工具输出' },
  { name: 'reviews', args: '[list|start <记录ID> <版本> <local|model>|show|events|cancel|resume|accept|dismiss|enable|disable]', desc: '后台证据复核收件箱；结果需显式核查并保存备注' },
  { name: 'session-export', args: '<id> [markdown|json]', desc: '导出 Agent 会话记录' },
  { name: 'agents',  args: '',                 desc: '专项智能体编制表' },
  { name: 'spawn',   args: '<智能体> <请求>',    desc: '派发专项子智能体' },
  { name: 'review',  args: '<任务请求>',        desc: '只读审计：结果、证据缺口与风险' },
  { name: 'terminal', args: '',                desc: '打开本机原生终端' },
  { name: 'remote', args: 'start [端口]|status|stop', desc: '手机局域网远程控制入口' },
  { name: 'connect', args: '<url> <token>',     desc: '连接远程工作台（令牌不进历史）' },
  { name: 'disconnect', args: '',              desc: '切回本机工作台' },
  { name: 'toggle', args: '<设置名> <on|off>',   desc: '修改持久化功能开关' },
  { name: 'panel', args: '<on|off>',            desc: '显示或收起右侧工具面板' },
];

/** 解析输入行：/cmd args → {name, args}；非命令返回 null。 */
export function parseCommand(raw) {
  const s = (raw || '').trim();
  if (!s.startsWith('/')) return null;
  const match = s.slice(1).match(/^(\S*)(?:\s+([\s\S]*))?$/);
  return { name: (match?.[1] || '').toLowerCase(), args: (match?.[2] || '').trim() };
}

/** 按终端显示宽度补空格（CJK 全角字符按 2 列计），用于帮助表对齐。 */
export function padWide(s, n) {
  const text = String(s ?? '');
  return text + ' '.repeat(Math.max(0, Math.floor(Number(n) || 0) - stringWidth(text)));
}

/**
 * 按显示宽度裁切（CJK 记 2 列），超出时以省略号收尾。
 * 省略号占 1 列，所以裁切上限是 cols - 1：否则结果会比列宽多 1 列，
 * 相邻列就会粘连（文献池表格曾因此出现「…2026」）。
 */
export function clipWide(s, cols) {
  const text = String(s ?? '');
  const limit = Math.max(0, Math.floor(Number(cols) || 0));
  if (!limit) return '';
  if (stringWidth(text) <= limit) return text;
  let w = 0, out = '';
  for (const { segment } of widthSegments.segment(text)) {
    const cw = stringWidth(segment);
    if (w + cw > limit - 1) break;
    out += segment; w += cw;
  }
  return out + '…';
}
