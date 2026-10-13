// 品牌头 —— 双模式徽标 + shimmer 微光（设计系统组件）
import React, { useEffect, useState } from "react"
import { TOKENS, MODE_TOKENS, modeColor } from "../theme/tokens.js"
import { shimmerText } from "../motion/blend.js"

const h = React.createElement

/**
 * 品牌头。
 * @param workspaceMode "general" | "research"
 * @param shimmerFrame 非 null 时播放 shimmer（外部以 80ms 递增）
 * @param status 右侧状态文案
 */
export function BrandHeader({ workspaceMode = "general", shimmerFrame = null, status = "" }) {
  const t = MODE_TOKENS[workspaceMode] || MODE_TOKENS.general
  const color = modeColor(TOKENS, workspaceMode)
  const label = `${t.symbol} ${t.label}`
  return h(
    "box",
    { flexDirection: "row", paddingLeft: 1, paddingRight: 1 },
    h("text", { content: " 格致 ", fg: TOKENS.accent, bold: true }),
    shimmerFrame == null
      ? h("text", { content: label, fg: color, bold: true })
      : shimmerText(label, shimmerFrame, { accent: color, base: TOKENS.text }).map((s, i) =>
          h("text", { key: i, content: s.ch, fg: s.fg, bold: true })
        ),
    h("text", { content: " SciZhi ", fg: TOKENS.textMuted }),
    status ? h("text", { content: `· ${status}`, fg: TOKENS.textMuted }) : null
  )
}

/** shimmer 帧驱动 hook：开启动效时按 80ms 递增，调用方控制 start/stop */
export function useShimmerFrame(active, frameMs = 80) {
  const [frame, setFrame] = useState(0)
  useEffect(() => {
    if (!active) return undefined
    setFrame(0)
    const id = setInterval(() => setFrame((f) => f + 1), frameMs)
    return () => clearInterval(id)
  }, [active, frameMs])
  return active ? frame : null
}
