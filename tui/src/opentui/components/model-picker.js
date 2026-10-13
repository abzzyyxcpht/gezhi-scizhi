// 科研模型配置面板（/models|/model）—— Ink ModelPicker 的 OpenTUI 移植
// 状态机在 session-controller（modelPickerOpen/Move/Tab/Save/Close），本组件只渲染。
// 小终端自适应：候选窗口按终端高度收缩（modelPickerGeometry 单一推导，布局预算与渲染同源），
// 错误行/页脚优先保留，候选项吸收剩余空间并向光标居中。
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"
import { clipToWidth } from "../layout/width-budget.js"

const h = React.createElement

const STAGE_LABELS = { planning: "规划", extraction: "提取", synthesis: "综合", review: "审查", default: "默认" }
const stageLabel = stage => `${STAGE_LABELS[stage] || stage}（${stage}）`

/** 候选窗口尺寸：小终端下限 1（错误行/页脚优先），上限 6 */
export function modelPickerItemRows(terminalHeight = 24) {
  const height = Math.floor(terminalHeight || 24)
  return Math.min(6, Math.max(1, height - 20))
}

/** 内容行预算：错误行/页脚/目标详情优先保留（下限 8，对齐 54x18 窄屏帧约束） */
export function modelPickerContentBudget(terminalHeight = 24) {
  const height = Math.floor(terminalHeight || 24)
  return Math.max(8, height - 10)
}

function buildPickerRows(state, itemRows, withIndicators) {
  const total = state.catalog?.items?.length || 0
  const count = Math.max(1, Math.min(Math.max(1, itemRows), total || 1))
  const start = Math.max(0, Math.min(Math.max(0, total - count), state.index - Math.floor(count / 2)))
  const hiddenAbove = withIndicators && start > 0
  const hiddenBelow = withIndicators && start + count < total
  const target = state.catalog?.items[state.index]
  const efforts = target?.efforts || []
  const option = efforts.find((row) => row.value === state.effort)
  return [
    { text: `科研模型配置 · ${state.loading ? "读取中…" : state.saving ? "保存中…" : "新请求"}`, fg: TOKENS.accent, bold: true },
    { text: `${state.focus === "stage" ? SYMBOLS.pointer : " "} 阶段 ${stageLabel(state.stage)} · Tab 切换选择项`, fg: state.focus === "stage" ? TOKENS.accent : TOKENS.textDim },
    ...(hiddenAbove ? [{ text: ` ↑ 还有 ${start} 项`, fg: TOKENS.textDim }] : []),
    ...((state.catalog?.items || []).slice(start, start + count).map((row, i) => ({
      text: `${start + i === state.index ? SYMBOLS.pointer : " "} ${row.label || row.id} · ${row.model || "未配置"}${row.id === state.catalog.active_id ? " ✓ 当前" : ""}`,
      fg: start + i === state.index ? TOKENS.accent : TOKENS.textDim,
    }))),
    ...(hiddenBelow ? [{ text: ` ↓ 还有 ${total - start - count} 项`, fg: TOKENS.textDim }] : []),
    ...(target ? [
      { text: `协议 ${target.protocol || "未记录"} · ${target.configured ? "已配置" : "需配置连接"}`, fg: TOKENS.textDim },
      { text: `${state.focus === "effort" ? SYMBOLS.pointer : " "} 思考 ${state.effort} · ${option?.enabled === false ? "不可选" : "所选目标配置"}`, fg: state.focus === "effort" ? TOKENS.accent : TOKENS.textDim },
    ] : []),
    { text: "档位仅经本地适配器校验，供应商能力未验证", fg: TOKENS.textDim },
    ...(state.error ? [{ text: state.error, fg: TOKENS.error }] : []),
    { text: "↑↓ 选择 · Enter 保存 · Esc 返回 · Ctrl+R 刷新", fg: TOKENS.textDim },
  ]
}

/**
 * 面板行模型（渲染与预算同源）：
 * 头部 + 阶段 + 候选窗口(±溢出指示) + 目标详情 + 适配器提醒 + 可选错误 + 页脚。
 * 超出 maxRows 时先丢溢出指示行，保证错误行/页脚始终可见。
 */
export function modelPickerRowModel(state, itemRows = 6, { maxRows = Infinity } = {}) {
  if (!state) return []
  const all = buildPickerRows(state, itemRows, true)
  return all.length <= maxRows ? all : buildPickerRows(state, itemRows, false)
}

/** 面板几何：itemRows 为候选窗口，panelRows 为布局预算（内容行 + 上下边框） */
export function modelPickerGeometry(terminalHeight = 24, state = null) {
  const itemRows = modelPickerItemRows(terminalHeight)
  const maxContentRows = modelPickerContentBudget(terminalHeight)
  const rows = modelPickerRowModel(state, itemRows, { maxRows: maxContentRows })
  const contentRows = Math.max(4, rows.length)
  return { itemRows, maxContentRows, contentRows, panelRows: contentRows + 2 }
}

export function ModelPickerPanel({ state, width = 74, itemRows = 6, maxContentRows = Infinity }) {
  if (!state) return null
  const inner = Math.max(20, Math.min(width, 78) - 2)
  const rows = modelPickerRowModel(state, itemRows, { maxRows: maxContentRows })
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: Math.min(width, 78) },
    ...rows.map((row, i) =>
      h("text", { key: i, content: clipToWidth(` ${row.text}`, inner), fg: row.fg, bold: Boolean(row.bold) })
    )
  )
}
