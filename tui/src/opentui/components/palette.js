// 命令面板（Ctrl+P）—— 数据逻辑复用 Ink 版 paletteCommands（中文搜索 + relevance）
// 鼠标：行 hover 与键盘共享单一 index（last-input-wins），mousedown 即选中
import React from "react"
import stringWidth from "string-width"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS, HINTS } from "../theme/symbols.js"
import { paletteCommands } from "../../command-palette.js"
import { clipToWidth } from "../layout/width-budget.js"

const h = React.createElement

export function PalettePanel({
  query = "",
  index = 0,
  commands = [],
  plugins = [],
  mode = "research",
  context = {},
  width = 74,
  onHover,
  onSelect,
}) {
  const rows = paletteCommands(commands, plugins, query, mode, context)
  if (!rows.length) return h("text", { content: "（无匹配命令）", fg: TOKENS.textMuted })
  const boxWidth = Math.min(width, 74)
  const inner = Math.max(8, boxWidth - 2)
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: boxWidth },
    h("text", { content: clipToWidth(` 命令面板 · 搜索：${query || "（输入中文/英文过滤）"}`, inner), fg: TOKENS.accent }),
    ...rows.slice(0, 8).map((c, i) => {
      const disabled = c.available === false
      const head = i === index ? `${SYMBOLS.pointer} /${c.name}` : `  /${c.name}`
      const desc = ` ${c.desc || c.description || ""}`
      const budget = Math.max(0, inner - stringWidth(head) - 1)
      return h(
        "box",
        {
          key: c.name,
          flexDirection: "row",
          onMouseOver: () => !disabled && onHover?.(i),
          onMouseDown: () => !disabled && onSelect?.(i),
        },
        h("text", { content: head, fg: i === index && !disabled ? TOKENS.accent : TOKENS.textMuted }),
        h("text", { content: clipToWidth(desc, budget), fg: TOKENS.textMuted }),
        disabled ? h("text", { content: ` （${c.disabled_reason || "不可用"}）`, fg: TOKENS.warning }) : null
      )
    }),
    h("text", { content: clipToWidth(` ${HINTS.navigate} · Enter 填入草稿 · 鼠标可点选`, inner), fg: TOKENS.textDim })
  )
}
