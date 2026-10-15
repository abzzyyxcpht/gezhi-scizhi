// 审批面板：警告色边框 + 编号选项 + 键盘独占
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS, HINTS } from "../theme/symbols.js"

const h = React.createElement

export const APPROVAL_OPTIONS = [
  { key: "allow_once", label: "允许一次", hint: "仅本次调用" },
  { key: "allow_session", label: "本会话允许", hint: "同类调用本会话内不再询问" },
  { key: "deny", label: "拒绝", hint: "拒绝本次调用" },
]

export function ApprovalPanel({ approval = null, index = 0, onHover, onSelect, width = 74 }) {
  if (!approval) return null
  return h(
    "box",
    {
      flexDirection: "column",
      border: true,
      borderStyle: "single",
      borderColor: TOKENS.warning,
      width: Math.min(width, 74),
    },
    h("text", { content: ` ${SYMBOLS.approval} 需要审批：${approval.tool}`, fg: TOKENS.warning, bold: true }),
    approval.reason ? h("text", { content: ` 原因：${approval.reason}`.slice(0, 70), fg: TOKENS.textMuted }) : null,
    approval.action
      ? h("text", {
          content: ` ${approval.action.risk || ""}类·需${approval.action.tier || ""}档：${approval.action.target || ""}${approval.action.reversible ? "" : "（不可逆）"}`.slice(0, 72),
          fg: TOKENS.textDim,
        })
      : null,
    ...APPROVAL_OPTIONS.map((opt, i) =>
      h(
        "box",
        {
          key: opt.key,
          flexDirection: "row",
          onMouseOver: () => onHover?.(i),
          onMouseDown: () => onSelect?.(i),
        },
        h("text", { content: i === index ? `${SYMBOLS.pointer} ` : "  ", fg: i === index ? TOKENS.warning : TOKENS.textMuted }),
        h("text", { content: `${i + 1}. ${opt.label}`, fg: i === index ? TOKENS.textStrong : TOKENS.text }),
        h("text", { content: `  ${opt.hint}`.slice(0, 40), fg: TOKENS.textDim })
      )
    ),
    h("text", { content: ` ${HINTS.navigate} · 数字键直选 · Esc 拒绝`, fg: TOKENS.textDim })
  )
}
