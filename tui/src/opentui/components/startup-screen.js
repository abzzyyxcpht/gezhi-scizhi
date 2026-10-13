// 启动屏（ZCode TuiStartupScreen + Claude Code 欢迎屏对标）：
// 双模式格点图案逐行揭示 + shimmer 标题 + 脉冲连接态；reducedMotion 直切全量
import React from "react"
import { TOKENS, MODE_TOKENS, modeColor } from "../theme/tokens.js"
import { shimmerText } from "../motion/blend.js"
import { brailleFrame, revealStartupArt, pulseDot } from "../motion/frames.js"

const h = React.createElement

export function StartupScreen({ workspaceMode = "general", spinnerFrame = 0, shimmerFrame = 0, error = "", reducedMotion = false }) {
  const t = MODE_TOKENS[workspaceMode] || MODE_TOKENS.general
  const color = modeColor(TOKENS, workspaceMode)
  const rows = reducedMotion
    ? (workspaceMode === "research"
        ? ["           ✦", "         · ✦ ·", "      ╭─────────╮", "      │         │", "      │  格 致  │", "      │  SciZhi │", "      │         │", "      ╰─────────╯", "         · ✦ ·", "           ✦"]
        : ["·  ✦  ·  ✦  ·  ✦  ·  ✦", "   ╭───────────────────╮", "   │                   │", "   │      格   致      │", "   │      SciZhi       │", "   │                   │", "   ╰───────────────────╯", "·  ✦  ·  ✦  ·  ✦  ·  ✦"])
    : revealStartupArt(workspaceMode, spinnerFrame)
  // 图案揭示完成后再起 shimmer（帧号错开，避免抢焦点）
  const shimmerStart = reducedMotion ? 0 : 24
  return h(
    "box",
    { flexDirection: "column", flexGrow: 1, alignItems: "center", justifyContent: "center" },
    ...rows.map((row, i) =>
      h(
        "box",
        { key: i, flexDirection: "row" },
        ...shimmerText(row, shimmerFrame + i * 3, { accent: color, base: TOKENS.textDim }).map((s, ci) =>
          h("text", { key: ci, content: s.ch, fg: s.fg, bold: row.includes("格") || row.includes("SciZhi") })
        )
      )
    ),
    shimmerFrame >= shimmerStart || reducedMotion
      ? h("text", { content: `${t.symbol} ${t.label}模式 · 输入消息回车发送，Ctrl+G 切换模式`, fg: color })
      : null,
    h("text", { content: ` ${brailleFrame(spinnerFrame)} ${error || "连接科研引擎…"} ${pulseDot(spinnerFrame)}`, fg: error ? TOKENS.error : TOKENS.textMuted }),
    error ? h("text", { content: " 请检查 python -m src.bridge 可启动；按任意键重试", fg: TOKENS.textDim }) : null
  )
}
