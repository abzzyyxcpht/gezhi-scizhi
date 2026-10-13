// Phase 3 帧存档：slash 补全面板 + Ctrl+P 命令面板（真对话壳）
// 运行: node src/opentui/spike/capture-phase3.js
import { mkdirSync, writeFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import { OpenTuiApp } from "../runtime.js"
import { createSessionController } from "../session-controller.js"

const here = dirname(fileURLToPath(import.meta.url))
const outDir = join(here, "..", "..", "..", "..", "design-previews", "2026-10-09-opentui-phase3")
mkdirSync(outDir, { recursive: true })

function fakeBridge() {
  return {
    calls: [],
    connection: () => ({ mode: "local" }),
    whenReady: async () => true,
    async call(req) {
      this.calls.push(req.cmd)
      if (req.cmd === "workbench_mode") return { ok: true, mode: req.mode || "research", revision: 2 }
      if (req.cmd === "agent_launch") return { ok: true, result: { session_id: "s-001" } }
      if (req.cmd === "agent_launch_status") return { ok: true, pending: null }
      if (req.cmd === "session_snapshot") return { ok: true, snapshot: { session_id: "s-001", status: "done", agent: "research" } }
      if (req.cmd === "session_events") return { ok: true, events: [{ kind: "assistant_message", payload: { text: "已完成文献梳理，生成 3 条候选选题" } }, { kind: "completed", payload: { status: "done" } }] }
      return { ok: true }
    },
  }
}

async function stepUntil(setup, predicate, { maxSteps = 200, settleMs = 12 } = {}) {
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

const bridge = fakeBridge()
const controller = createSessionController({ bridge })
const setup = await testRender(React.createElement(OpenTuiApp, { reducedMotion: true, controller }), { width: 100, height: 26 })
const { mockInput } = setup

await stepUntil(setup, (f) => f.includes("格致"))
writeFileSync(join(outDir, "1-startup.txt"), setup.captureCharFrame(), "utf-8")

// 鼠标 hover 面板行（Phase 5）：空草稿直接开 Ctrl+P，hover 首个候选行
await React.act(async () => {
  mockInput.pressKey("p", { ctrl: true })
})
const NL = String.fromCharCode(10)
await stepUntil(setup, (f) => f.includes("搜索："))
const plines = setup.captureCharFrame().split(NL)
const hy = plines.findIndex((l) => l.includes("搜索：")) + 1
await React.act(async () => {
  await setup.mockMouse.moveTo(4, hy + 2)
})
await stepUntil(setup, (f) => f.split(NL)[hy + 2]?.includes("❯ /"), { maxSteps: 30 })
writeFileSync(join(outDir, "2-palette-hover.txt"), setup.captureCharFrame(), "utf-8")
await React.act(async () => {
  mockInput.pressEscape()
})

// slash 补全
await React.act(async () => {
  await mockInput.typeText("/mo")
})
await stepUntil(setup, (f) => f.includes("/mode"))
writeFileSync(join(outDir, "2-slash-suggest.txt"), setup.captureCharFrame(), "utf-8")

// Ctrl+P 命令面板（中文搜索）
await React.act(async () => {
  mockInput.pressKey("p", { ctrl: true })
})
await stepUntil(setup, (f) => f.includes("命令面板"))
writeFileSync(join(outDir, "3-palette.txt"), setup.captureCharFrame(), "utf-8")
await React.act(async () => {
  await mockInput.typeText("模式")
})
await stepUntil(setup, (f) => f.includes("搜索：模式"))
writeFileSync(join(outDir, "4-palette-zh-search.txt"), setup.captureCharFrame(), "utf-8")

// 真对话：agent 会话事件渲染（先清空草稿与面板）
await React.act(async () => {
  mockInput.pressEscape()
  mockInput.pressBackspace()
  mockInput.pressBackspace()
  mockInput.pressBackspace()
})
await React.act(async () => {
  await mockInput.typeText("帮我调研这个课题")
})
await React.act(async () => {
  mockInput.pressEnter()
})
await stepUntil(setup, (f) => f.includes("已完成文献梳理"), { maxSteps: 300 })
writeFileSync(join(outDir, "5-agent-conversation.txt"), setup.captureCharFrame(), "utf-8")

// 科研步骤面板（创新项）
await React.act(async () => {
  await mockInput.typeText("/steps")
})
await React.act(async () => {
  mockInput.pressEnter()
})
await stepUntil(setup, (f) => f.includes("科研步骤"))
writeFileSync(join(outDir, "7-research-steps.txt"), setup.captureCharFrame(), "utf-8")
await React.act(async () => {
  mockInput.pressEscape()
})
await new Promise((r) => setTimeout(r, 200))
await setup.renderOnce()

setup.renderer.destroy()
console.error(`phase3 frames saved -> ${outDir}`)
