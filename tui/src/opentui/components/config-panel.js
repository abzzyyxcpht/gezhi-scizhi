// 配置面板（/config）：模型 / 主题 / 科研 / 界面 / 实验能力 分组聚合，Enter 即切即存（settings_save）
// 数据源：bridge settings_get + llm_status；本组件只负责展示与光标，保存由 runtime 接线。
// 小终端内部滚动：面板按 terminalHeight 窗口化（configPanelRows 预算与渲染共用一个推导），不溢出。
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"

const h = React.createElement

export const CONFIG_GROUPS = [
  {
    title: "模型",
    items: [
      { key: "llm_model", label: "默认模型", type: "model", desc: "科研任务使用的默认模型" },
      { key: "reasoning_effort", label: "推理强度", type: "cycle", options: ["low", "medium", "high"], desc: "思考链投入程度" },
      { key: "default_offline", label: "离线模式", type: "bool", desc: "开启后不访问外部文献源" },
    ],
  },
  {
    title: "主题",
    items: [
      { key: "appearance_dark_mode", label: "深色主题", type: "bool", desc: "关闭后切换浅色" },
      { key: "ui_animations", label: "界面动效", type: "bool", desc: "shimmer/spinner/切换过渡" },
      { key: "appearance_density", label: "信息密度", type: "cycle", options: ["comfortable", "compact"], desc: "compact 为窄屏优化" },
    ],
  },
  {
    title: "科研",
    items: [
      { key: "workspace_mode", label: "默认模式", type: "cycle", options: ["research", "general"], desc: "新会话默认用途" },
      { key: "lit_threshold", label: "文献阈值", type: "number", desc: "相关性筛选阈值 0–1" },
      { key: "memory_enabled", label: "研究记忆", type: "bool", desc: "长期记忆注入提示前缀" },
    ],
  },
  {
    title: "实验能力",
    items: [
      { key: "experimental_message_queue", label: "下一轮队列", type: "bool", desc: "忙时入队下一轮（/queue 管理）" },
      { key: "experimental_evidence_reviews", label: "后台证据复核", type: "bool", desc: "复核收件箱（/reviews 管理）" },
    ],
  },
]

/** 扁平化条目（面板索引用） */
export function flattenConfig(groups = CONFIG_GROUPS) {
  const rows = []
  for (const g of groups) {
    for (const item of g.items) rows.push({ ...item, group: g.title })
  }
  return rows
}

/** 面板行模型：组标题行 + 条目行（窗口化渲染用）；item 携带 flattenConfig 索引。 */
export function configRowModel(groups = CONFIG_GROUPS) {
  const rows = []
  let itemIndex = 0
  for (const g of groups) {
    rows.push({ kind: "header", label: g.title })
    for (const item of g.items) rows.push({ kind: "item", item, itemIndex: itemIndex++ })
  }
  return rows
}

/** 面板固定开销（行）：头部 1 + 页脚 1 + 上下边框 2；另有溢出指示行各 1（仅溢出时计入） */
export const CONFIG_PANEL_CHROME_ROWS = 4
export const CONFIG_PANEL_OVERFLOW_ROWS = 2

/**
 * 面板几何（预算与渲染共用，防止预算漂移）：
 * maxVisibleRows 按终端高度推导 —— 预留 chrome 10 行（品牌头/spinner/composer/footer/余量）
 * + 会话区最少 3 行 + 面板固定开销 6 行（含溢出指示），保证小终端下 transcript 不被压没。
 * panelRows 为布局预算行数：与 ConfigPanel 实际渲染行数一致（溢出时含上下指示行）。
 */
export function configPanelGeometry(terminalHeight = 24, groups = CONFIG_GROUPS) {
  const totalRows = configRowModel(groups).length
  const maxVisibleRows = Math.max(4, Math.floor(terminalHeight || 24) - 19)
  const visibleRows = Math.min(totalRows, maxVisibleRows)
  const overflows = totalRows > visibleRows
  return {
    totalRows,
    maxVisibleRows,
    visibleRows,
    overflows,
    panelRows: visibleRows + CONFIG_PANEL_CHROME_ROWS + (overflows ? CONFIG_PANEL_OVERFLOW_ROWS : 0),
  }
}

/** 依据光标条目修正窗口起点（光标行必须可见，含 1 行余量给组标题上下文）。 */
export function configScrollFor(rows, itemIndex, scroll, maxVisibleRows) {
  let cursorRow = 0
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].kind === "item" && rows[i].itemIndex === itemIndex) { cursorRow = i; break }
  }
  const maxScroll = Math.max(0, rows.length - maxVisibleRows)
  let next = Math.min(Math.max(0, scroll), maxScroll)
  if (cursorRow < next) next = cursorRow
  if (cursorRow >= next + maxVisibleRows) next = cursorRow - maxVisibleRows + 1
  return Math.min(Math.max(0, next), maxScroll)
}

function formatValue(item, values) {
  const v = values?.[item.key]
  if (item.type === "bool") return v ? "开" : "关"
  if (item.type === "model") return String(v || "(未配置)")
  if (v === undefined || v === null || v === "") return "(未设置)"
  return String(v)
}

export function ConfigPanel({ values = {}, index = 0, scroll = 0, maxVisibleRows = 12, width = 74, groups = CONFIG_GROUPS }) {
  const rows = configRowModel(groups)
  const start = Math.min(Math.max(0, scroll), Math.max(0, rows.length - maxVisibleRows))
  const window = rows.slice(start, start + maxVisibleRows)
  const hiddenAbove = start > 0
  const hiddenBelow = start + maxVisibleRows < rows.length
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: Math.min(width, 78) },
    h("text", { content: " 配置 · Enter 切换/修改（即切即存）· Esc 关闭", fg: TOKENS.accent }),
    hiddenAbove ? h("text", { content: `  ↑ 更多配置（第 ${start + 1} 行起）`, fg: TOKENS.textDim }) : null,
    ...window.map((row, i) => {
      if (row.kind === "header") {
        return h("text", { key: `h${start + i}`, content: ` ${row.label}`, fg: TOKENS.modeResearch })
      }
      const selected = row.itemIndex === index
      const item = row.item
      return h(
        "box",
        { key: item.key, flexDirection: "row" },
        h("text", { content: selected ? `${SYMBOLS.pointer} ` : "  ", fg: selected ? TOKENS.accent : TOKENS.textMuted }),
        h("text", { content: item.label, fg: selected ? TOKENS.textStrong : TOKENS.text }),
        h("text", { content: `  ${formatValue(item, values)}`, fg: selected ? TOKENS.accent : TOKENS.textMuted }),
        selected ? h("text", { content: `  ${item.desc}`.slice(0, 30), fg: TOKENS.textDim }) : null
      )
    }),
    hiddenBelow ? h("text", { content: `  ↓ 更多配置（共 ${rows.length} 行）`, fg: TOKENS.textDim }) : null,
    h("text", { content: ` ↑↓ 选择 · Enter 修改 · 共 ${flattenConfig(groups).length} 项 · /models 阶段模型 · /connections 连接总览`, fg: TOKENS.textDim })
  )
}
