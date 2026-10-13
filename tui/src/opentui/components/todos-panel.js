// 研究待办面板（Ctrl+T，Claude Code toggleTodos 对标）：只读列表 + /todo 引导
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"

const h = React.createElement

const STATUS_MARK = { pending: "○", doing: "◐", done: "●" }
const STATUS_LABEL = { pending: "待办", doing: "进行中", done: "完成" }

export function TodosPanel({ todos = [], width = 74 }) {
  if (!todos.length) {
    return h(
      "box",
      { border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
      h("text", { content: " 研究待办：暂无记录（/todo save 录入）", fg: TOKENS.textMuted })
    )
  }
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.modeResearch, width: Math.min(width, 78) },
    h("text", { content: ` 研究待办 · ${todos.length} 项`, fg: TOKENS.modeResearch }),
    ...todos.slice(0, 10).map((t) =>
      h(
        "box",
        { key: t.id || t.title, flexDirection: "row" },
        h("text", { content: `${STATUS_MARK[t.status] || "○"} `, fg: t.status === "done" ? TOKENS.success : t.status === "doing" ? TOKENS.warning : TOKENS.textMuted }),
        h("text", { content: t.title.slice(0, 52), fg: t.status === "done" ? TOKENS.textDim : TOKENS.text }),
        h("text", { content: ` ${STATUS_LABEL[t.status] || t.status}`, fg: TOKENS.textDim })
      )
    ),
    h("text", { content: ` ${SYMBOLS.bullet} /todo save <JSON> 录入 · Ctrl+T 开关`, fg: TOKENS.textDim })
  )
}
