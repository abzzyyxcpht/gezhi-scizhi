// 上下文分解面板（dsh ContextMeter 对标）：消息 / 工具 schema / 输出预留 / 上限
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"

const h = React.createElement

function bar(used, total, width = 28) {
  if (!total) return "?".repeat(width)
  const filled = Math.max(0, Math.min(width, Math.round((used / total) * width)))
  return "█".repeat(filled) + "░".repeat(width - filled)
}

export function ContextPanel({ breakdown = null, width = 60 }) {
  if (!breakdown) {
    return h(
      "box",
      { border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
      h("text", { content: " 上下文分解：当前会话暂无数据", fg: TOKENS.textMuted })
    )
  }
  const { messages, tools, input, reserve, limit } = breakdown
  const pct = limit ? Math.round((input / limit) * 100) : 0
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: Math.min(width, 64) },
    h("text", { content: ` 上下文占用 ${pct}%（${input}/${limit}）`, fg: TOKENS.accent }),
    h("text", { content: ` ${bar(input, limit)}`, fg: TOKENS.accent }),
    h("text", { content: `   消息 ${messages} · 工具 schema ${tools} · 输出预留 ${reserve}`, fg: TOKENS.textMuted }),
    h("text", { content: ` ${SYMBOLS.bullet} 输入 = 消息 + 工具 schema；预留保障响应空间`, fg: TOKENS.textDim })
  )
}
