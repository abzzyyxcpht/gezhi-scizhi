import assert from 'node:assert/strict';
import * as statusView from '../src/session-status.js';
import { commandsForMode } from '../src/workspace-mode.js';
import { COMMANDS } from '../src/commands.js';
import stringWidth from 'string-width';

assert.equal(typeof statusView.contextManifestLines, 'function', 'context manifest formatter is implemented');
assert.equal(typeof statusView.executionContextIdentity, 'function', 'identity separates purpose, role and execution');
assert.equal(typeof statusView.wrapContextLines, 'function', 'context reader wraps metadata without losing it');
const longPath = '项目路径 D:/研究目录/很长的中文文件夹名称/GEZHI.local.md';
const wrapped = statusView.wrapContextLines([longPath], 20);
assert.ok(wrapped.every(line => stringWidth(line) <= 20));
assert.equal(wrapped.join(''), longPath, 'narrow reader preserves all source metadata across lines');
const manifest = {
  schema_version: 1, status: 'captured', basis: 'frozen_prefix', captured_at: '2026-10-06T10:20:30Z',
  workspace_mode: 'general', project_id: 'project-a', agent: 'explore', stage: 'planning', prompt_version: '2.1',
  budget: { unit: 'characters', limit_chars: 6000, used_chars: 2180 },
  sources: [
    { kind: 'instructions', name: 'GEZHI.md', scope: 'project', path: 'D:/project-a/GEZHI.md',
      status: 'included', used_chars: 1200, total_chars: 1200 },
    { kind: 'skill_index', name: 'evidence-audit', scope: 'stage', path: 'skills/evidence-audit/SKILL.md',
      status: 'budget_deferred', used_chars: 0, total_chars: 4100, reason: '字符预算不足' },
    { kind: 'skill_body', name: 'search-plan', scope: 'session', status: 'partial', used_chars: 6000, total_chars: 8000 },
    { kind: 'memory', name: 'approved-memory', scope: 'project', status: 'included', used_chars: 980, total_chars: 980,
      body: 'PRIVATE MEMORY BODY MUST NOT RENDER' },
  ], notices: ['只显示来源元数据'], system_prompt: 'PRIVATE SYSTEM MUST NOT RENDER',
};
const text = statusView.contextManifestLines(manifest).join('\n');
for (const fragment of ['已冻结', '2026-10-06', '字符 2,180/6,000', 'GEZHI.md', '已纳入',
  'evidence-audit', '因预算延期', '部分正文已读取', '只显示来源元数据']) assert.ok(text.includes(fragment), fragment);
assert.ok(!text.includes('PRIVATE'));
assert.ok(!text.includes('已采用'), 'reading does not prove the model followed skill content');
assert.deepEqual(statusView.contextManifestLines(null), ['执行上下文来源：未记录（旧会话或规则流程）']);
assert.ok(statusView.contextManifestLines({ schema_version: 999, ...manifest, schema_version: 999 })[0].includes('未记录'));
assert.ok(statusView.executionContextIdentity({ workspace_mode: 'general', agent: 'explore', mode: 'review' }).includes('用途 通用'));
assert.ok(statusView.executionContextIdentity({ workspace_mode: 'general', agent: 'explore', mode: 'review' }).includes('角色 explore'));
assert.ok(statusView.executionContextIdentity({ workspace_mode: 'general', agent: 'explore', mode: 'review' }).includes('执行 证据审查'));
assert.equal(statusView.executionContextIdentity({}), '用途 未记录 · 角色 未记录 · 执行 未记录');
assert.ok(statusView.sessionStatusLines({ session_id: 'saved', context_manifest: manifest }).join('\n').includes('GEZHI.md'));
const long = { ...manifest, sources: Array.from({ length: 40 }, (_, i) => ({ ...manifest.sources[0], name: `rule-${i}` })) };
const bounded = statusView.contextManifestLines(long).join('\n');
assert.ok(bounded.includes('另有 16 个来源未显示'));
assert.ok(!bounded.includes('rule-24'));
assert.ok(statusView.contextManifestLines({ ...long, omitted_sources: 5 }).join('\n').includes('另有 21 个来源未显示'));
assert.ok(statusView.contextManifestLines({ ...manifest, sources: [
  { ...manifest.sources[0], scope: 'application' }, { ...manifest.sources[0], scope: 'working_directory', status: 'manual_only' },
] }).join('\n').includes('程序目录'));
const currentDirectorySource = statusView.contextManifestLines({ ...manifest, sources: [
  { ...manifest.sources[0], scope: 'working_directory', status: 'manual_only' },
] }).join('\n');
assert.ok(currentDirectorySource.includes('工作目录') && currentDirectorySource.includes('禁止模型调用（手动入口待支持）'));
assert.equal(typeof statusView.sessionContextLines, 'function', 'session combines main and internal research provenance');
const requestEntry = (index, outcome = 'returned') => ({ request_id: `inner-${index}`, node: 'extract',
  prompt_key: 'evidence_extract', outcome, observed_at: '2026-10-06T16:20:30+08:00',
  context_manifest: manifest, raw_response: 'PRIVATE MODEL RESPONSE' });
const internalStatus = { driver: 'planned', context_manifest: null, research_contexts: {
  schema_version: 1, entries: [requestEntry(1), requestEntry(2, 'error')], omitted_entries: 0,
} };
const internal = statusView.sessionContextLines(internalStatus).join('\n');
for (const fragment of ['主会话前缀', '未记录（旧会话或规则流程）', '近期工具内部模型请求', 'inner-1',
  'extract', 'evidence_extract', '请求已返回', '请求失败', '2026-10-06T16:20:30', 'GEZHI.md']) assert.ok(internal.includes(fragment), fragment);
assert.ok(!internal.includes('PRIVATE') && !internal.includes('已通过验证'));
assert.ok(statusView.sessionStatusLines(internalStatus).join('\n').includes('近期工具内部模型请求'));
for (const research of [null, { schema_version: 1, entries: [] }, { schema_version: 9, entries: [requestEntry(1)] }]) {
  assert.deepEqual(statusView.sessionContextLines({ context_manifest: manifest, research_contexts: research }), statusView.contextManifestLines(manifest));
}
const failedRead = statusView.sessionContextLines({ research_contexts: { schema_version: 1, entries: [],
  issue: '工具内部模型请求记录暂不可读，请稍后重试。 api_key=fixture-secret' } }).join('\n');
assert.ok(failedRead.includes('暂不可读，请稍后重试') && !failedRead.includes('fixture-secret'));
assert.ok(!failedRead.includes('没有调用') && !failedRead.includes('请求已返回'));
const recent = statusView.sessionContextLines({ research_contexts: { schema_version: 1,
  entries: Array.from({ length: 7 }, (_, index) => requestEntry(index)), omitted_entries: 5 } }).join('\n');
assert.ok(recent.includes('另有 8 条工具内部模型请求未显示'));
assert.ok(!recent.includes('inner-0') && !recent.includes('inner-2') && recent.indexOf('inner-3') < recent.indexOf('inner-6'));
const command = context => commandsForMode(COMMANDS, 'general', context).find(row => row.name === 'context');
assert.equal(command({ remote: true }).available, false, 'remote discovery remains host-only');
assert.equal(command({ remote: true, sessionId: 'saved' }).available, false, 'remote context contract is unchanged this iteration');
console.log('Context manifest frozen identity, safe fields, source states, legacy and bounds passed');
