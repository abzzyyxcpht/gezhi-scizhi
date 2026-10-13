// TUI 本地状态持久化：主题序号 + 输入历史（GEZHI_DATA_DIR/tui_state.json）。
// 解析优先级与 Python 侧 config.DATA_DIR 保持一致：环境变量 > 仓库 data/。
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

export const HISTORY_LIMIT = 50;

/** 数据目录：GEZHI_DATA_DIR 环境变量优先，否则仓库 data/。 */
export function dataDir() {
  return process.env.GEZHI_DATA_DIR || path.join(ROOT, 'data');
}

/** 状态文件路径。 */
export function tuiStatePath(dir = dataDir()) {
  return path.join(dir, 'tui_state.json');
}

/** 类型清洗：只保留 theme（非负整数）与 history（字符串数组，截到上限）。 */
const sanitize = (raw) => {
  const state = { theme: 0, history: [] };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return state;
  if (Number.isSafeInteger(raw.theme) && raw.theme >= 0) state.theme = raw.theme;
  if (Array.isArray(raw.history)) {
    state.history = raw.history.filter(item => typeof item === 'string').slice(-HISTORY_LIMIT);
  }
  return state;
};

/** 读状态：文件缺失、JSON 损坏或结构非法时容错回默认，绝不让启动失败。 */
export function loadTuiState(dir = dataDir()) {
  try {
    return sanitize(JSON.parse(fs.readFileSync(tuiStatePath(dir), 'utf-8')));
  } catch {
    return { theme: 0, history: [] };
  }
}

/** The first frame needs only this local display preference, never model credentials. */
export function loadUiMotionPreference(dir = dataDir()) {
  try {
    const settings = JSON.parse(fs.readFileSync(path.join(dir, 'settings.json'), 'utf-8'));
    return { ui_animations: settings?.ui_animations !== false };
  } catch {
    return { ui_animations: true }; // Matches Python's existing default.
  }
}

/** 写状态：best-effort（只读目录等场景静默失败，由下一次成功写入覆盖）。 */
export function saveTuiState(state, dir = dataDir()) {
  const clean = sanitize(state);
  try {
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(tuiStatePath(dir), JSON.stringify(clean, null, 2) + '\n', 'utf-8');
  } catch { /* 持久化失败不阻断交互 */ }
  return clean;
}
