// 远程模式（Ink remote-ui 的 OpenTUI 移植）：不启动本机引擎、严格 HTTP 探测纪律、
// 远程导出内容本地安全落盘（服务器路径永不采用）、会话阅读器脱敏、令牌零泄漏。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureRemoteFrames = () => upgradeFixture(async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'gezhi-remote-ui-'));
  const oldCwd = process.cwd();
  process.chdir(directory); // 导出落盘目录隔离（saveRemoteExport 默认 cwd/outputs/remote-sessions）
  const originalCall = Bridge.prototype.call;
  const secret = 'fixture-private-key';
  const calls = [];
  Bridge.prototype.call = async function (request) {
    calls.push(request);
    const fixtures = {
      workflows: { workflows: [{ id: 'research_pipeline', title: '完整科研流水线', stages: [] }] },
      workbench_status: { processes: [], loops: [] }, list: { tasks: [] },
      session_snapshot: { snapshot: { session_id: 's1', api_key: secret, total_tokens: 12 } },
      session_export: { filename: '../../untrusted.md', content: '# Remote session\nSaved safely.', mime: 'text/markdown' },
    };
    if (!fixtures[request.cmd]) return originalCall.call(this, request);
    return { ok: true, ...fixtures[request.cmd] };
  };
  try {
    t.mode = 'remote';
    // 远程模式不得触碰本机配置探测（HTTP 请求纪律：settings/llm/plugins 只在主机发）
    await t.wait(() => t.frame().includes('· 输入'), 'remote startup');
    assert.ok(!calls.some(call => ['settings_get', 'llm_status', 'plugin_commands'].includes(call.cmd)),
      'remote mode must not probe local-only settings endpoints');
    // 远程导出：内容本地安全落盘；服务器给的路径（../../untrusted.md）永不采用
    await t.command('/session-export s1 markdown');
    await t.wait(() => t.frame().includes('会话已导出'), 'remote export');
    const exportDirectory = path.join(directory, 'outputs', 'remote-sessions');
    const files = fs.readdirSync(exportDirectory);
    assert.equal(files.length, 1);
    assert.match(files[0], /^s1-[\w-]+\.md$/);
    assert.equal(fs.readFileSync(path.join(exportDirectory, files[0]), 'utf8'), '# Remote session\nSaved safely.');
    assert.ok(!fs.existsSync(path.join(directory, 'untrusted.md')), 'server-controlled filename ignored');
    // 会话阅读器：令牌脱敏、计数可读；监控已有会话不发起新的 agent RPC
    await t.command('/session s1');
    await t.wait(() => t.frame().includes('会话 s1'), 'session reader');
    assert.ok(!t.frame().includes(secret), 'reader redacts credentials');
    assert.ok(t.frame().includes('12'), 'token counters remain readable');
    await t.press('q');
    await t.wait(() => !t.frame().includes('（↑↓ 滚动'), 'reader closed');
    assert.equal(t.calls.filter(call => ['agent_run', 'agent_launch'].includes(call.cmd)).length, 0,
      'remote monitoring uses an existing session');
    assert.ok(t.frames.every(frame => !String(frame.text).includes('fixture-private-key')), 'credentials never render in any captured frame');
  } finally {
    Bridge.prototype.call = originalCall;
    process.chdir(oldCwd);
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureRemoteFrames();
  console.log(`Remote-only startup, safe local export and redacted session reader passed (${result.frames.length} frames)`);
  process.exit(0);
}
