// 空会话中央 logo —— 双模式 ASCII 艺术 + shimmer 微光
// 窄终端契约：所有行按可用宽度裁剪（clipToWidth），10 列也不横向溢出。
import React from "react"
import { TOKENS, MODE_TOKENS, modeColor } from "../theme/tokens.js"
import { shimmerText } from "../motion/blend.js"
import { clipToWidth } from "../layout/width-budget.js"

const h = React.createElement

// 双模式logo：通用=开放网格，科研=聚焦格点（6 行块字符画，宽 24）
const LOGOS = {
  general: [
    "◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇",
    "◇ ◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇ ◆",
    "◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇",
    "◇ ◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇ ◆",
    "◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇ ◆ ◇",
    "    格 致 · 通 用      ",
  ],
  research: [
    "· · · · ◎ · · · · · ·",
    "· · · ◎ ◎ ◎ · · · · ·",
    "· · ◎ ◎ ◎ ◎ ◎ · · · ·",
    "◎ ◎ ◎ ◎ ◎ ◎ ◎ ◎ ◎ ◎",
    "· · · ◎ ◎ ◎ ◎ ◎ · · ·",
    "    格 致 · 科 研      ",
  ],
}

export function EmptyTranscript({ workspaceMode = "general", shimmerFrame = null, width = 80 }) {
  const t = MODE_TOKENS[workspaceMode] || MODE_TOKENS.general
  const color = modeColor(TOKENS, workspaceMode)
  const art = LOGOS[workspaceMode] || LOGOS.general
  const pad = " ".repeat(Math.max(0, Math.floor((width - 24) / 2)))
  const limit = Math.max(8, width)
  return h(
    "box",
    { flexDirection: "column", flexGrow: 1, justifyContent: "center", alignItems: "center" },
    ...art.map((row, i) => {
      const isTitle = i === art.length - 1
      const text = clipToWidth(`${pad}${row}`, limit)
      if (shimmerFrame == null || isTitle) {
        return h("text", { key: i, content: text, fg: isTitle ? color : TOKENS.textDim })
      }
      return h(
        "box",
        { key: i, flexDirection: "row" },
        ...shimmerText(text, shimmerFrame + i * 2, { accent: color, base: TOKENS.textDim }).map((s, ci) =>
          h("text", { key: ci, content: s.ch, fg: s.fg })
        )
      )
    }),
    h("text", { content: clipToWidth(`${pad}${t.symbol} ${t.label}模式 · 输入消息开始`, limit), fg: TOKENS.textMuted }),
    workspaceMode === "research"
      ? h("text", { content: clipToWidth(`${pad}/steps 选择科研步骤（文献扫描 · 证据评审 · 完整流水线）`, limit), fg: TOKENS.modeResearch })
      : h("text", { content: clipToWidth(`${pad}/ 触发命令补全 · @ 引用文件 · Ctrl+P 命令面板`, limit), fg: TOKENS.textMuted })
  )
}
