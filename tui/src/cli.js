#!/usr/bin/env node
// 格致 SciZhi TUI 入口
// 无参数：交互界面（exitOnCtrlC=false：改为「双击退出」，首次只提示，避免误触直接丢会话）
// --continue：启动后自动恢复最近会话；-p/--print：headless 单轮问答；--version：打印版本。
import { parseArgv, runHeadlessPrint, printVersion, printHelp } from './headless.js';
import { resolveEngine } from './engine-select.js';

const argv = parseArgv(process.argv.slice(2));
if (argv.mode === 'version') {
  printVersion();
} else if (argv.mode === 'help') {
  printHelp();
} else if (argv.mode === 'error') {
  console.error(argv.error);
  process.exitCode = 1;
} else if (argv.mode === 'print') {
  process.exitCode = await runHeadlessPrint(argv.query);
} else if (!process.stdin.isTTY || !process.stdout.isTTY) {
  console.error('当前输入/输出不是交互终端。请在终端运行 gezhi，或使用 gezhi --print "任务请求"。');
  printHelp();
  process.exitCode = argv.mode === 'continue' ? 1 : 0;
} else if (resolveEngine({ env: process.env, stdin: process.stdin, stdout: process.stdout, mode: argv.mode }) === 'opentui') {
  // OpenTUI 引擎（0.9.0 起唯一交互引擎；历史 Ink 引擎已移除）
  try {
    const { run } = await import('./opentui/runtime.js');
    await run({ autoResume: argv.mode === 'continue' });
  } catch (error) {
    console.error(`OpenTUI 引擎启动失败：${String(error?.message || error).slice(0, 300)}`);
    console.error('请检查 node_modules 是否完整（npm ci）后重试；或以 gezhi --print "任务请求" 使用 headless 模式。');
    process.exitCode = 1;
  }
}
