// 人工确认关口菜单（Ink REVIEW_MENU 内联菜单的 OpenTUI 面板移植）：
// 任务进入 waiting_human 时弹出，编号选择执行（数字直选 / Enter 确认 / Esc 稍后）。
// 选项与文案与 Ink 共享 src/commands.js 的 REVIEW_MENU（单一来源）。
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS, HINTS } from "../theme/symbols.js"
import { REVIEW_MENU } from "../../commands.js"

const h = React.createElement

export function GateMenuPanel({ taskId = "", index = 0, onHover, onSelect, width = 74 }) {
  return h(
    "box",
    {
      flexDirection: "column",
      border: true,
      borderStyle: "single",
      borderColor: TOKENS.warning,
      width: Math.min(width, 74),
    },
    h("text", {
      content: ` ${SYMBOLS.approval} 待人工确认${taskId ? ` · 任务 ${taskId}` : ""}`,
      fg: TOKENS.warning,
      bold: true,
    }),
    ...REVIEW_MENU.map((item, i) =>
      h(
        "box",
        {
          key: item.key,
          flexDirection: "row",
          onMouseOver: () => onHover?.(i),
          onMouseDown: () => onSelect?.(i),
        },
        h("text", { content: i === index ? `${SYMBOLS.pointer} ` : "  ", fg: i === index ? TOKENS.warning : TOKENS.textMuted }),
        h("text", { content: `${item.key}. ${item.label}`, fg: i === index ? TOKENS.textStrong : TOKENS.text }),
        h("text", { content: `  → ${item.cmd}`.slice(0, 30), fg: TOKENS.textDim })
      )
    ),
    h("text", { content: ` ${HINTS.navigate} · 数字键直选 · Esc 稍后处理`, fg: TOKENS.textDim })
  )
}

/** 面板行数（panelRows 预算用）：标题 + 选项 + 提示行 */
export const GATE_MENU_ROWS = REVIEW_MENU.length + 2
