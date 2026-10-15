// 会话区视图 —— 手动视口裁剪（替代 fork 中有布局 bug 的 scrollbox，同时满足 Phase 7 性能要求）
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { clipWide } from "../../commands.js"
import { DiffView } from "./diff-view.js"
import { prepareTranscriptRows, markupSpans } from "./markup-lines.js"
import { terminalGeometry } from "../theme/preferences.js"
import { typeStyle } from "../theme/ui-type.js"

const h = React.createElement

// 宽度键控行缓存：长会话重复裁剪不重算
const clipCache = new Map()
const CLIP_CACHE_MAX = 4096
export function clipCached(text, width) {
  const key = `${width}|${text}`
  const hit = clipCache.get(key)
  if (hit !== undefined) return hit
  const value = clipWide(String(text ?? ""), Math.max(4, width))
  if (clipCache.size >= CLIP_CACHE_MAX) clipCache.clear()
  clipCache.set(key, value)
  return value
}

/**
 * 视口裁剪纯函数：sticky 吸底语义 + 滚轮偏移。
 * @param offset 0=吸底；>0=向上回看 offset 行
 */
export function windowLines(lines = [], height, offset = 0) {
  if (!Number.isFinite(height) || height <= 0) return lines.slice(-20)
  const size = Math.max(1, Math.floor(height))
  const maxOffset = Math.max(0, lines.length - size)
  const off = Math.min(maxOffset, Math.max(0, Math.floor(offset)))
  const end = lines.length - off
  return lines.slice(Math.max(0, end - size), end)
}

/** chrome 行数核算：品牌头 1 + spinner 1 + composer 3 + panel(含 margin) + footer 2 + 余量 2 */
export function chromeRows(panelRows = 0, draftRows = 1) {
  return 1 + 1 + Math.max(3, draftRows + 2) + (panelRows > 0 ? panelRows + 1 : 0) + 2 + 2
}

/** 滚轮增量钳制（offset ∈ [0, max(0, lines-height)]） */
export function clampScrollOffset(offset, total, height) {
  const size = Math.max(1, Math.floor(height))
  const max = Math.max(0, total - size)
  return Math.min(max, Math.max(0, Math.floor(offset)))
}

export function TranscriptView({ lines = [], height = 20, offset = 0, width = 80, onScroll, emptyArt, appearance = {} }) {
  const prepared = React.useMemo(() => prepareTranscriptRows(lines, width, appearance), [lines, width, appearance.code_show_line_numbers, appearance.code_wrap_long_lines])
  const rows = windowLines(prepared, height, offset)
  const base = typeStyle("ui_base")
  const scrolled = offset > 0
  const isEmpty = lines.length <= 1 && !scrolled
  return h(
    "box",
    {
      flexDirection: "column",
      flexGrow: 1,
      flexShrink: 1,
      paddingLeft: terminalGeometry(appearance.density).contentPadding,
      paddingRight: terminalGeometry(appearance.density).contentPadding,
      onMouseScroll: (event) => {
        const dir = event?.scroll?.direction || event?.direction || "down"
        onScroll?.(dir === "up" ? 3 : -3)
      },
    },
    isEmpty && emptyArt ? emptyArt : null,
    scrolled ? h("text", { content: `↑ 回看 ${offset} 行（滚轮向下回到底部）`, fg: TOKENS.textDim }) : null,
    ...rows.map((l, i) => {
      if (l.kind === "diff" && l.diff) {
        return h(
          "box",
          { key: `d${i}`, flexDirection: "column", paddingLeft: 1 },
          h("text", { content: clipCached(l.text, width), fg: TOKENS.textMuted }),
          h(DiffView, { diff: l.diff, width, appearance })
        )
      }
      if (l.user) {
        return h(
          "box",
          { key: `${i}-${l.text}`, backgroundColor: TOKENS.userMessage, paddingLeft: 1, paddingRight: 1 },
          h("text", { content: clipCached(l.text, Math.max(4, width - 2)), fg: TOKENS.text })
        )
      }
      return h("text", { key: `${i}-${l.text}`, fg: TOKENS[base.tier], bg: l.code ? TOKENS.codeBackground : undefined },
        l.gutter ? h("span", { fg: TOKENS.diffLineNumber }, l.gutter) : null,
        ...markupSpans(l).map((span, si) => h("span", { key: si, fg: TOKENS[span.token], bold: span.bold, bg: span.token === "markdownCode" ? TOKENS.markdownCodeBg : undefined }, span.text)))
    })
  )
}
