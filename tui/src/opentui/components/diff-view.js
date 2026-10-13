// Diff 视图（ZCode 对标）：统一/分屏随宽度切换 + 行级增删背景色
import React from "react"
import { TOKENS } from "../theme/tokens.js"

const h = React.createElement

/**
 * @param diff  unified diff 文本（--- a/file / +++ b/file / @@ ... @@）
 * @param width 可用宽度：>100 列分屏，否则统一（对齐 ZCode 宽度策略）
 */
export function DiffView({ diff, width = 80 }) {
  if (!diff) return null
  const view = width > 100 ? "split" : "unified"
  return h("diff", {
    diff,
    view,
    fg: TOKENS.text,
    filetype: "diff",
    showLineNumbers: true,
    lineNumberFg: TOKENS.textDim,
    addedBg: TOKENS.diffAddedBg,
    removedBg: TOKENS.diffRemovedBg,
    addedSignColor: TOKENS.diffAdded,
    removedSignColor: TOKENS.diffRemoved,
    wrapMode: "none",
  })
}
