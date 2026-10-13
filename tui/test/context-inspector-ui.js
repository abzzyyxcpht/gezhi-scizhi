import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureContextInspectorFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const calls = [], pending = [];
  let hold = false, recorded = true;
  const manifest = {
    schema_version: 1, status: 'captured', basis: 'frozen_prefix', captured_at: '2026-10-06T10:20:30Z',
    workspace_mode: 'general', project_id: 'project-a', agent: 'explore', stage: 'planning', prompt_version: '2.1',
    budget: { unit: 'characters', limit_chars: 6000, used_chars: 2180 },
    sources: [{ kind: 'instructions', name: 'GEZHI.md', scope: 'project', path: 'D:/project-a/GEZHI.md',
      status: 'included', used_chars: 1200, total_chars: 1200, body: 'PRIVATE RULE BODY' }], notices: [],
  };
  const status = () => ({ session_id: 'saved', status: 'done', workspace_mode: 'general', agent: 'explore', mode: 'review',
    model: { model: 'recorded-model' }, context_manifest: recorded ? manifest : null });
  Bridge.prototype.call = async function (request) {
    if (['context_status', 'session_snapshot', 'session_status', 'skills_list'].includes(request.cmd)) {
      calls.push(request);
      const answer = () => request.cmd === 'context_status'
        ? { ok: true, context: { found: true, path: 'D:/current-settings/GEZHI.md', bytes: 128, sections: ['偏好'] } }
        : request.cmd === 'skills_list'
          ? { ok: true, skills: [{ name: 'audit', description: '核验证据', active: true }] }
          : request.cmd === 'session_snapshot' ? { ok: true, snapshot: status() } : { ok: true, status: status() };
      if (hold && request.cmd === 'session_status') return new Promise(resolve => pending.push(() => resolve(answer())));
      return answer();
    }
    return originalCall.call(this, request);
  };
  try {
    await t.command('/context');
    await t.wait(() => t.frame().includes('发现') && t.frame().includes('current-settings'), 'no-session context only describes discovery');
    assert.ok(!t.frame().includes('已加载'), 'file existence is not a recorded prompt inclusion');
    t.capture('context-discovery', 80, 24);
    await t.command('/skills');
    await t.wait(() => t.frame().includes('正文按需读取'), 'skill help explains progressive loading');
    assert.ok(!t.frame().includes('启用后正文注入'));
    await t.command('/session saved');
    await t.wait(() => t.frame().includes('「会话 saved」'), 'saved session selected');
    await t.press('q');
    const discoveryCalls = calls.filter(call => call.cmd === 'context_status').length;
    await t.command('/context');
    await t.wait(() => t.frame().includes('「执行上下文来源」') && t.frame().includes('已冻结'), 'context opens recorded manifest');
    assert.ok(t.frame().includes('用途 通用') && t.frame().includes('角色 explore') && t.frame().includes('执行 证据审查'));
    assert.equal(calls.filter(call => call.cmd === 'context_status').length, discoveryCalls, 'selected context never substitutes current files');
    assert.ok(!t.frame().includes('PRIVATE RULE BODY'));
    t.capture('context-captured', 80, 24, false);
    t.resize(54, 18); await t.press('');
    t.capture('context-narrow', 54, 18, false);
    await t.press('q');
    recorded = false;
    await t.command('/context');
    await t.wait(() => t.frame().includes('未记录（旧会话或规则流程'), 'legacy manifest stays unknown');
    assert.ok(!t.frame().includes('current-settings'));
    t.capture('context-legacy', 54, 18, false);
    await t.press('q');
    recorded = true; hold = true;
    await t.command('/context');
    await t.wait(() => pending.length > 0, 'context request held');
    await t.press('\u0010');
    await t.wait(() => t.frame().includes('命令面板'), 'palette owns view');
    pending.shift()(); hold = false;
    await t.press('');
    assert.ok(t.frame().includes('命令面板') && !t.frame().includes('「执行上下文来源」'), 'late inspector cannot replace palette');
    assert.ok(!t.calls.some(call => ['agent_run', 'agent_launch', 'agent_approve', 'workflow_run'].includes(call.cmd)));
  } finally {
    pending.forEach(resolve => resolve());
    Bridge.prototype.call = originalCall;
  }
});

const verifyResearchContextReader = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  const calls = [];
  const manifest = { schema_version: 1, status: 'captured', basis: 'frozen_prefix',
    project_id: 'inner-project', agent: 'research', stage: 'extraction',
    sources: [{ kind: 'instructions', name: 'INNER-GEZHI.md', scope: 'project', status: 'included',
      path: 'D:/inner-project/GEZHI.md', body: 'PRIVATE SOURCE BODY' }], system_prompt: 'PRIVATE SYSTEM PROMPT' };
  let research = { schema_version: 1, entries: ['returned', 'error'].map((outcome, index) => ({
    request_id: `inner-${index}`, node: 'extract', prompt_key: 'evidence_extract', outcome,
    observed_at: '2026-10-06T16:20:30+08:00', context_manifest: manifest, raw_response: 'PRIVATE MODEL RESPONSE',
  })), omitted_entries: 2 };
  const identity = { session_id: 'planned-inner', status: 'done', driver: 'planned',
    workspace_mode: 'research', agent: 'research', mode: 'agent', context_manifest: null };
  Bridge.prototype.call = async function (request) {
    calls.push(request);
    if (request.cmd === 'session_snapshot') return { ok: true, snapshot: identity };
    if (request.cmd === 'session_status') return { ok: true, status: { ...identity, research_contexts: research } };
    return originalCall.call(this, request);
  };
  try {
    await t.command('/session planned-inner');
    await t.wait(() => t.frame().includes('「会话 planned-inner」'), 'planned session selected');
    await t.press('q');
    const discoveryCount = calls.filter(call => call.cmd === 'context_status').length;
    await t.command('/context');
    await t.wait(() => t.frame().includes('主会话前缀') && t.frame().includes('工具内部模型请求'), 'planned session exposes inner requests without a main manifest');
    assert.ok(t.frame().includes('未记录（旧会话或规则流程）'));
    let seen = t.frame();
    for (let page = 0; page < 4; page += 1) {
      await t.press('\u001b[6~');
      seen += '\n' + t.frame();
    }
    for (const fragment of ['请求已返回', '请求失败', 'INNER-GEZHI.md', 'inner-project', '另有 2 条工具内部模型请求未显示']) {
      assert.ok(seen.includes(fragment), fragment);
    }
    assert.ok(!seen.includes('PRIVATE') && !seen.includes('已通过验证'));
    assert.equal(calls.filter(call => call.cmd === 'context_status').length, discoveryCount);
    await t.press('q');
    research = { schema_version: 1, entries: [], issue: '工具内部模型请求记录暂不可读，请稍后重试。' };
    await t.command('/context');
    await t.wait(() => t.frame().includes('暂不可读，请稍后重试'), 'failed provenance read is shown without claiming no calls');
    assert.ok(!t.frame().includes('请求已返回') && !t.frame().includes('没有调用'));
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureContextInspectorFrames();
  await verifyResearchContextReader();
  console.log(`${result.frames.length} context inspector real OpenTUI frames and planned internal-request provenance passed`);
}

// opentui 渲染器保活计时器：直接运行时必须显式退出（被 capture 脚本 import 时不退出）
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) process.exit(0);
