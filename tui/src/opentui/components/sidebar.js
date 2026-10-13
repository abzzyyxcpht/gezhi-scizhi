// 侧栏 —— 响应式三态（docked / overlay / hidden），数据驱动
import React from "react"
import { TOKENS, MODE_TOKENS, modeColor } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"
import { sidebarLayoutForTerminal, SIDEBAR_WIDTH } from "../layout/width-budget.js"

const h = React.createElement

/**
 * @param terminalWidth 终端宽（useTerminalDimensions 注入）
 * @param hidden 用户手动隐藏
 * @param sections [{title, lines:[{text, fg?}], collapsed?}]
 */
export function Sidebar({ terminalWidth = 120, hidden = false, workspaceMode = "general", sections = [] }) {
  const layout = sidebarLayoutForTerminal(terminalWidth, { userHidden: hidden })
  if (layout === "hidden") return null
  const color = modeColor(TOKENS, workspaceMode)
  return h(
    "box",
    {
      width: SIDEBAR_WIDTH - 2,
      flexDirection: "column",
      padding: 1,
      backgroundColor: layout === "overlay" ? TOKENS.panel : TOKENS.background,
      border: true,
      borderStyle: "single",
      borderColor: layout === "overlay" ? TOKENS.borderWarning : TOKENS.border,
    },
    h("text", { content: `${SYMBOLS.modeGeneral}${SYMBOLS.modeResearch} ${MODE_TOKENS[workspaceMode]?.label ?? ""} 模式`, fg: color, bold: true }),
    ...sections.flatMap((sec, si) => [
      h("text", { key: `t${si}`, content: ` ${sec.title}`, fg: TOKENS.textMuted, bold: true }),
      ...(sec.collapsed
        ? []
        : (sec.lines || []).map((l, li) => h("text", { key: `l${si}_${li}`, content: `  ${l.text}`, fg: l.fg || TOKENS.text }))),
    ])
  )
}
