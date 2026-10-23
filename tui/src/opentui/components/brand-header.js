// 品牌头 —— 双模式徽标 + shimmer 微光（设计系统组件）
import React, { useEffect, useState } from "react"
import stringWidth from "string-width"
import { TOKENS, MODE_TOKENS, modeColor } from "../theme/tokens.js"
import { shimmerText } from "../motion/blend.js"

const h = React.createElement
const graphemes = new Intl.Segmenter(undefined, { granularity: "grapheme" })

function headerLayout({ workspaceMode = "general", status = "", width = 80 }) {
  const inner = Math.max(1, width - 2)
  const mode = MODE_TOKENS[workspaceMode] || MODE_TOKENS.general
  const label = `${mode.symbol} ${mode.label}`
  const latin = width >= 55 ? " SciZhi " : " "
  const base = ` 格致 ${label}${latin}`
  const inline = !status || stringWidth(`${base}· ${status}`) <= inner
  const statusLines = []
  if (status && !inline) {
    let line = "", used = 0
    for (const { segment } of graphemes.segment(status)) {
      const size = stringWidth(segment)
      if (line && used + size > inner) { statusLines.push(line); line = ""; used = 0 }
      line += segment; used += size
    }
    if (line) statusLines.push(line)
  }
  return { inner, label, latin, inline, statusLines }
}

export function brandHeaderRows(options) { return 1 + headerLayout(options).statusLines.length }

/**
 * 品牌头。
 * @param workspaceMode "general" | "research"
 * @param shimmerFrame 非 null 时播放 shimmer（外部以 80ms 递增）
 * @param status 右侧状态文案
 */
export function BrandHeader({ workspaceMode = "general", shimmerFrame = null, status = "", width = 80 }) {
  const color = modeColor(TOKENS, workspaceMode)
  const { inner, label, latin, inline, statusLines } = headerLayout({ workspaceMode, status, width })
  return h(
    "box",
    { flexDirection: "column", width, paddingLeft: 1, paddingRight: 1, flexShrink: 0 },
    h("text", { width: inner },
      h("span", { fg: TOKENS.accent, bold: true }, " 格致 "),
      shimmerFrame == null
        ? h("span", { fg: color, bold: true }, label)
        : shimmerText(label, shimmerFrame, { accent: color, base: TOKENS.text }).map((s, i) =>
            h("span", { key: i, fg: s.fg, bold: true }, s.ch)),
      h("span", { fg: TOKENS.textMuted }, latin),
      status && inline ? h("span", { fg: TOKENS.textMuted }, `· ${status}`) : null),
    ...statusLines.map((line, i) => h("text", { key: `status-${i}`, content: line, width: inner, fg: TOKENS.textMuted }))
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
