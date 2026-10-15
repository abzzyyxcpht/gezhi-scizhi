// Slash 补全面板（composer 内联）—— 数据逻辑使用 commandsForMode/COMMANDS
import { COMMANDS } from "../../commands.js"
import { commandsForMode } from "../../workspace-mode.js"

/** 有序子序列模糊匹配（发现用模糊，执行用精确名） */
export function fuzzyMatch(query, name) {
  if (!query) return true
  const q = query.toLowerCase()
  const n = name.toLowerCase()
  let qi = 0
  for (let i = 0; i < n.length && qi < q.length; i++) if (n[i] === q[qi]) qi++
  return qi === q.length
}

/**
 * 补全候选：前缀优先 + 子序列兜底，按模式 relevance 排序
 * @returns [{name, desc, args, scope}]
 */
export function completionCandidates(draft, mode = "research", context = {}) {
  const typed = draft.startsWith("/") ? draft.slice(1).split(/\s+/)[0] : ""
  const scoped = commandsForMode(COMMANDS, mode, context)
  const prefix = scoped.filter((c) => c.name.startsWith(typed))
  const fuzzy = scoped.filter((c) => !c.name.startsWith(typed) && fuzzyMatch(typed, c.name))
  return [...prefix, ...fuzzy].slice(0, 8)
}

/** 可视窗口滑窗（选中项始终可见，窄屏 6 条） */
export function completionWindow(candidates, index, visible = 6) {
  if (candidates.length <= visible) return { start: 0, rows: candidates }
  const start = Math.max(0, Math.min(index - Math.floor(visible / 2), candidates.length - visible))
  return { start, rows: candidates.slice(start, start + visible) }
}
