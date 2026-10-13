// 连接面板（/connections）：服务连接 / 模型配置 / 用途路由 只读总览（数据源 settings_get）
// 小终端窗口化：connectionsGeometry 单一推导，预算与渲染同源；↑↓ 滚动，Esc 关闭。
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"

const h = React.createElement

const STAGE_LABELS = { planning: "规划", extraction: "提取", synthesis: "综合", review: "审查", fast: "快档", strong: "强档" }

function maskUrl(url = "") {
  const text = String(url || "")
  return text.replace(/\/\/([^/@]+)@/, "//") // 带凭据地址在面板中不显示用户信息
}

/** 行模型（组标题 + 条目）；数据来自 connections()（model_registry.describe 归一） */
export function connectionsRowModel(data = {}) {
  const connections = data.connections || []
  const profiles = data.profiles || []
  const resolved = data.resolved || []
  const rows = []
  rows.push({ kind: "header", label: `服务连接（${connections.length}）` })
  if (connections.length === 0) {
    rows.push({ kind: "note", text: "（无自定义连接 · 全部沿用内置 legacy 连接；桌面端 /model 配置）" })
  }
  for (const c of connections) {
    const marker = c.active ? "◉" : "○"
    rows.push({
      kind: "item",
      key: `conn:${c.id}`,
      label: `${marker} ${c.label || c.id} · ${c.id}`,
      value: `${c.protocol || "chat_completions"} · ${maskUrl(c.base_url) || "地址未记录"} · ${c.has_api_key ? "密钥已配置" : "密钥未配置"}`,
    })
  }
  rows.push({ kind: "header", label: `模型配置（${profiles.length}）` })
  if (profiles.length === 0) {
    rows.push({ kind: "note", text: "（未定义命名模型配置 · 各阶段使用内置 legacy）" })
  }
  for (const p of profiles) {
    const marker = p.active ? "◉" : "○"
    rows.push({
      kind: "item",
      key: `profile:${p.id}`,
      label: `${marker} ${p.label || p.id} · 连接 ${p.connection_id || "legacy"}`,
      value: `${p.model || "未填模型"} · 思考 ${p.reasoning_effort || "auto"}`,
    })
  }
  rows.push({ kind: "header", label: `各阶段生效（${resolved.length}）` })
  if (resolved.length === 0) {
    rows.push({ kind: "note", text: "（未读取到生效配置 · 默认全部走 legacy）" })
  }
  for (const r of resolved) {
    rows.push({
      kind: "item",
      key: `stage:${r.stage}`,
      label: `${STAGE_LABELS[r.stage] || r.stage} → ${r.profile_id || "legacy"}`,
      value: `${r.model || "未记录模型"} · ${r.protocol || "chat_completions"} · 连接 ${r.connection_id || "legacy"}`,
    })
  }
  return rows
}

/** 面板几何（预算与渲染同源）：窗口按终端高度推导，panelRows == 实际渲染最坏行数 */
export function connectionsGeometry(terminalHeight = 24, data = null) {
  const totalRows = connectionsRowModel(data || {}).length
  const maxVisibleRows = Math.max(4, Math.floor(terminalHeight || 24) - 19)
  const visibleRows = Math.min(totalRows, maxVisibleRows)
  const overflows = totalRows > visibleRows
  return {
    totalRows,
    maxVisibleRows,
    visibleRows,
    overflows,
    panelRows: visibleRows + 4 + (overflows ? 2 : 0),
  }
}

/** 条目序号 → 扁平行号（光标只落在条目行；无条目时回 0） */
export function connectionsCursorRow(rows, ordinal = 0) {
  const cursors = rows.map((r, i) => (r.kind === "item" ? i : -1)).filter((i) => i >= 0)
  if (!cursors.length) return 0
  return cursors[Math.max(0, Math.min(ordinal, cursors.length - 1))]
}

/** 依据光标行修正窗口起点（光标行必须可见） */
export function connectionsScrollFor(rows, cursor, scroll, maxVisibleRows) {
  const cursorRow = Math.max(0, Math.min(cursor, rows.length - 1))
  const maxScroll = Math.max(0, rows.length - maxVisibleRows)
  let next = Math.min(Math.max(0, scroll), maxScroll)
  if (cursorRow < next) next = cursorRow
  if (cursorRow >= next + maxVisibleRows) next = cursorRow - maxVisibleRows + 1
  return Math.min(Math.max(0, next), maxScroll)
}

export function ConnectionsPanel({ data = {}, cursor = 0, scroll = 0, maxVisibleRows = 12, width = 74 }) {
  const rows = connectionsRowModel(data)
  const start = connectionsScrollFor(rows, cursor, scroll, maxVisibleRows)
  const window = rows.slice(start, start + maxVisibleRows)
  const hiddenAbove = start > 0
  const hiddenBelow = start + maxVisibleRows < rows.length
  const cursorKey = rows[Math.max(0, Math.min(cursor, rows.length - 1))]?.key
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: Math.min(width, 78) },
    h("text", { content: " 连接总览 · ↑↓ 移动 · Esc 关闭（编辑请在桌面端 /model）", fg: TOKENS.accent }),
    hiddenAbove ? h("text", { content: `  ↑ 更多（第 ${start + 1} / ${rows.length} 行）`, fg: TOKENS.textDim }) : null,
    ...window.map((row, i) => {
      if (row.kind === "header") {
        return h("text", { key: `h${start + i}`, content: ` ${row.label}`, fg: TOKENS.modeResearch })
      }
      if (row.kind === "note") {
        return h("text", { key: `n${start + i}`, content: ` ${row.text}`, fg: TOKENS.textDim })
      }
      const selected = row.key === cursorKey
      return h(
        "box",
        { key: row.key, flexDirection: "row" },
        h("text", { content: selected ? `${SYMBOLS.pointer} ` : "  ", fg: selected ? TOKENS.accent : TOKENS.textMuted }),
        h("text", { content: row.label, fg: selected ? TOKENS.textStrong : TOKENS.text }),
        row.value ? h("text", { content: `  ${row.value}`, fg: selected ? TOKENS.accent : TOKENS.textMuted }) : null
      )
    }),
    hiddenBelow ? h("text", { content: `  ↓ 更多（共 ${rows.length} 行）`, fg: TOKENS.textDim }) : null,
    h("text", { content: ` 共 ${rows.filter((r) => r.kind === "item").length} 项 · /models 切换阶段模型 · /config 模型与主题 · 写操作走 /connection save|delete`, fg: TOKENS.textDim })
  )
}
