// CLI 参数解析（--continue / -p / --print / --version）与 headless 单轮问答。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseArgv, runHeadlessPrint, printVersion, USAGE } from '../src/headless.js';
import { VERSION } from '../src/commands.js';

// ---- parseArgv ----
assert.deepEqual(parseArgv([]), { mode: 'interactive', query: '' }, '无参数保持现状（交互界面）');
assert.equal(parseArgv(['--continue']).mode, 'continue');
assert.equal(parseArgv(['-c']).mode, 'continue');
assert.equal(parseArgv(['--version']).mode, 'version');
assert.equal(parseArgv(['-v']).mode, 'version');
assert.equal(parseArgv(['--help']).mode, 'help');
assert.equal(parseArgv(['-h']).mode, 'help');
assert.deepEqual(parseArgv(['-p', '调研 量子计算']), { mode: 'print', query: '调研 量子计算' });
assert.deepEqual(parseArgv(['--print', '写一段综述']), { mode: 'print', query: '写一段综述' });
assert.deepEqual(parseArgv(['--print=分析数据']), { mode: 'print', query: '分析数据' });
assert.deepEqual(parseArgv(['-p分析数据']), { mode: 'print', query: '分析数据' });
assert.equal(parseArgv(['-p']).mode, 'error', '-p 缺参数 → 错误用法');
assert.equal(parseArgv(['-p', '   ']).mode, 'error', '空白查询 → 错误用法');
assert.equal(parseArgv(['--unknown']).mode, 'error', '未知参数不能误启动交互界面');
assert.equal(parseArgv(['unexpected']).mode, 'error', '未声明的位置参数应给用法提示');
assert.equal(parseArgv(['--continue', '--print', 'query']).mode, 'error', '冲突模式不能悄悄改变执行方式');
assert.equal(parseArgv(['--print', '--continue']).mode, 'error', '缺少请求时不能把CLI选项当科研任务');
assert.equal(parseArgv(['--help', '--continue']).mode, 'help', '帮助入口优先且无执行副作用');

// ---- runHeadlessPrint（mock 桥，不启动 Python / ink） ----
const mkBridge = (response) => ({
  calls: [],
  async whenReady() { return true; },
  async call(request) { this.calls.push(request); return response; },
  close() { this.closed = true; },
});
let stdout = [], stderr = [];
const writers = () => ({ stdout: t => stdout.push(t), stderr: t => stderr.push(t) });

let bridge = mkBridge({ ok: true, result: { status: 'done', answer: '最终答复文本' } });
let code = await runHeadlessPrint('量子计算', { bridge, ...writers() });
assert.equal(code, 0);
assert.deepEqual(bridge.calls.at(-1), { cmd: 'agent_run', request: '量子计算', mode: 'agent' }, '默认单轮请求由服务端保存的工作模式决定智能体');
assert.deepEqual(stdout, ['最终答复文本'], 'stdout 只含最终 assistant 文本');
assert.equal(bridge.closed, true, '结束后关桥');

stdout = []; stderr = [];
code = await runHeadlessPrint('x', { bridge: mkBridge({ ok: false, error: '引擎错误' }), ...writers() });
assert.equal(code, 1);
assert.ok(stderr[0].includes('引擎错误'));

stdout = []; stderr = [];
code = await runHeadlessPrint('x', { bridge: mkBridge({ ok: true, result: { status: 'error', error: 'boom' } }), ...writers() });
assert.equal(code, 1);
assert.ok(stderr[0].includes('boom'));

stdout = []; stderr = [];
code = await runHeadlessPrint('x', { bridge: mkBridge({ ok: true, result: { status: 'cancelled' } }), ...writers() });
assert.equal(code, 1, 'cancelled 视为失败');

stdout = []; stderr = [];
code = await runHeadlessPrint('x', { bridge: mkBridge({ ok: true, result: { status: 'waiting_approval', pending: { id: 'c1', tool: 'shell.run' } } }), ...writers() });
assert.equal(code, 1, '停在工具审批 → 退出码 1，提示回交互模式处理');
assert.ok(stderr[0].includes('shell.run'));

stdout = []; stderr = [];
code = await runHeadlessPrint('x', { bridge: mkBridge(null), ...writers() });
assert.equal(code, 1, '桥异常 → 退出码 1');

// answer 兼容 message/content 字段
stdout = [];
code = await runHeadlessPrint('x', { bridge: mkBridge({ ok: true, result: { status: 'done', message: '备选答复' } }), ...writers() });
assert.equal(code, 0);
assert.deepEqual(stdout, ['备选答复']);

// ---- printVersion ----
let version = '';
printVersion((text) => { version = text; });
const packageVersion = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;
assert.equal(version, `gezhi ${packageVersion}`);
assert.equal(VERSION, packageVersion, 'interactive and lightweight entry paths share the release version');
assert.ok(USAGE.includes('--continue') && USAGE.includes('--print'));
console.log('argv parsing and headless single-turn (exit codes, stdout purity) passed');
