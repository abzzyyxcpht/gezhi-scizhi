// 宽度预算 —— 全应用唯一宽度推导入口（吸取 ZCode 宽度常量散落 10+ 文件的教训）
// 纯函数，可无终端单测
import stringWidth from 'string-width'

export const FALLBACK_TERMINAL_WIDTH = 80
export const MIN_CONTENT_WIDTH = 8
export const SIDEBAR_WIDTH = 42
export const WIDE_BREAKPOINT = 120

/** 内容宽 = 终端宽 - 侧栏 - padding - 边框；有兜底与下限 */
export function contentWidthForTerminal(terminalWidth, { sidebarVisible = false, padding = 2, chrome = 2 } = {}) {
  const tw = Number.isFinite(terminalWidth) && terminalWidth > 0 ? terminalWidth : FALLBACK_TERMINAL_WIDTH
  const sidebar = sidebarVisible ? SIDEBAR_WIDTH : 0
  return Math.max(MIN_CONTENT_WIDTH, tw - sidebar - padding - chrome)
}

/** 侧栏三态布局（对齐 ZCode sidebarLayoutForTerminal） */
export function sidebarLayoutForTerminal(terminalWidth, { userHidden = false, mode = "docked" } = {}) {
  if (userHidden || mode === "hidden") return "hidden"
  if (mode === "overlay") return "overlay"
  if (terminalWidth >= WIDE_BREAKPOINT) return "docked"
  return "overlay"
}

/** 工具卡 header 截断策略（对齐 kimi-code keep:'head'）：窄终端先裁参数、保住尾部 chip */
export function truncateHeader({ label, detail, chip }, width) {
  const ellipsis = "…"
  const fixed = label.length + (chip ? chip.length + 1 : 0) + 2
  const budget = Math.max(0, width - fixed)
  const clipped = [...detail].length > budget ? [...detail].slice(0, Math.max(0, budget - 1)).join("") + ellipsis : detail
  return { label, detail: clipped, chip }
}

/** 按显示宽度截断（CJK 记 2 列），超宽以 … 收尾；供各面板在窄终端防溢出 */
export function clipToWidth(text, maxWidth) {
  const value = String(text ?? "")
  if (!Number.isFinite(maxWidth) || maxWidth <= 0) return ""
  if (stringWidth(value) <= maxWidth) return value
  const ellipsis = "…"
  let out = ""
  let used = 0
  for (const ch of value) {
    const w = stringWidth(ch)
    if (used + w > maxWidth - stringWidth(ellipsis)) break
    out += ch
    used += w
  }
  return out + ellipsis
}
