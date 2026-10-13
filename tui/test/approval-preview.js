// 审批内容预览：工具名 + 关键参数摘要（file 类给 path，shell.run 给 command 原文，mcp 给 server/tool）。
import assert from 'node:assert/strict';
import { approvalSummaryLine, presentAgentResult } from '../src/research-commands.js';

// ---- approvalSummaryLine ----
assert.equal(approvalSummaryLine({ tool: 'shell.run', arguments: { command: 'python eval.py' } }),
  'shell.run — python eval.py', 'shell.run 给 command 原文');
assert.equal(approvalSummaryLine({ tool: 'file.read', arguments: { path: 'data/experiment.csv' } }),
  'file.read — data/experiment.csv', 'file 类给 path');
assert.equal(approvalSummaryLine({ tool: 'file.write', arguments: { path: 'notes.md', content: 'x' } }),
  'file.write — notes.md');
assert.equal(approvalSummaryLine({ tool: 'mcp.fs_search', arguments: { server: 'fs', tool: 'search', query: 'q' } }),
  'mcp.fs_search — fs / search', 'mcp 给 server/tool');
assert.equal(approvalSummaryLine({ tool: 'search', arguments: {} }), 'search', '无参数只给工具名');
assert.equal(approvalSummaryLine({ tool: 'unknown_tool', arguments: { topic: '大模型评测' } }),
  'unknown_tool — 大模型评测', '兜底取第一个字符串参数');
assert.equal(approvalSummaryLine({ tool: 'shell.run', arguments: {} }), 'shell.run', '缺 command 不硬拼');
assert.equal(approvalSummaryLine(null), '', '空 pending 不炸');

// ---- presentAgentResult：摘要位于审批提示下方、/approve 提示上方 ----
const output = [];
presentAgentResult({
  session_id: 's1', status: 'waiting_approval',
  pending: { id: 'call-9', tool: 'shell.run', capability: 'write', arguments: { command: 'python benchmark.py' } },
}, { out: (text) => output.push(text) });
const idxPending = output.findIndex(l => l.includes('待审批'));
const idxSummary = output.findIndex(l => l.includes('shell.run — python benchmark.py'));
const idxApprove = output.findIndex(l => l.includes('/approve s1 call-9'));
const idxDeny = output.findIndex(l => l.includes('/deny s1 call-9'));
assert.ok(idxPending >= 0 && idxSummary > idxPending, '参数摘要紧跟待审批提示');
assert.ok(idxApprove > idxSummary && idxDeny > idxApprove, '之后再提示 /approve /deny');
const tokenOutput = [];
presentAgentResult({ context: { measurement: 'heuristic_tokens', tokens: 1200, limit_tokens: 8000, compressions: 2 } },
  { out: text => tokenOutput.push(text) });
assert.ok(tokenOutput.some(line => line.includes('1200/8000') && line.includes('估算 token')), 'heuristic context is labeled as estimated tokens');
assert.ok(!tokenOutput.some(line => line.includes('字节')), 'token measurements are not rendered as bytes');
const legacyOutput = [];
presentAgentResult({ context: { bytes: 1200, limit_bytes: 8000, compressions: 1 } }, { out: text => legacyOutput.push(text) });
assert.ok(legacyOutput.some(line => line.includes('1200/8000 字节')), 'legacy byte measurements stay explicit');
console.log('approval preview summary (shell/file/mcp/fallback) and prompt ordering passed');
