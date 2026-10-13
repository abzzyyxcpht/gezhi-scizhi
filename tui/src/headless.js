// Help/version require only Node built-ins, even before UI dependencies are installed.
import { readFileSync } from 'node:fs';
const VERSION = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;

export const USAGE = '用法：gezhi [--continue | --print "任务请求" | --help | --version]';
export const HELP = [
  `格致 SciZhi ${VERSION} · 通用与科研智能体工作台`, '', USAGE, '',
  '  无参数                 打开交互工作台（需要交互终端）',
  '  -c, --continue         恢复最近的 Agent 会话',
  '  -p, --print "任务请求"  单轮执行，遵循保存的通用/科研模式；stdout 只输出答复',
  '  -h, --help             显示帮助，不启动 Python 引擎',
  '  -v, --version          显示版本，不加载交互界面', '',
  '进入工作台后：',
  '  /mode general|research 切换新会话默认用途；Ctrl+G 一键切换，已有会话保持原角色/用途',
  '  Ctrl+P                 搜索全部命令，按当前模式优先显示',
  '  /setup                 首次运行检查、离线范例与配置入口',
  '  /models                查看模型配置与科研阶段路由',
  '  /connections           查看模型连接；/model 配置默认模型',
  '  /session-status        查看当前会话上下文与预算',
  '  /help                  查看全部交互命令', '',
  '首次使用：安装 Python 依赖和 TUI 依赖后，在交互终端运行 gezhi，再输入 /setup。',
].join('\n');

/**
 * 解析 CLI 参数：无参数 → interactive（保持现状）；
 * --continue → 启动后恢复最近会话；-p/--print → headless 单轮问答；--version → 打印版本。
 */
export function parseArgv(argv = []) {
  if (argv.includes('--help') || argv.includes('-h')) return { mode: 'help', query: '' };
  let mode = 'interactive';
  let query = '';
  const error = reason => ({ mode: 'error', error: `${reason}。请使用 --help 查看用法。\n${USAGE}` });
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (mode !== 'interactive') return error('不能同时指定多个启动模式或多余参数');
    if (arg === '--version' || arg === '-v') mode = 'version';
    else if (arg === '--continue' || arg === '-c') mode = 'continue';
    else if (arg === '-p' || arg === '--print') {
      mode = 'print'; query = argv[++i] ?? '';
      if (query.startsWith('-')) return error('--print 缺少任务请求（以 - 开头的请求可用 --print=请求）');
    } else if (arg.startsWith('--print=')) { mode = 'print'; query = arg.slice('--print='.length); }
    else if (arg.startsWith('-p') && arg.length > 2) { mode = 'print'; query = arg.slice(2); }
    else return error('未知参数');
  }
  if (mode === 'print' && !String(query).trim()) return error('--print 需要非空任务请求');
  return { mode, query };
}

/** Print the package version through the lightweight entry path. */
export function printVersion(writer = (text) => process.stdout.write(`${text}\n`)) {
  writer(`gezhi ${VERSION}`);
}

export function printHelp(writer = text => process.stdout.write(`${text}\n`)) { writer(HELP); }

/**
 * 单轮问答：启动桥、发送 agent_run（服务端跑完整个工具循环）、
 * 把最终 assistant 文本写到 stdout 并返回 0；任何失败/审批中断写 stderr 并返回 1。
 * stdout 保持纯净（只含答复），可直接管道给下游命令。
 */
export async function runHeadlessPrint(query, { bridge, stdout, stderr, timeoutMs = 1800000 } = {}) {
  const b = bridge || new (await import('./bridge.js')).Bridge({});
  const out = stdout || ((text) => process.stdout.write(`${text}\n`));
  const err = stderr || ((text) => process.stderr.write(`${text}\n`));
  try {
    await b.whenReady();
    // 在线检索 + 逐篇抽取的完整任务可显著超过交互模式的 5 分钟默认超时；
    // headless 是自主批处理，给到 30 分钟并在注释中说明可经 timeoutMs 覆盖。
    // The bridge chooses the saved workspace mode; an untouched install remains research.
    const response = await b.call({ cmd: 'agent_run', request: query, mode: 'agent' },
      { timeoutMs });
    const result = response?.result || {};
    if (!response?.ok || ['error', 'blocked', 'cancelled'].includes(result.status)) {
      err(`错误：${response?.error || result.error || result.status || '执行失败'}`);
      return 1;
    }
    if (result.status === 'waiting_approval' || result.pending) {
      err(`错误：执行停在工具审批（${result.pending?.tool || '未知工具'}）；请用交互模式 /approve 处理`);
      return 1;
    }
    const answer = result.answer || result.message || result.content;
    if (typeof answer === 'string') out(answer);
    return 0;
  } catch (error) {
    err(`错误：${error.message || error}`);
    return 1;
  } finally {
    b.close?.();
  }
}
