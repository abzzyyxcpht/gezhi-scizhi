// 科研时间线面板（/timeline，科研模式创新模块）：把科研流水线按 ZCode workflow-timeline 的
// 词汇终端化 —— 轨(rail) / 站(station) / 灯(lamp) / 计量条(meter) / 空洞(hole) / 计数行。
// 数据来自既有 bridge 只读命令（Python 零改动）：goal + 假设/共识/事实/实验树/待办。
// 小终端窗口化：timelineGeometry 单一推导，预算与渲染同源；光标只落站点行。
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS, HINTS } from "../theme/symbols.js"
import { statusLamp, statusLabel, meterBar, countLine, compactNumber, divider } from "./ui-parts.js"

const h = React.createElement

/** 阶段定义（顺序即流水线顺序；label/unit/holeHint 为该阶段的终端表述） */
export const TIMELINE_STAGES = [
  { key: "goal", label: "立项目标", unit: "", holeHint: "用 /goal <目标> 立项" },
  { key: "hypotheses", label: "假设台", unit: "条", holeHint: "假设来自 <br> 智能体提议或 /plan 拆解" },
  { key: "evidence", label: "证据池", unit: "条", holeHint: "证据来自检索与 web_reader" },
  { key: "facts", label: "世界事实", unit: "张", holeHint: "用 /fact add <论断> 录入" },
  { key: "experiments", label: "实验树", unit: "节点", holeHint: "实验记录来自 /run-experiment" },
  { key: "todos", label: "待办复核", unit: "项", holeHint: "待办来自报告生成与复核建议" },
]

/**
 * 行模型（渲染与预算同源）。station 行为光标可落行；header/rail/summary/footer 为家具行。
 * status：done=有数据 / pending=空洞（ZCode hole：未写的阶段画虚线空灯）。
 */
export function timelineRowModel(data = {}) {
  const stages = data.stages || []
  const rows = [{ kind: "header", label: ` 科研时间线 · ${stages.length} 阶段 · ${data.goal ? "目标已立项" : "未立项"}`, fg: TOKENS.accent, bold: true }]
  rows.push({ kind: "rail", label: ` ${divider(Math.max(16, Math.min(58, data.width || 40)))}`, fg: TOKENS.borderSubtle })
  for (const st of stages) {
    const meta = TIMELINE_STAGES.find((s) => s.key === st.key) || TIMELINE_STAGES[0]
    rows.push({
      kind: st.status === "pending" ? "hole" : "station",
      key: `stage:${st.key}`,
      stage: st.key,
      label: ` ${meta.label}`,
      status: st.status,
      detail: st.detail || "",
      meter: st.meter,
      hint: meta.holeHint,
    })
  }
  rows.push({ kind: "summary", label: countLine(data.counts || []), fg: TOKENS.textMuted })
  rows.push({ kind: "footer", label: ` ${HINTS.scroll} · /goal 立项 · /hypotheses 细节`, fg: TOKENS.textDim })
  return rows
}

/** 面板几何（预算与渲染同源） */
export function timelineGeometry(terminalHeight = 24, data = null) {
  const totalRows = timelineRowModel(data || {}).length
  const maxVisibleRows = Math.max(4, Math.floor(terminalHeight || 24) - 19)
  const visibleRows = Math.min(totalRows, maxVisibleRows)
  const overflows = totalRows > visibleRows
  return { totalRows, maxVisibleRows, visibleRows, overflows, panelRows: visibleRows + 4 + (overflows ? 2 : 0) }
}

/** 条目序号 → 扁平行号（光标只落 station/hole 行） */
export function timelineCursorRow(rows, ordinal = 0) {
  const cursors = rows.map((r, i) => (r.kind === "station" || r.kind === "hole" ? i : -1)).filter((i) => i >= 0)
  if (!cursors.length) return 0
  return cursors[Math.max(0, Math.min(ordinal, cursors.length - 1))]
}

/** 依据光标行修正窗口起点（光标行必须可见） */
export function timelineScrollFor(rows, cursor, scroll, maxVisibleRows) {
  const cursorRow = Math.max(0, Math.min(cursor, rows.length - 1))
  const maxScroll = Math.max(0, rows.length - maxVisibleRows)
  let next = Math.min(Math.max(0, scroll), maxScroll)
  if (cursorRow < next) next = cursorRow
  if (cursorRow >= next + maxVisibleRows) next = cursorRow - maxVisibleRows + 1
  return Math.min(Math.max(0, next), maxScroll)
}

export function ResearchTimelinePanel({ data = {}, cursor = 0, scroll = 0, maxVisibleRows = 12, width = 74 }) {
  const rows = timelineRowModel(data)
  const start = timelineScrollFor(rows, cursor, scroll, maxVisibleRows)
  const window = rows.slice(start, start + maxVisibleRows)
  const hiddenAbove = start > 0
  const hiddenBelow = start + maxVisibleRows < rows.length
  const cursorRow = timelineCursorRow(rows, cursor)
  const inner = Math.max(20, Math.min(width, 78) - 2)
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: Math.min(width, 78) },
    hiddenAbove ? h("text", { content: `  ${SYMBOLS.lampPending} 更多（第 ${start + 1} / ${rows.length} 行）`, fg: TOKENS.textDim }) : null,
    ...window.map((row, i) => {
      const pos = start + i
      if (row.kind === "header") return h("text", { key: "h", content: row.label, fg: row.fg, bold: true })
      if (row.kind === "rail") return h("text", { key: "r", content: row.label.slice(0, inner), fg: row.fg })
      if (row.kind === "summary") return h("text", { key: "s", content: ` ${row.label || "（暂无计数）"}`, fg: row.fg })
      if (row.kind === "footer") return h("text", { key: "f", content: row.label.slice(0, inner), fg: row.fg })
      const selected = pos === cursorRow
      const lamp = statusLamp(row.status)
      const lampFg = row.status === "failed" ? TOKENS.error : row.status === "running" ? TOKENS.warning : row.status === "done" ? TOKENS.success : TOKENS.textDim
      const label = row.kind === "hole" ? `${row.label}` : row.label
      const detail = row.kind === "hole" ? `  ┈ ${row.hint}` : row.detail ? `  ${row.detail}` : ""
      const meter = typeof row.meter === "number" && row.meter > 0 ? `  ${meterBar(row.meter, 8)}` : ""
      return h(
        "box",
        { key: row.key, flexDirection: "row" },
        h("text", { content: selected ? "❯ " : "  ", fg: selected ? TOKENS.accent : TOKENS.textMuted }),
        h("text", { content: lamp, fg: lampFg }),
        h("text", { content: label, fg: selected ? TOKENS.textStrong : TOKENS.text }),
        h("text", { content: detail, fg: row.kind === "hole" ? TOKENS.textDim : TOKENS.textMuted }),
        meter ? h("text", { content: meter, fg: TOKENS.success }) : null,
        selected ? h("text", { content: `  ${statusLabel(row.status)}`, fg: TOKENS.textDim }) : null
      )
    }),
    hiddenBelow ? h("text", { content: `  ${SYMBOLS.lampPending} 更多（共 ${rows.length} 行）`, fg: TOKENS.textDim }) : null
  )
}

/** 计数行数据构造（ZCode 计数行：✓n · ◌n · ✕n · ○n，0 缺席） */
export function timelineCounts(stages = []) {
  const by = (s) => stages.filter((st) => st.status === s).length
  return [
    { key: "done", n: by("done"), symbol: SYMBOLS.success },
    { key: "running", n: by("running"), symbol: SYMBOLS.lampRunning },
    { key: "failed", n: by("failed"), symbol: SYMBOLS.failure },
    { key: "pending", n: by("pending"), symbol: SYMBOLS.lampPending },
  ]
}

/** 行数换算：供预算/测试对齐紧凑数字格式 */
export function timelineDetail(stage, count, extra = "") {
  if (!count) return ""
  const unit = (TIMELINE_STAGES.find((s) => s.key === stage) || {}).unit || ""
  return `${compactNumber(count)}${unit ? ` ${unit}` : ""}${extra ? ` · ${extra}` : ""}`
}
