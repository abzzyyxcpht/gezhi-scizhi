// 全屏阅读器（/read digest|outline|cites|board|graph|ledger，Claude Code pager 对标）
// Ink 版 reader 的 OpenTUI 移植：标题栏、行号、进度条、逐行/翻页滚动、
// "/" 搜索（Enter 跳转、n/N 下一处/上一处）、q/Esc 返回。
import React from "react"
import stringWidth from "string-width"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"
import { clipToWidth } from "../layout/width-budget.js"

const h = React.createElement

/**  offset 收敛到 [0, max(0, total-rows)]；rows 非正时归零（无可见区）。 */
export function clampOffset(offset, total, rows) {
  if (!Number.isFinite(rows) || rows <= 0) return 0
  const value = Number.isFinite(offset) ? Math.trunc(offset) : 0
  return Math.max(0, Math.min(value, Math.max(0, (total || 0) - rows)))
}

/** 查询命中行号（大小写不敏感；空查询无命中）。 */
export function matchLines(lines = [], query = "") {
  const q = (query || "").trim().toLowerCase()
  if (!q) return []
  const hits = []
  ;(lines || []).forEach((text, i) => {
    if (String(text).toLowerCase().includes(q)) hits.push(i)
  })
  return hits
}

/** 从当前 offset 起按方向跳到下一处/上一处命中；环绕到另一端。 */
export function jumpToMatch(offset, lines, query, dir = 1) {
  const hits = matchLines(lines, query)
  if (!hits.length) return offset
  if (dir >= 0) {
    const next = hits.find((i) => i > offset)
    return next === undefined ? hits[0] : next
  }
  const prev = [...hits].reverse().find((i) => i < offset)
  return prev === undefined ? hits[hits.length - 1] : prev
}

export function ReaderPanel({
  title = "阅读器",
  lines = [],
  offset = 0,
  query = "",
  searchMode = false,
  width = 74,
  height = 20,
}) {
  const rows = Math.max(3, (height || 20) - 4)
  const safe = clampOffset(offset, lines.length, rows)
  const visible = lines.slice(safe, safe + rows)
  const gutter = String(Math.max(1, lines.length)).length
  const hits = query ? matchLines(lines, query) : []
  const progress = lines.length <= rows ? 1 : Math.min(1, (safe + rows) / lines.length)
  const filled = Math.round(progress * 20)
  const boxWidth = Math.min(width, 100)
  const inner = Math.max(8, boxWidth - 2)
  const textWidth = Math.max(8, inner - gutter - 3)
  return h(
    "box",
    {
      flexDirection: "column",
      border: true,
      borderStyle: "single",
      borderColor: TOKENS.borderFocus,
      width: boxWidth,
    },
    h("text", {
      content: clipToWidth(` 「${title}」 · ${lines.length} 行${query ? ` · 搜索「${query}」${hits.length} 处` : ""}`, inner),
      fg: TOKENS.accent,
    }),
    ...visible.map((text, i) =>
      h(
        "box",
        { key: safe + i, flexDirection: "row" },
        h("text", { content: `${String(safe + i + 1).padStart(gutter)} `, fg: TOKENS.textDim }),
        h("text", {
          content: clipToWidth(text, textWidth),
          fg: hits.includes(safe + i) ? TOKENS.warning : TOKENS.text,
        })
      )
    ),
    h("text", { content: clipToWidth(` [${"=".repeat(filled)}${" ".repeat(20 - filled)}] ${Math.round(progress * 100)}%`, inner), fg: TOKENS.textDim }),
    h("text", {
      content: clipToWidth(
        searchMode
          ? ` 搜索：${query || ""}（Enter 跳转 · Esc 取消）`
          : ` ${SYMBOLS.bullet} ↑↓ 逐行 · ←→ 翻页 · PgUp/PgDn 整页 · / 搜索 · n/N 跳转 · q/Esc 返回`,
        inner
      ),
      fg: TOKENS.textDim,
    })
  )
}
