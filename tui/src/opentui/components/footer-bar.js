// Footer 双行状态栏 —— 渲染层（数据逻辑在 layout/footer.js）
// 状态栏按内容和提示分层：
//   行 1 = 槽位徽标 + 右侧技巧轮转（Ctrl+P 等快捷键）+ expand 提示（xs 层级、右对齐）；
//   行 2 = 左侧预警（semantic）+ 右侧上下文占用 badge（紧凑数字 K/M）。
import React, { useEffect, useState } from "react"
import { TOKENS, MODE_TOKENS, modeColor } from "../theme/tokens.js"
import { SYMBOLS, HINTS, shortcutHint } from "../theme/symbols.js"
import { typeStyle } from "../theme/ui-type.js"
import { compactNumber } from "./ui-parts.js"
import { createTipRotator, resolveSlots, formatContextUsage, resolveWarning, clipSlot } from "../layout/footer.js"

const h = React.createElement

/** xs 层级（快捷键/徽标/极弱元数据） */
const TIP_STYLE = typeStyle("xs")
const HINT_STYLE = typeStyle("xs")

/**
 * 行 1：可配置槽位 + 技巧轮转 + expand 提示
 * @param slotValues {mode, model, tasks, cwd, git}
 * @param tips [{text, weight}] 技巧表（文案经 shortcutHint 键帽化）
 * @param tickMs 当前 tick（外部时钟注入，测试可固定）
 */
export function FooterTop({ slotValues = {}, tips = [], tickMs = 0, expanded = false, workspaceMode = "general" }) {
  const rot = React.useMemo(() => createTipRotator(tips), [tips])
  const tipIdx = tips.length ? rot.indexAt(tickMs) : -1
  const slots = resolveSlots(slotValues)
  return h(
    "box",
    { flexDirection: "row", paddingLeft: 1, paddingRight: 1, backgroundColor: TOKENS.panel },
    ...slots.map(({ slot, value }) =>
      h("text", { key: slot, content: `[${clipSlot(value, slot === "mode" ? 10 : 16)}] `, fg: slot === "mode" ? modeColor(TOKENS, workspaceMode) : TOKENS.textMuted })
    ),
    h("box", { flexGrow: 1 }),
    tipIdx >= 0 ? h("text", { content: `${shortcutHint(tips[tipIdx].text)}  `, fg: TIP_STYLE.tier === "textDim" ? TOKENS.textDim : TOKENS.textMuted }) : null,
    h("text", { content: expanded ? HINTS.collapse : HINTS.expand, fg: HINT_STYLE.tier === "textDim" ? TOKENS.textDim : TOKENS.textMuted })
  )
}

/** 行 2：左侧预警 + 右侧上下文占用 */
export function FooterBottom({ used = null, total = null, warningCtx = {} }) {
  const warning = resolveWarning(warningCtx)
  const known = Number.isFinite(used) && used >= 0 && Number.isFinite(total) && total > 0
  const context = known ? `ctx ${Math.round((used / total) * 100)}% · ${compactNumber(used)}/${compactNumber(total)} · 估算` : "ctx 暂无数据"
  return h(
    "box",
    { flexDirection: "row", paddingLeft: 1, paddingRight: 1, backgroundColor: TOKENS.panel },
    warning ? h("text", { content: `${SYMBOLS.banner} ${warning}`, fg: TOKENS.warning }) : h("text", { content: " " }),
    h("box", { flexGrow: 1 }),
    h("text", { content: context, fg: TOKENS.textMuted })
  )
}

/** tick 时钟 hook（1s 步进；reducedMotion 下也照常，纯信息轮转） */
export function useTick(stepMs = 1000) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + stepMs), stepMs)
    return () => clearInterval(id)
  }, [stepMs])
  return tick
}
