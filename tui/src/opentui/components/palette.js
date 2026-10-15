// 命令面板（Ctrl+P）—— 数据逻辑使用 paletteCommands（中文搜索 + relevance）
// 鼠标：行 hover 与键盘共享单一 index（last-input-wins），mousedown 即选中
import React from "react"
import stringWidth from "string-width"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS, HINTS } from "../theme/symbols.js"
import { paletteCommands } from "../../command-palette.js"
import { clipToWidth } from "../layout/width-budget.js"
import { chromeRows } from "./transcript.js"

const h = React.createElement

/** Header, footer and both borders occupy four rows; keep one transcript row. */
export function palettePanelGeometry(terminalHeight = 24, count = 0, draftRows = 1) {
  const maxVisibleRows = Math.max(1, Math.min(8, terminalHeight - chromeRows(4, draftRows) - 1))
  const visibleRows = Math.min(count, maxVisibleRows)
  return { maxVisibleRows, visibleRows, panelRows: count ? visibleRows + 4 : 1 }
}

export function PalettePanel({
  query = "",
  index = 0,
  commands = [],
  plugins = [],
  mode = "research",
  context = {},
  width = 74,
  maxVisibleRows = 8,
  onHover,
  onSelect,
}) {
  const rows = paletteCommands(commands, plugins, query, mode, context)
  if (!rows.length) return h("text", { content: "（无匹配命令）", fg: TOKENS.textMuted })
  const boxWidth = Math.min(width, 74)
  const inner = Math.max(8, boxWidth - 2)
  const windowSize = Math.max(1, maxVisibleRows)
  const start = Math.max(0, Math.min(index - windowSize + 1, rows.length - windowSize))
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: boxWidth },
    h("text", { content: clipToWidth(` 命令面板 · 搜索：${query || "（输入中文/英文过滤）"}`, inner), fg: TOKENS.accent }),
    ...rows.slice(start, start + windowSize).map((c, offset) => {
      const i = start + offset
      const disabled = c.available === false
      const head = clipToWidth(i === index ? `${SYMBOLS.pointer} /${c.name}` : `  /${c.name}`, inner)
      const desc = disabled ? ` （${c.disabled_reason || "不可用"}）` : ` ${c.desc || c.description || ""}`
      const budget = Math.max(0, inner - stringWidth(head))
      return h(
        "box",
        {
          key: c.name,
          flexDirection: "row",
          onMouseOver: () => !disabled && onHover?.(i),
          onMouseDown: () => !disabled && onSelect?.(i),
        },
        h("text", { content: head, fg: i === index && !disabled ? TOKENS.accent : TOKENS.textMuted }),
        h("text", { content: clipToWidth(desc, budget), fg: disabled ? TOKENS.warning : TOKENS.textMuted })
      )
    }),
    h("text", { content: clipToWidth(` ${index + 1}/${rows.length} · ${HINTS.navigate} · Enter 填入草稿`, inner), fg: TOKENS.textDim })
  )
}
