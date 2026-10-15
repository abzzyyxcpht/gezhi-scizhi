// 工作台：
// 原生终端启动契约（src/terminal.js 活模块，断言原样保留）。
// 注：Ink 的「工具与进程」宽窄检查器面板由 OpenTUI 侧栏三态 + Ctrl+B 取代，
// 响应式断言由 opentui-shell-ui / opentui-runtime-ui 覆盖；/connect 输入掩码由
// runtime safeCommandEcho 覆盖（见 research-ui / smoke 迁移版）。
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { openLocalTerminal } from '../src/terminal.js';

let launchArgs;
await openLocalTerminal({ platform: 'win32', cwd: 'D:\\research', launch: (...args) => {
  launchArgs = args;
  const child = new EventEmitter(); child.unref = () => {};
  queueMicrotask(() => child.emit('spawn'));
  return child;
} });
assert.deepEqual(launchArgs.slice(0, 2), ['powershell.exe', ['-NoLogo', '-NoExit']]);
assert.equal(launchArgs[2].shell, false);
assert.equal(launchArgs[2].cwd, 'D:\\research');
await openLocalTerminal({ platform: 'win32', launch: (...args) => {
  launchArgs = args;
  const child = new EventEmitter(); child.unref = () => {};
  queueMicrotask(() => child.emit('spawn'));
  return child;
} });
assert.equal(launchArgs[2].cwd, path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..'));
console.log('Native terminal launch contract passed (no terminal was opened)');
process.exit(0);
