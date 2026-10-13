// OpenTUI 设计系统纯函数测试（无终端）
// 运行: node test/opentui-design.js
import { TOKENS, LIGHT_TOKENS, MODE_TOKENS, paletteFor, modeColor } from "../src/opentui/theme/tokens.js"
import { applyTheme, currentTheme, detectTheme } from "../src/opentui/theme/theme.js"
import { SYMBOLS, BORDER, HINTS } from "../src/opentui/theme/symbols.js"
import { blendHex, shimmerText } from "../src/opentui/motion/blend.js"
import {
  BRAILLE,
  SWITCH_FRAMES,
  MODE_TRANSITION_MS,
  transitionPlan,
  staggerDelayMs,
  brailleFrame,
} from "../src/opentui/motion/frames.js"
import {
  contentWidthForTerminal,
  sidebarLayoutForTerminal,
  truncateHeader,
  SIDEBAR_WIDTH,
  WIDE_BREAKPOINT,
  FALLBACK_TERMINAL_WIDTH,
  MIN_CONTENT_WIDTH,
} from "../src/opentui/layout/width-budget.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}

// tokens
assert(TOKENS.modeGeneral !== TOKENS.modeResearch, "双模式品牌色可区分")
assert(paletteFor("light") !== TOKENS && paletteFor("light").background === LIGHT_TOKENS.background, "paletteFor 返回浅色系副本")
assert(modeColor(TOKENS, "research") === TOKENS.modeResearch, "modeColor 科研紫")
assert(modeColor(TOKENS, "bogus") === TOKENS.modeGeneral, "modeColor 非法值回退通用")
assert(MODE_TOKENS.general.symbol === "◇" && MODE_TOKENS.research.symbol === "◎", "模式符号契约")

// symbols
assert(SYMBOLS.bullet === "●" && SYMBOLS.switchPending === "◌", "符号表关键项")
assert(BORDER.rounded === "rounded" && BORDER.single === "single", "边框风格枚举")
assert(HINTS.navigate.includes("↑↓"), "hint 词汇表（ZCode 键帽化）")

// blend / shimmer
assert(blendHex("#000000", "#ffffff", 0.5) === "#808080", "blendHex 中点")
assert(blendHex("#000000", "#ffffff", 2) === "#ffffff", "blendHex 钳位上限")
const sh0 = shimmerText("格致科研", 0)
assert(sh0.length === 4 && sh0.every((s) => s.fg.startsWith("#")), "shimmer CJK 逐字符")
const shCycle = shimmerText("abc", 64)
assert(JSON.stringify(shCycle.map((s) => s.fg)) === JSON.stringify(shimmerText("abc", 0).map((s) => s.fg)), "shimmer 周期循环")

// frames
assert(BRAILLE.length === 10 && brailleFrame(10) === BRAILLE[0] && brailleFrame(-1) === BRAILLE[9], "盲文帧循环（负索引安全）")
assert(SWITCH_FRAMES.join("") === "·◌●✓" && MODE_TRANSITION_MS === 320, "切换回执帧与时长契约")
const p0 = transitionPlan(0)
const pHalf = transitionPlan(0.5)
const p1 = transitionPlan(1)
assert(p0.logoCrossfade === 0 && p0.done === false, "transitionPlan 起点")
assert(pHalf.logoCrossfade === 1 && pHalf.commandStagger < 1, "transitionPlan 中段：logo 完成、stagger 进行中")
assert(p1.done === true && p1.commandStagger === 1, "transitionPlan 终点")
assert(staggerDelayMs(5) === 150, "stagger 递延")

// width budget
assert(contentWidthForTerminal(120, { sidebarVisible: true }) === 120 - SIDEBAR_WIDTH - 4, "宽终端停靠侧栏内容宽")
assert(contentWidthForTerminal(NaN) === FALLBACK_TERMINAL_WIDTH - 4, "非法宽度兜底 80 列")
assert(contentWidthForTerminal(5) === MIN_CONTENT_WIDTH, "内容宽下限 8")
assert(sidebarLayoutForTerminal(WIDE_BREAKPOINT) === "docked", "120 列停靠")
assert(sidebarLayoutForTerminal(100) === "overlay", "窄终端 overlay")
assert(sidebarLayoutForTerminal(200, { userHidden: true }) === "hidden", "用户隐藏优先")
const tr = truncateHeader({ label: "Run", detail: "a".repeat(50), chip: "0s" }, 20)
assert(tr.detail.endsWith("…") && tr.label.length + 1 + tr.detail.length + 1 + tr.chip.length <= 20, "header 截断保 chip 且整行不越宽")

// 主题热切换
applyTheme("dark")
assert(TOKENS.background === "#0f1419" && currentTheme() === "dark", "applyTheme 深色（ZCode 终端主题 defaults.ts）")
applyTheme("light")
assert(TOKENS.background === LIGHT_TOKENS.background && TOKENS.modeGeneral === LIGHT_TOKENS.modeGeneral, "applyTheme 浅色（原地热切换）")
const light = await detectTheme({ waitForThemeMode: async () => "light" })
assert(light === "light" && TOKENS.background === LIGHT_TOKENS.background, "detectTheme 采信 renderer 探测")
const dark = await detectTheme({ waitForThemeMode: async () => { throw new Error("no osc11") } })
assert(dark === "dark", "探测失败兜底深色")
const cfg = await detectTheme({ waitForThemeMode: async () => null })
assert(cfg === "dark", "探测返回 null 兜底深色")
applyTheme("dark")

if (failures === 0) {
  console.error("opentui-design: ALL_PASS")
  process.exit(0)
}
console.error(`opentui-design: FAILURES=${failures}`)
process.exit(1)
