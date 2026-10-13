// 保存 spike 三态字符帧到预览目录（供存档/对比，颜色见 GUI 截图）
// 运行: node src/opentui/spike/capture-frames.js
import { mkdirSync, writeFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import { SpikeApp } from "./spike-app.js"

const here = dirname(fileURLToPath(import.meta.url))
const outDir = join(here, "..", "..", "..", "..", "design-previews", "2026-10-08-opentui-spike")
mkdirSync(outDir, { recursive: true })

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

let toggle = () => {}
const setup = await testRender(React.createElement(SpikeApp, { onRegisterToggle: (fn) => (toggle = fn) }), {
  width: 72,
  height: 18,
  targetFps: 30,
})
await stepUntil(setup, (f) => f.includes("格致"))
writeFileSync(join(outDir, "frame-1-general.txt"), setup.captureCharFrame(), "utf-8")

React.act(() => toggle())
await stepUntil(setup, (f) => f.includes("切换中") || f.includes("◎"), { maxSteps: 10 })
writeFileSync(join(outDir, "frame-2-switching.txt"), setup.captureCharFrame(), "utf-8")

await stepUntil(setup, (f) => f.includes("已切换到科研模式"), { maxSteps: 80 })
writeFileSync(join(outDir, "frame-3-research.txt"), setup.captureCharFrame(), "utf-8")

setup.renderer.destroy()
console.error(`frames saved -> ${outDir}`)
