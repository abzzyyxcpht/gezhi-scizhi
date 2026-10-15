// Diff 视图：统一/分屏随宽度切换 + 行级增删背景色
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { normalizeAppearance } from "../theme/preferences.js"

const h = React.createElement

/**
 * @param diff  unified diff 文本（--- a/file / +++ b/file / @@ ... @@）
 * @param width 可用宽度：>100 列分屏，否则统一
 */
export function DiffView({ diff, width = 80, appearance = {} }) {
  if (!diff) return null
  const preferences = normalizeAppearance(appearance)
  const view = width > 100 ? "split" : "unified"
  return h("diff", {
    diff,
    view,
    fg: TOKENS.diffContext,
    filetype: "diff",
    showLineNumbers: preferences.code_show_line_numbers,
    lineNumberFg: TOKENS.diffLineNumber,
    contextBg: TOKENS.background,
    contextContentBg: TOKENS.background,
    lineNumberBg: TOKENS.panel,
    addedBg: TOKENS.diffAddedBg,
    removedBg: TOKENS.diffRemovedBg,
    addedSignColor: TOKENS.diffAdded,
    removedSignColor: TOKENS.diffRemoved,
    wrapMode: preferences.code_wrap_long_lines ? "char" : "none",
  })
}
