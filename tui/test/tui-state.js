// tui_state 持久化：写读回环、历史上限、文件损坏与类型非法的容错。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { loadTuiState, saveTuiState, tuiStatePath, HISTORY_LIMIT } from '../src/state.js';

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gezhi-tui-state-'));
process.once('exit', () => { try { fs.rmSync(dir, { recursive: true, force: true }); } catch {} });

// 缺失文件 → 默认状态，且启动不受影响
assert.deepEqual(loadTuiState(dir), { theme: 0, history: [] });
// 写读回环
saveTuiState({ theme: 2, history: ['/help', '/board'] }, dir);
assert.equal(fs.existsSync(tuiStatePath(dir)), true);
assert.deepEqual(loadTuiState(dir), { theme: 2, history: ['/help', '/board'] });
// 历史上限：超出截到最近 50 条
saveTuiState({ theme: 0, history: Array.from({ length: 60 }, (_, i) => `cmd-${i}`) }, dir);
const capped = loadTuiState(dir);
assert.equal(capped.history.length, HISTORY_LIMIT);
assert.equal(capped.history.at(-1), 'cmd-59');
assert.equal(capped.history[0], 'cmd-10');
// 损坏 JSON → 容错回默认
fs.writeFileSync(tuiStatePath(dir), '{broken', 'utf-8');
assert.deepEqual(loadTuiState(dir), { theme: 0, history: [] });
// 结构非法（theme/history 类型不符、顶层为数组）→ 清洗
fs.writeFileSync(tuiStatePath(dir), JSON.stringify({ theme: 'x', history: 'nope', extra: 1 }), 'utf-8');
assert.deepEqual(loadTuiState(dir), { theme: 0, history: [] });
fs.writeFileSync(tuiStatePath(dir), JSON.stringify(['not', 'an', 'object']), 'utf-8');
assert.deepEqual(loadTuiState(dir), { theme: 0, history: [] });
// history 混入非字符串 → 过滤保留
fs.writeFileSync(tuiStatePath(dir), JSON.stringify({ theme: 3, history: ['ok', 7, null, '/theme'] }), 'utf-8');
assert.deepEqual(loadTuiState(dir), { theme: 3, history: ['ok', '/theme'] });
// 负号 theme → 回默认
fs.writeFileSync(tuiStatePath(dir), JSON.stringify({ theme: -1, history: [] }), 'utf-8');
assert.deepEqual(loadTuiState(dir), { theme: 0, history: [] });
console.log('tui_state persistence roundtrip, history cap and corruption tolerance passed');
