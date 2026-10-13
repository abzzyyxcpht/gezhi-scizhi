// 首次运行检查与离线样例（Ink onboarding-ui 的 OpenTUI 移植）：远程只读检查、不自动写设置、
// 显式 /demo 才启动合成样例，且样例任务被当前工作台跟踪（透传 task_id，不新开会话）。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureOnboardingFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call, originalConnection = Bridge.prototype.connection;
  const calls = [];
  Bridge.prototype.connection = () => ({ mode: 'remote', url: 'http://fixture' });
  Bridge.prototype.call = async function (request) {
    calls.push(request);
    const fixtures = {
      workflows: { workflows: [] }, workbench_status: { processes: [], loops: [] }, list: { tasks: [] }, nodes: { nodes: [] },
      product_readiness: { readiness: { version: '0.6.0', agent_mode: 'model',
        model: { status: 'configured', model: 'fixture-model', protocol: 'responses', connection_verified: false,
          execution_ready: true, reason: '仅检查已保存配置', api_key: 'private-ready-secret' },
        research: { default_offline: false, offline_available: true, notice: '离线入口使用合成样例' },
        checks: [{ title: '基础依赖', status: 'ready', detail: '可用' }],
        suggestions: { offline_command: '/demo', settings_command: '/models', example_request: '核对科研证据' } } },
      product_demo: { task_id: 'demo-task', workflow_id: 'literature_scan', offline: true,
        notice: '内置合成样例，仅供体验；不可作为真实科研证据。' },
    };
    return { ok: true, ...(fixtures[request.cmd] || {}) };
  };
  try {
    t.resize(80, 40); // 首次运行检查全文 22 行：加高终端使阅读器无需滚动即可核对
    await t.command('/setup');
    await t.wait(() => t.frame().includes('「首次运行检查」'), 'onboarding reader');
    for (const phrase of ['0.6.0', '已配置', '未验证连接', '主机', '/demo', '/models']) assert.ok(t.frame().includes(phrase), phrase);
    t.capture('onboarding-setup', 80, 40, false);
    assert.equal(calls.filter(call => call.cmd === 'product_demo').length, 0, 'setup does not execute its demo suggestion');
    assert.ok(!calls.some(call => ['settings_save', 'agent_run', 'agent_launch'].includes(call.cmd)));
    assert.ok(!t.frame().includes('private-ready-secret'), 'readiness never renders the api key');
    await t.press('q');
    await t.wait(() => !t.frame().includes('「首次运行检查」'), 'return to workbench');
    await t.command('/demo');
    await t.wait(() => t.frame().includes('不可作为真实科研证据'), 'explicit offline demo tracked by existing workflow');
    assert.equal(calls.filter(call => call.cmd === 'product_demo').length, 1);
    assert.ok(!calls.some(call => ['settings_save', 'agent_run', 'agent_launch', 'workflow_run', 'create'].includes(call.cmd)));
    await t.command('/read digest');
    await t.wait(() => calls.some(call => call.cmd === 'command' && call.task_id === 'demo-task'),
      'demo task stays selected for research commands');
    assert.ok(!t.frame().includes('private-ready-secret'));
  } finally {
    Bridge.prototype.call = originalCall;
    Bridge.prototype.connection = originalConnection;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureOnboardingFrames();
  console.log(`${result.frames.length} onboarding real OpenTUI frames: remote readiness reader, no automatic writes and explicit offline demo tracking passed`);
}
// opentui 渲染器保活计时器：直接运行时必须显式退出（被 capture 脚本 import 时不退出）
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) process.exit(0);
