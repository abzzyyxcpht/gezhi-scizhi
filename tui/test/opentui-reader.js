// 全屏阅读器（/read）：纯函数滚动/搜索 + runtime 集成（开合/滚动/搜索/Esc）
// 运行: node test/opentui-reader.js
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import { OpenTuiApp } from "../src/opentui/runtime.js"
import { createSessionController } from "../src/opentui/session-controller.js"
import { clampOffset, jumpToMatch, matchLines, ReaderPanel } from "../src/opentui/components/reader.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}
const eq = (actual, expected, name) => assert(actual === expected, `${name}（实际 ${actual}，期望 ${expected}）`)
const deepEq = (actual, expected, name) =>
  assert(JSON.stringify(actual) === JSON.stringify(expected), `${name}（实际 ${JSON.stringify(actual)}）`)

async function stepUntil(setup, predicate, { maxSteps = 250, settleMs = 12 } = {}) {
  let frame = setup.captureCharFrame()
  for (let i = 0; i < maxSteps; i++) {
    await React.act(async () => { await new Promise((r) => setTimeout(r, settleMs)) })
    await setup.renderOnce()
    frame = setup.captureCharFrame()
    if (predicate(frame)) return frame
  }
  return null
}

// ---- 1) 纯函数：offset 收敛与搜索跳转 ----
eq(clampOffset(-5, 100, 10), 0, "负 offset 归零")
eq(clampOffset(95, 100, 10), 90, "offset 不超过 total-rows")
eq(clampOffset(50, 100, 10), 50, "区间内不变")
eq(clampOffset(10, 5, 0), 0, "无可见区时归零")
deepEq(matchLines(["abc", "xyz", "ABC"], "abc"), [0, 2], "大小写不敏感匹配")
deepEq(matchLines(["abc"], "  "), [], "空查询无命中")
eq(jumpToMatch(0, ["a", "b", "a"], "a", 1), 2, "n 跳到下一处")
eq(jumpToMatch(2, ["a", "b", "a"], "a", 1), 0, "末尾环绕到首处")
eq(jumpToMatch(2, ["a", "b", "a"], "a", -1), 0, "N 跳到上一处")
eq(jumpToMatch(0, ["a", "b", "a"], "a", -1), 2, "头部环绕到末处")
eq(jumpToMatch(5, ["a"], "zz", 1), 5, "无命中保持原位")

// ---- 2) 面板渲染（testRender 帧口径）----
const digestLines = Array.from({ length: 60 }, (_, i) => `第 ${i + 1} 行 · 摘要稿内容`)
const panel = await testRender(React.createElement(ReaderPanel, {
  title: "摘要稿", lines: digestLines, offset: 0, query: "", width: 90, height: 24,
}), { width: 90, height: 24, targetFps: 30 })
let frame = await stepUntil(panel, (f) => f.includes("摘要稿"))
assert(frame !== null, "面板渲染出帧")
assert(frame.includes("60 行"), "总行数可见")
assert(frame.includes("第 1 行"), "首行可见")
assert(frame.includes("q/Esc 返回"), "操作提示可见")
assert(!frame.includes("第 40 行"), "可见区外不渲染")
await panel.cleanup?.()

// ---- 3) runtime 集成：/read 打开、滚动、搜索、Esc 关闭 ----
const READ_TEXT = Array.from({ length: 80 }, (_, i) => `阅读行 ${i + 1}`).join("\n")
const calls = []
const bridge = {
  calls,
  connection: () => ({ mode: "local" }),
  whenReady: async () => true,
  async call(req) {
    this.calls.push(req)
    switch (req.cmd) {
      case "workbench_mode": return { ok: true, mode: "research", revision: 1 }
      case "settings_get":
        return { ok: true, settings: { llm_model: "glm-5", appearance_dark_mode: true, ui_animations: true, appearance_density: "comfortable", workspace_mode: "research", lit_threshold: 0.18, memory_enabled: true, default_offline: true } }
      case "command":
        if (req.text === "/read digest") return { ok: true, text: READ_TEXT }
        return { ok: true, text: "已执行" }
      default: return { ok: true }
    }
  },
}
const controller = createSessionController({ bridge, sleep: () => Promise.resolve() })
let exited = false
const setup = await testRender(React.createElement(OpenTuiApp, { reducedMotion: false, controller, onExit: () => { exited = true } }), {
  width: 110, height: 30, targetFps: 30,
})

frame = await stepUntil(setup, (f) => f.includes("格致") || f.includes("◇") || f.includes("◎"))
assert(frame !== null, "应用启动")

// 打开阅读器
await React.act(async () => { await setup.mockInput.typeText("/read digest") })
await React.act(async () => { setup.mockInput.pressEnter() })
frame = await stepUntil(setup, (f) => f.includes("阅读行 1") && f.includes("80 行"))
assert(frame !== null, "/read 打开全屏阅读器")
assert(frame.includes("摘要稿"), "阅读器标题来自面板名")

// 逐行滚动：Down 后首行变为阅读行 2
await React.act(async () => { setup.mockInput.pressArrow("down") })
await setup.renderOnce()
frame = setup.captureCharFrame()
assert(frame.includes("阅读行 2"), "Down 逐行滚动")

// 翻页：Right 一次进 10 行
await React.act(async () => { setup.mockInput.pressArrow("right") })
await setup.renderOnce()
frame = setup.captureCharFrame()
assert(frame.includes("阅读行 12"), "翻页滚动 10 行")

// 搜索：/ 进入搜索态，输入查询后 Enter 跳转
await React.act(async () => { setup.mockInput.pressKey("/") })
await setup.renderOnce()
frame = setup.captureCharFrame()
assert(frame.includes("搜索："), "搜索输入态")
await React.act(async () => { await setup.mockInput.typeText("阅读行 50") })
await setup.renderOnce()
frame = setup.captureCharFrame()
assert(frame.includes("搜索：阅读行 50"), "查询输入回显")
await React.act(async () => { setup.mockInput.pressEnter() })
await setup.renderOnce()
frame = setup.captureCharFrame()
assert(frame.includes("阅读行 50"), "Enter 跳转到命中行")
assert(frame.includes("搜索「阅读行 50」"), "退出搜索态后保留命中高亮提示")

// n 跳到下一处（阅读行 51）
await React.act(async () => { setup.mockInput.pressKey("n") })
await setup.renderOnce()
frame = setup.captureCharFrame()
assert(frame.includes("阅读行 51"), "n 跳转到下一处命中")

// Esc 关闭阅读器，回到 REPL
await React.act(async () => { setup.mockInput.pressEscape() })
frame = await stepUntil(setup, (f) => !f.includes("80 行") && !f.includes("阅读行 80"))
assert(frame !== null, "Esc 关闭阅读器")
assert(calls.some((c) => c.cmd === "command" && c.text === "/read digest"), "阅读内容走命令分发")

if (failures) {
  console.error(`opentui-reader: ${failures} FAILED`)
  process.exit(1)
}
console.log("opentui-reader: ALL_PASS")
process.exit(0)
