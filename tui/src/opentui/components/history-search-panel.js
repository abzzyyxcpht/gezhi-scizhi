// 历史搜索面板（Ctrl+R，Claude Code history:search 对标）：过滤 100 条历史，Enter 填入草稿
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"

const h = React.createElement

/** 子序列模糊过滤（复用 slash 补全口径） */
export function filterHistory(history = [], query = "") {
  const q = (query || "").trim().toLowerCase()
  if (!q) return history.slice(-50).reverse()
  return history
    .map((text, i) => ({ text, i }))
    .filter(({ text }) => text.toLowerCase().includes(q))
    .reverse()
    .map(({ text }) => text)
}

export function HistorySearchPanel({ history = [], query = "", index = 0, width = 74 }) {
  const rows = filterHistory(history, query)
  if (!rows.length) {
    return h(
      "box",
      { border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
      h("text", { content: ` 历史搜索：无匹配${query ? `（${query}）` : ""}`, fg: TOKENS.textMuted })
    )
  }
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: Math.min(width, 80) },
    h("text", { content: ` 历史搜索 ${query ? `· ${query}` : ""} · ${rows.length} 条`, fg: TOKENS.accent }),
    ...rows.slice(0, 8).map((text, i) =>
      h(
        "box",
        { key: i, flexDirection: "row", onMouseOver: () => undefined },
        h("text", { content: i === index ? `${SYMBOLS.pointer} ` : "  ", fg: i === index ? TOKENS.accent : TOKENS.textMuted }),
        h("text", { content: text.slice(0, 66), fg: i === index ? TOKENS.textStrong : TOKENS.text })
      )
    ),
    h("text", { content: " ↑↓ 选择 · Enter 填入草稿 · Esc 关闭 · 继续输入过滤", fg: TOKENS.textDim })
  )
}
