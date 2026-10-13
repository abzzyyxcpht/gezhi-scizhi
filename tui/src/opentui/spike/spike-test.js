// Phase 0 Spike 无终端验证：切换过渡序列 + shimmer 品牌头 + 降级路径
// 运行: node src/opentui/spike/spike-test.js
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import { SpikeApp, shimmerText, blendHex, MODES } from "./spike-app.js"

const log = (m) => console.error(`[spike-test] ${m}`)
let failures = 0
const assert = (cond, name) => {
  if (cond) log(`PASS ${name}`)
  else {
    log(`FAIL ${name}`)
    failures++
  }
}

// 手动步进：act 内推进真实定时器 + 渲染一帧，直到谓词满足或步数耗尽
async function stepUntil(setup, predicate, { maxSteps = 100, settleMs = 15 } = {}) {
  let frame = setup.captureCharFrame()
  for (let i = 0; i < maxSteps; i++) {
    await React.act(async () => {
      await new Promise((r) => setTimeout(r, settleMs))
    })
    await setup.renderOnce()
    frame = setup.captureCharFrame()
    if (predicate(frame)) return frame
  }
  return null
}

// --- 纯函数断言 ---
const blended = blendHex("#000000", "#ffffff", 0.5)
assert(blended === "#808080", `blendHex 中点 (${blended})`)
const shimmer = shimmerText("科研", 0)
assert(shimmer.length === 2 && shimmer.every((s) => s.fg.startsWith("#")), "shimmerText 逐字符产色")
assert(MODES.general.symbol === "◇" && MODES.research.symbol === "◎", "双模式符号契约")

// --- 组件级断言（test renderer + act 环境，不起终端）---
let toggle = () => {}
const setup = await testRender(React.createElement(SpikeApp, { onRegisterToggle: (fn) => (toggle = fn) }), {
  width: 80,
  height: 20,
  targetFps: 30,
})

let frame = await stepUntil(setup, (f) => f.includes("格致"))
assert(frame !== null, "品牌头渲染")
assert(frame.includes("◇") && frame.includes("通用"), "初始为通用模式")
assert(frame.includes("context: 12%"), "状态行上下文占位")

React.act(() => toggle()) // Ctrl+G 语义：触发切换过渡
frame = await stepUntil(setup, (f) => f.includes("已切换到科研模式"), { maxSteps: 80 })
assert(frame !== null, "切换回执完成文案")
assert(frame.includes("模式切换完成"), "会话区记录切换")
assert(frame.includes("◎") && frame.includes("科研"), "品牌头已是科研模式")

// 降级路径：reducedMotion 下直接换模式无动画中间态
let toggle2 = () => {}
const setup2 = await testRender(
  React.createElement(SpikeApp, { reducedMotion: true, onRegisterToggle: (fn) => (toggle2 = fn) }),
  { width: 80, height: 20 }
)
await stepUntil(setup2, (f) => f.includes("格致"))
React.act(() => toggle2())
const frame2 = await stepUntil(setup2, (f) => f.includes("已切换到科研模式"), { maxSteps: 20 })
assert(frame2 !== null && !frame2.includes("切换中"), "reducedMotion 直接切换（无过渡中间态）")

setup.renderer.destroy()
setup2.renderer.destroy()
log(failures === 0 ? "ALL_PASS" : `FAILURES=${failures}`)
process.exit(failures === 0 ? 0 : 1)
