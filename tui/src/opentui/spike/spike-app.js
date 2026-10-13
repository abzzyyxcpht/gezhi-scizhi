// Phase 0 Spike：双模式最小壳 —— 品牌头 shimmer + 圆角输入框 + spinner + 模式切换过渡
// 运行（真终端）: node src/opentui/spike/spike-app.js
// 验证（无终端）: node src/opentui/spike/spike-test.js
import React, { useEffect, useRef, useState } from "react"
import { createRoot, useKeyboard, useRawMouse } from "@mbears/opentui-react"
import { TOKENS, MODE_TOKENS, modeColor } from "../theme/tokens.js"
import { blendHex, shimmerText } from "../motion/blend.js"
import { BRAILLE, SWITCH_FRAMES, brailleFrame } from "../motion/frames.js"

// 兼容导出：spike 期对外的 TOKENS/MODES/blendHex 等从设计系统转发，保持单一来源
export { TOKENS, blendHex, shimmerText }
export const MODES = {
  general: { label: MODE_TOKENS.general.label, symbol: MODE_TOKENS.general.symbol, color: modeColor(TOKENS, "general") },
  research: { label: MODE_TOKENS.research.label, symbol: MODE_TOKENS.research.symbol, color: modeColor(TOKENS, "research") },
}

export function SpikeApp({ reducedMotion = false, onRegisterToggle, onExit } = {}) {
  const [mode, setMode] = useState("general")
  const [switching, setSwitching] = useState(null) // {to, frame}
  const [spinnerFrame, setSpinnerFrame] = useState(0)
  const [status, setStatus] = useState("就绪 · Ctrl+G / Shift+Tab 切换模式，Ctrl+C 退出")
  const [draft, setDraft] = useState("")
  const [mouseMoves, setMouseMoves] = useState(0)
  const [lines, setLines] = useState(["欢迎使用格致 SciZhi（OpenTUI spike）", "试试 Ctrl+G 感受双模式丝滑切换"])
  const timerRef = useRef([])

  // 键盘路由（组件级）：Ctrl+C 退出 / Ctrl+G 与 Shift+Tab 切换
  useKeyboard((key) => {
    if (key.ctrl && key.name === "c") {
      if (onExit) onExit()
      return
    }
    if ((key.ctrl && key.name === "g") || (key.shift && key.name === "tab")) toggleRef.current()
  })

  // 鼠标管道验证：统计移动事件（GUI 验证用，Phase 5 换成真实 hover 语义）
  useRawMouse((event) => {
    const { type } = event.input || {}
    if (type === "move") setMouseMoves((n) => n + 1)
  })

  useEffect(() => {
    if (onRegisterToggle) onRegisterToggle(() => toggleRef.current())
    return undefined
  }, [onRegisterToggle])
  const toggleRef = useRef(() => {})

  useEffect(() => {
    if (reducedMotion) return
    const id = setInterval(() => setSpinnerFrame((f) => (f + 1) % BRAILLE.length), 80)
    timerRef.current.push(id)
    return () => clearInterval(id)
  }, [reducedMotion])

  // 切换过渡序列：乐观切换 + 4 帧回执 + 状态行（对齐现有 MODE_TRANSITION 语义）
  const changeMode = (to) => {
    if (to === mode || switching) return
    const play = (frame) => {
      if (frame >= SWITCH_FRAMES.length) {
        setSwitching(null)
        setStatus(`✓ 已切换到${MODES[to].label}模式`)
        setLines((ls) => [...ls, `${MODES[to].symbol} 模式切换完成（shimmer 品牌过渡已播放）`])
        return
      }
      setSwitching({ to, frame })
      timerRef.current.push(setTimeout(() => play(frame + 1), reducedMotion ? 0 : 80))
    }
    setMode(to)
    if (reducedMotion) {
      setStatus(`✓ 已切换到${MODES[to].label}模式`)
    } else {
      setStatus(`切换中… ${MODES[to].symbol}`)
      play(0)
    }
  }

  const toggle = () => changeMode(mode === "general" ? "research" : "general")
  toggleRef.current = toggle

  const m = MODES[mode]
  const title = switching ? ` ${MODES[switching.to].symbol} ${MODES[switching.to].label} · ${SWITCH_FRAMES[switching.frame]} ` : ` ${m.symbol} ${m.label}模式 `

  const h = React.createElement
  return h(
    "box",
    { width: "100%", height: "100%", flexDirection: "column", padding: 1, backgroundColor: TOKENS.background },
    // 品牌头：shimmer 扫过（切换期间帧推进）
    h(
      "box",
      { flexDirection: "row", marginBottom: 1 },
      h("text", { content: " 格致 ", fg: TOKENS.accent, bold: true }),
      ...(reducedMotion || !switching
        ? [h("text", { content: `${m.symbol} ${m.label}`, fg: m.color, bold: true })]
        : shimmerText(`${m.symbol} ${m.label}`, switching.frame * 3, { accent: m.color }).map((s, i) =>
            h("text", { key: i, content: s.ch, fg: s.fg, bold: true })
          )),
      h("text", { content: " SciZhi ", fg: TOKENS.textMuted }),
      h("text", { content: `· ${status}`, fg: TOKENS.textMuted })
    ),
    // 会话区
    h(
      "box",
      { flexDirection: "column", flexGrow: 1, paddingLeft: 2, paddingRight: 2 },
      ...lines.map((l, i) => h("text", { key: i, content: l, fg: i === lines.length - 1 ? TOKENS.text : TOKENS.textMuted }))
    ),
    // 状态行：spinner + 上下文占位
    h(
      "box",
      { flexDirection: "row", marginTop: 1 },
      h("text", { content: reducedMotion ? "●" : `${BRAILLE[spinnerFrame]}`, fg: m.color }),
      h("text", { content: " 就绪 ", fg: TOKENS.textMuted }),
      h("text", { content: "context: 12% (8.2k/68k)", fg: TOKENS.textMuted })
    ),
    // 输入框：边框色随模式过渡（spike 直接换色；Phase 3 上渐变）
    h(
      "box",
      {
        border: true,
        borderStyle: "rounded",
        borderColor: m.color,
        title,
        paddingLeft: 1,
        paddingRight: 1,
        marginTop: 1,
      },
      h("text", { content: draft || " ", fg: TOKENS.text })
    ),
    h("box", { flexDirection: "row", marginTop: 1 }, h("text", { content: `Ctrl+G 切换 · Shift+Tab 循环 · Ctrl+C 退出 · 鼠标移动 ${mouseMoves} 次`, fg: TOKENS.textMuted }))
  )
}

// ---- 真终端入口 ----
export async function runSpike({ stdin, stdout } = {}) {
  const { createCliRenderer } = await import("@mbears/opentui-core")
  const reducedMotion = !!process.env.GEZHI_REDUCED_MOTION || !process.stdout.isTTY
  const renderer = await createCliRenderer({
    targetFps: 30,
    useMouse: true,
    enableMouseMovement: true,
    exitOnCtrlC: false,
    stdin: stdin || process.stdin,
    stdout: stdout || process.stdout,
  })

  let toggleMode = () => {}
  const root = createRoot(renderer)
  root.render(
    React.createElement(SpikeApp, {
      reducedMotion,
      onRegisterToggle: (fn) => (toggleMode = fn),
      onExit: () => {
        renderer.destroy()
        process.exit(0)
      },
    })
  )

  // Ctrl+C 兜底（组件内 useKeyboard 之外再保险一层）
  renderer.keyHandler?.on("keypress", (key) => {
    if (key.ctrl && key.name === "c") {
      renderer.destroy()
      process.exit(0)
    }
  })
  return renderer
}

// 直接运行入口
if (import.meta.url === `file://${process.argv[1].replace(/\\/g, "/")}`) {
  runSpike().catch((err) => {
    console.error(`[spike] 启动失败: ${err.message}`)
    process.exit(1)
  })
}
