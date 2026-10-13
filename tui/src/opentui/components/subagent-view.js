// Subagent 面板与只读下钻视图（ZCode subagent-view 对标）
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS, HINTS } from "../theme/symbols.js"

const h = React.createElement

const STATUS_COLOR = { done: TOKENS.success, running: TOKENS.accent, failed: TOKENS.error, pending: TOKENS.textMuted }

/** Swarm 列表面板（selection 层） */
export function SubagentsPanel({ swarms = [], index = 0, onHover, onSelect, width = 74 }) {
  if (!swarms.length) {
    return h(
      "box",
      { border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
      h("text", { content: " （暂无 Subagent 计划；发送消息时可用 /swarm 拆分）", fg: TOKENS.textMuted })
    )
  }
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus, width: Math.min(width, 74) },
    h("text", { content: ` Subagent 计划（${swarms.length}）· Enter 下钻只读视图`, fg: TOKENS.accent }),
    ...swarms.slice(0, 8).map((s, i) =>
      h(
        "box",
        {
          key: s.plan_id || i,
          flexDirection: "row",
          onMouseOver: () => onHover?.(i),
          onMouseDown: () => onSelect?.(i),
        },
        h("text", { content: i === index ? `${SYMBOLS.pointer} ` : "  ", fg: i === index ? TOKENS.accent : TOKENS.textMuted }),
        h("text", { content: `${s.plan_id || "?"}`.slice(0, 18), fg: i === index ? TOKENS.textStrong : TOKENS.text }),
        h("text", { content: ` ${s.status || "?"} · ${(s.request || "").slice(0, 28)}`, fg: TOKENS.textMuted })
      )
    ),
    h("text", { content: ` ${HINTS.navigate} · Esc 关闭`, fg: TOKENS.textDim })
  )
}

/** 只读下钻视图：分支状态 + 子会话事件（整页替换主会话区，键盘几乎全消费） */
export function SubagentView({ swarm = null, events = [], onBack }) {
  if (!swarm) return null
  const branches = Array.isArray(swarm.branches) ? swarm.branches : []
  return h(
    "box",
    { flexDirection: "column", flexGrow: 1, border: true, borderStyle: "single", borderColor: TOKENS.accent, paddingLeft: 1 },
    h("text", { content: `${SYMBOLS.banner} Subagent ${swarm.plan_id} · ${swarm.status || "?"}`, fg: TOKENS.accent, bold: true }),
    h("text", { content: ` 目标：${(swarm.request || "").slice(0, 80)}`, fg: TOKENS.textMuted }),
    h("text", { content: ` 分支（${branches.length}）：`, fg: TOKENS.text }),
    ...branches.slice(0, 12).map((b, i) =>
      h(
        "box",
        { key: b.id || i, flexDirection: "row" },
        h("text", { content: `  ${SYMBOLS.bullet} `, fg: STATUS_COLOR[b.status] || TOKENS.textMuted }),
        h("text", { content: `${b.agent || "agent"}`, fg: TOKENS.text }),
        h("text", { content: ` ${(b.request || "").slice(0, 40)}`, fg: TOKENS.textMuted }),
        h("text", { content: ` [${b.status || "pending"}]`, fg: STATUS_COLOR[b.status] || TOKENS.textMuted })
      )
    ),
    h("text", { content: ` 最近事件：`, fg: TOKENS.text }),
    ...events.slice(-8).map((ev, i) =>
      h("text", { key: i, content: `  · ${ev.kind} ${String(ev.payload?.text || ev.payload?.name || "").slice(0, 50)}`, fg: TOKENS.textDim })
    ),
    h("text", { content: ` Esc / Ctrl+C 返回主会话`, fg: TOKENS.textDim })
  )
}
