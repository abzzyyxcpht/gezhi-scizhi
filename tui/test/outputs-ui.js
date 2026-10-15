// 工具输出阅读：字节偏移 list/search/read，读取不重放工具。
// 夹具驱动真 runtime + 全桩 Bridge；提交纯文本即 agent 请求（OpenTUI 无 /agent 本地分支）。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureOutputFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const calls = [];
  const id = 'out_' + 'a'.repeat(32);
  Bridge.prototype.call = async function (request) {
    calls.push(request);
    const fixtures = {
      settings_get: { settings: { ui_animations: false } }, nodes: { nodes: [] }, list: { tasks: [] },
      plugin_commands: { commands: [] }, llm_status: { configured: false }, plugins_summary: { plugins: { count: 0, names: [] } },
      skills_list: { skills: [] }, context_status: { context: { found: false } }, usage: { usage: { model: {} } },
      agent_launch: { result: { session_id: 'output-session', status: 'done', content: 'fixture ready' } },
      session_snapshot: { snapshot: { session_id: 'output-session', status: 'done' } },
      session_tool_outputs: { output: { session_id: 'output-session', items: [{ output_id: id, tool: 'file.read', total_bytes: 50000 }], total: 1, next_offset: null } },
      session_tool_output_read: { output: { session_id: 'output-session', output_id: id, text: '尾部证据：尚需独立核查。', offset: 48000, next_offset: null, eof: true, total_bytes: 50000 } },
      session_tool_output_search: { output: { session_id: 'output-session', output_id: id, matches: [{ offset: 48000, preview: '尾部证据：尚需独立核查。' }], next_offset: null, eof: true } },
    };
    return { ok: true, ...(fixtures[request.cmd] || {}) };
  };
  try {
    await t.command('fixture');
    await t.wait(() => t.frame().includes('output-session'), 'selected session');
    await t.command('/outputs');
    await t.wait(() => t.frame().includes('工具输出') && t.frame().includes('out_'), 'output list');
    t.capture('outputs-list', 80, 24);
    await t.press('q');
    await t.command(`/outputs search ${id} 0 尾部证据`);
    await t.wait(() => t.frame().includes('48000') && t.frame().includes('尾部证据'), 'search offset');
    await t.press('q');
    await t.command(`/outputs read ${id} 48000`);
    await t.wait(() => t.frame().includes('已读至结尾') && t.frame().includes('尚需独立核查'), 'read tail');
    t.capture('outputs-read', 80, 24, false);
    assert.deepEqual(calls.filter(call => call.cmd.startsWith('session_tool_')).map(call => call.session_id),
      ['output-session', 'output-session', 'output-session']);
    assert.equal(calls.filter(call => call.cmd === 'agent_launch').length, 1);
    assert.ok(!calls.some(call => ['agent_run', 'workflow_run', 'create'].includes(call.cmd)));
    await t.press('q');
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureOutputFrames();
  console.log(`${result.frames.length} tool output real OpenTUI frames: byte-offset list/search and tail reader passed without replaying tools`);
}
// opentui 渲染器保活计时器：直接运行时必须显式退出（被 capture 脚本 import 时不退出）
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) process.exit(0);
