// 快捷键帮助面板（F1 / Ctrl+/ 打开；对标顶级 harness 的快捷键面板）
// 小终端窗口化：按终端高度裁窗（shortcutsGeometry 单一推导，预算与渲染同源），↑↓ 滚动。
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"

const h = React.createElement

export const SHORTCUT_GROUPS = [
  {
    title: "模式",
    items: [
      { keys: "Ctrl+G / Shift+Tab", desc: "切换 通用◇ / 科研◎" },
      { keys: "/mode <general|research>", desc: "命令方式切换模式" },
      { keys: "/goal <目标>", desc: "研究目标模式（目标常驻）" },
    ],
  },
  {
    title: "面板",
    items: [
      { keys: "Ctrl+P / Ctrl+U", desc: "命令面板（中文搜索）" },
      { keys: "/config", desc: "配置面板（模型/主题/科研/界面）" },
      { keys: "/hypotheses /consensus /matrix /experiments /facts", desc: "科研分析面板（科研模式）" },
      { keys: "Ctrl+I", desc: "上下文分解面板" },
      { keys: "F1", desc: "本快捷键帮助" },
      { keys: "F12", desc: "Debug overlay（渲染统计）" },
    ],
  },
  {
    title: "会话",
    items: [
      { keys: "Ctrl+S", desc: "回合中追加指导（steer）" },
      { keys: "Ctrl+O", desc: "展开/折叠工具调用详情" },
      { keys: "Ctrl+B", desc: "侧栏：宽屏停靠，窄屏抽屉；Esc 关闭" },
      { keys: "Ctrl+R", desc: "历史搜索（过滤历史输入）" },
      { keys: "Ctrl+T", desc: "研究待办面板" },
      { keys: "!<命令>", desc: "shell 模式（项目根目录执行）" },
      { keys: "@<路径>", desc: "文件提及补全" },
    ],
  },
  {
    title: "编辑与滚动",
    items: [
      { keys: "↑ / ↓", desc: "历史输入（无面板时）" },
      { keys: "PageUp / PageDown", desc: "会话区翻页" },
      { keys: "Home / End", desc: "行首 / 行尾" },
      { keys: "Ctrl+K", desc: "删除光标到行尾" },
      { keys: "Ctrl+L", desc: "清屏（保留草稿）" },
      { keys: "Ctrl+Y", desc: "复制选区（OSC52）" },
    ],
  },
  {
    title: "退出",
    items: [
      { keys: "Ctrl+C ×2", desc: "双击退出（首次清草稿/复制选区）" },
      { keys: "Ctrl+D", desc: "直接退出" },
      { keys: "Esc", desc: "关面板 → 暂停输出 → 再按提示退出" },
    ],
  },
]

/** 扁平行模型（组标题 + 条目），渲染与预算同源 */
export function shortcutsRowModel(groups = SHORTCUT_GROUPS) {
  const rows = []
  for (const g of groups) {
    rows.push({ kind: "header", label: g.title })
    for (const item of g.items) rows.push({ kind: "item", keys: item.keys, desc: item.desc })
  }
  return rows
}

/**
 * 面板几何（预算与渲染同源）：
 * 窗口按终端高度推导（预留 chrome 10 + 会话区 4 + 面板固定开销 6），
 * panelRows = 可视行 + 头/脚 + 边框（+ 溢出指示 2，仅溢出时）。
 */
export function shortcutsGeometry(terminalHeight = 24, groups = SHORTCUT_GROUPS) {
  const totalRows = shortcutsRowModel(groups).length
  const maxVisibleRows = Math.max(6, Math.floor(terminalHeight || 24) - 20)
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

/** 滚动起点钳制（内容不足一屏时为 0） */
export function shortcutsScrollFor(scroll, totalRows, maxVisibleRows) {
  const max = Math.max(0, totalRows - maxVisibleRows)
  return Math.min(Math.max(0, Math.floor(scroll || 0)), max)
}

export function ShortcutsPanel({ width = 74, scroll = 0, maxVisibleRows = 20, groups = SHORTCUT_GROUPS }) {
  const rows = shortcutsRowModel(groups)
  const start = shortcutsScrollFor(scroll, rows.length, maxVisibleRows)
  const window = rows.slice(start, start + maxVisibleRows)
  const hiddenAbove = start > 0
  const hiddenBelow = start + maxVisibleRows < rows.length
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: Math.min(width, 80) },
    h("text", { content: ` 快捷键 ${SYMBOLS.bullet} F1 开关 · Esc 关闭`, fg: TOKENS.accent }),
    hiddenAbove ? h("text", { content: `  ↑ 更多（第 ${start + 1} / ${rows.length} 行）`, fg: TOKENS.textDim }) : null,
    ...window.map((row, i) => row.kind === "header"
      ? h("text", { key: `h${start + i}`, content: ` ${row.label}`, fg: TOKENS.modeResearch })
      : h(
          "box",
          { key: `i${start + i}`, flexDirection: "row" },
          h("text", { content: `  ${row.keys}`.slice(0, 34), fg: TOKENS.textStrong }),
          h("text", { content: `  ${row.desc}`.slice(0, 36), fg: TOKENS.textMuted })
        )),
    hiddenBelow ? h("text", { content: `  ↓ 更多（共 ${rows.length} 行）`, fg: TOKENS.textDim }) : null,
    h("text", { content: ` ↑↓ 滚动 · 共 ${rows.length} 行 · Esc 关闭`, fg: TOKENS.textDim })
  )
}

export const SHORTCUTS_ROWS = SHORTCUT_GROUPS.reduce((n, g) => n + g.items.length + 2, 0)
