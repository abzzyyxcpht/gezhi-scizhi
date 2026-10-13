import assert from 'node:assert/strict';
import { sessionStatusLines } from '../src/session-status.js';

const text = sessionStatusLines({ session_id: 'ctx', context: { measurement: 'heuristic_tokens', tokens: 7200,
  schema_tokens: 800, input_tokens: 8000, output_reserve_tokens: 2048, pressure_tokens: 10048, limit_tokens: 12000 } }).join('\n');
for (const fragment of ['工具schema 800', '总输入 8000', '输出预留 2048', '上下文压力 10048/12000']) assert.ok(text.includes(fragment), fragment);
assert.ok(text.includes('估算'), 'heuristic pressure is not provider token accounting');
console.log('Context pressure components distinguish messages, schema, input and reserve');
