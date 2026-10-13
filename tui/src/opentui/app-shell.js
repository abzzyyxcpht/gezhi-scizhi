// 应用壳 —— Phase 2 布局总装：品牌头 / 会话区 / 侧栏 / composer / footer
// 数据全部走 props（单一受控组件），交互语义通过回调上抛，保证可无终端测试
import React from "react"
import { TOKENS, MODE_TOKENS, modeColor } from "./theme/tokens.js"
import { SYMBOLS } from "./theme/symbols.js"
import { contentWidthForTerminal, sidebarLayoutForTerminal, clipToWidth } from "./layout/width-budget.js"
import { BrandHeader } from "./components/brand-header.js"
import { Sidebar } from "./components/sidebar.js"
import { FooterTop, FooterBottom } from "./components/footer-bar.js"
import { TranscriptView, chromeRows } from "./components/transcript.js"
import { EmptyTranscript } from "./components/empty-transcript.js"
import { StartupScreen } from "./components/startup-screen.js"
import { brailleFrame } from "./motion/frames.js"

const h = React.createElement

/** 草稿 + 光标渲染：光标处插入方块符（空草稿显示 lone 光标） */
export function draftWithCursor(draft = "", cursor = 0) {
  const chars = [...draft]
  const c = Math.min(Math.max(0, cursor), chars.length)
  if (!chars.length) return "▋"
  return chars.slice(0, c).join("") + "▋" + chars.slice(c).join("")
}

/**
 * @param state {
 *   workspaceMode, terminalWidth, terminalHeight,
 *   lines:[{text, dim?}], spinner:{active, frame}, draft, status,
 *   sidebar:{hidden, sections}, slotValues, tips, used, total,
 *   shimmerFrame, reducedMotion,
 * }
 */
export function AppShell({ state = {} }) {
  const {
    workspaceMode = "general",
    terminalWidth = 120,
    lines = [],
    spinner = { active: false, frame: 0 },
    draft = "",
    status = "",
    sidebar = {},
    slotValues = {},
    tips = [],
    used = 0,
    total = 68000,
    shimmerFrame = null,
    reducedMotion = false,
  } = state

  const sidebarLayout = sidebarLayoutForTerminal(terminalWidth, { userHidden: sidebar.hidden })
  const contentWidth = contentWidthForTerminal(terminalWidth, { sidebarVisible: sidebarLayout === "docked" })
  const color = state.shellMode ? TOKENS.shellMode : modeColor(TOKENS, workspaceMode)
  void contentWidth // Phase 2 末期接入逐面板高预算；先保证单一推导入口被消费

  if (state.booting) {
    return h(
      "box",
      { width: "100%", height: "100%", backgroundColor: TOKENS.background },
      h(StartupScreen, { workspaceMode, spinnerFrame: state.spinnerFrame ?? 0, shimmerFrame: state.shimmerFrame ?? 0, error: state.bootError })
    )
  }

  return h(
    "box",
    { width: "100%", height: "100%", flexDirection: "row", backgroundColor: TOKENS.background },
    h(
      "box",
      { flexDirection: "column", flexGrow: 1, minWidth: 40 },
      h(BrandHeader, { workspaceMode, shimmerFrame, status }),
      state.drill
        ? state.drill
        : h(TranscriptView, {
        lines,
        height: Math.max(1, (state.terminalHeight ?? 24) - chromeRows(state.panelRows ?? 0, draftWithCursor(draft, state.cursor ?? 0).split("\n").length)),
        offset: state.scrollOffset ?? 0,
        width: contentWidthForTerminal(state.terminalWidth ?? 80, { sidebarVisible: sidebarLayout === "docked" }),
        onScroll: state.onScroll,
            emptyArt: state.emptyArt !== false
              ? h(EmptyTranscript, { workspaceMode, shimmerFrame: state.shimmerFrame ?? null, width: contentWidthForTerminal(state.terminalWidth ?? 80, { sidebarVisible: sidebarLayout === "docked" }) })
              : null,
          }
        ),
      h(
        "box",
        { flexDirection: "row", paddingLeft: 1 },
        spinner.active
          ? h("text", { content: `${brailleFrame(spinner.frame)}`, fg: color })
          : h("text", { content: `${SYMBOLS.bullet}`, fg: TOKENS.success }),
        h("text", { content: " 就绪 ", fg: TOKENS.textMuted })
      ),
      h(
        "box",
        {
          border: true,
          borderStyle: "rounded",
          borderColor: color,
          title: state.shellMode ? " $ shell · 输入 " : ` ${MODE_TOKENS[workspaceMode]?.symbol ?? "◇"} ${MODE_TOKENS[workspaceMode]?.label ?? "通用"} · 输入 `,
          paddingLeft: 1,
          paddingRight: 1,
          height: Math.max(3, draftWithCursor(draft, state.cursor ?? 0).split("\n").length + 2),
          flexShrink: 0,
        },
        h("text", {
          content: draftWithCursor(draft, state.cursor ?? 0)
            .split("\n")
            .map((line, i) => clipToWidth(i === 0 ? `❯ ${line}` : `  ${line}`, Math.max(8, contentWidth - 4)))
            .join("\n"),
          fg: TOKENS.text,
        })
      ),
      state.panel ? h("box", { marginTop: 1, flexShrink: 0 }, state.panel) : null,
      h(FooterTop, { slotValues, tips, tickMs: state.tickMs ?? 0, workspaceMode, expanded: state.expanded }),
      h(FooterBottom, { used, total, warningCtx: state.warningCtx })
    ),
    sidebarLayout === "docked" ? h(Sidebar, { terminalWidth, workspaceMode, hidden: false, sections: sidebar.sections }) : null
  )
}
