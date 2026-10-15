// 侧栏 —— 响应式三态（docked / overlay / hidden），数据驱动
import React from "react"
import { TOKENS, MODE_TOKENS, modeColor } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"
import { sidebarLayoutForTerminal, SIDEBAR_WIDTH } from "../layout/width-budget.js"
import { typeStyle } from "../theme/ui-type.js"
import { terminalGeometry } from "../theme/preferences.js"

const h = React.createElement

/**
 * @param terminalWidth 终端宽（useTerminalDimensions 注入）
 * @param hidden 用户手动隐藏
 * @param sections [{title, lines:[{text, fg?}], collapsed?}]
 */
export function Sidebar({ terminalWidth = 120, terminalHeight = 24, hidden = false, workspaceMode = "general", sections = [], layout: requestedLayout, density, scroll = 0 }) {
  const layout = requestedLayout || sidebarLayoutForTerminal(terminalWidth, { userHidden: hidden })
  if (layout === "hidden") return null
  const color = modeColor(TOKENS, workspaceMode)
  const heading = typeStyle("ui_lg")
  const caption = typeStyle("ui_caption")
  const rows = sections.flatMap((sec) => [
    { text: ` ${sec.title}`, heading: true },
    ...(sec.collapsed ? [] : (sec.lines || []).map((line) => ({ ...line, text: `  ${line.text}` }))),
  ])
  const visibleRows = Math.max(1, terminalHeight - 6)
  const start = Math.min(Math.max(0, scroll), Math.max(0, rows.length - visibleRows))
  const visible = layout === "overlay" ? rows.slice(start, start + visibleRows) : rows
  return h(
    "box",
    {
      width: Math.min(SIDEBAR_WIDTH - 2, terminalWidth - 2),
      flexDirection: "column",
      padding: terminalGeometry(density).sidebarPadding,
      height: layout === "overlay" ? "100%" : undefined,
      backgroundColor: layout === "overlay" ? TOKENS.panel : TOKENS.background,
      border: true,
      borderStyle: "single",
      borderColor: layout === "overlay" ? TOKENS.borderFocus : TOKENS.border,
    },
    h("text", { content: `${SYMBOLS.modeGeneral}${SYMBOLS.modeResearch} ${MODE_TOKENS[workspaceMode]?.label ?? ""} 模式`, fg: color, bold: true }),
    ...visible.map((row, i) => h("text", { key: start + i, content: row.text, fg: row.fg || TOKENS[row.heading ? heading.tier : "text"], bold: row.heading && heading.strong })),
    layout === "overlay" ? h("text", { content: " ↑↓ 滚动 · Esc / Ctrl+B 关闭", fg: TOKENS[caption.tier] }) : null
  )
}
