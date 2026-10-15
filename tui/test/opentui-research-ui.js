// 科研创新功能 runtime 集成验证（fake bridge：面板打开/Esc/goal 注入/模式门禁/novelty）
// 运行: node test/opentui-research-ui.js
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import { writeFileSync } from "node:fs"
import { OpenTuiApp } from "../src/opentui/runtime.js"
import { createSessionController } from "../src/opentui/session-controller.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
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

function researchBridge() {
  const calls = []
  const requests = []
  let polls = 0
  return {
    calls, requests,
    connection: () => ({ mode: "local" }),
    whenReady: async () => true,
    async call(req) {
      calls.push(req.cmd)
      if (req.request) requests.push(req.request)
      switch (req.cmd) {
        case "workbench_mode":
          return req.mode ? { ok: true, mode: req.mode, revision: 2 } : { ok: true, mode: "research", revision: 1 }
        case "notebook_list":
          return req.kind === "hypothesis"
            ? { ok: true, records: [
                { id: "h1", title: "假设甲：高温提升产率", status: "completed", updated_at: "2026-10-01T00:00:00Z",
                  evidence: [{ relation: "supports" }, { relation: "supports" }] },
              ] }
            : { ok: true, records: [
                { id: "e1", title: "基线实验", status: "completed", kind: "experiment", related_ids: [] },
                { id: "e2", title: "分支A：调参", status: "running", kind: "experiment", related_ids: ["e1"] },
              ] }
        case "evidence_brief":
          return { ok: true, brief: { records: [
            { id: "r1", title: "文献一", counts: { supports: 2, challenges: 1, context: 3 }, gaps: ["缺样本量"], followup_draft: "补充对照实验" },
          ] } }
        case "chat_sessions":
          return { ok: true, sessions: [{ session_id: "s-continue-42", status: "done" }] }
        case "agent_continue":
          return { ok: true, result: { session_id: "s-continue-42", status: "done" } }
        case "agent_launch":
        case "agent_run":
          return { ok: true, result: { session_id: "s-90", agent: "research" } }
        case "agent_launch_status":
          polls += 1
          return { ok: true, pending: polls <= 1 ? { id: "l1" } : null }
        case "session_events":
          return { ok: true, events: [{ kind: "assistant_message", payload: { text: "检测完成" } }] }
        case "session_snapshot":
          return { ok: true, snapshot: { session_id: "s-90", status: "done" } }
        default:
          return { ok: true }
      }
    },
  }
}

const bridge = researchBridge()
const controller = createSessionController({ bridge, sleep: () => Promise.resolve() })
const setup = await testRender(React.createElement(OpenTuiApp, { reducedMotion: false, controller }), {
  width: 110, height: 26, targetFps: 30,
})
const { mockInput } = setup

// 启动同步科研模式
let frame = await stepUntil(setup, (f) => f.includes("◎"))
assert(frame !== null, "启动同步科研模式")

async function runCommand(text) {
  await React.act(async () => { await mockInput.typeText(text) })
  await React.act(async () => { mockInput.pressEnter() })
}

// ---- /hypotheses 假设排行榜 ----
await runCommand("/hypotheses")
frame = await stepUntil(setup, (f) => f.includes("假设排行榜"))
assert(frame !== null, "/hypotheses 打开假设排行榜面板")
assert(frame.includes("假设甲"), "面板含排序后的假设")
assert(bridge.calls.includes("notebook_list"), "面板数据走 notebook_list")

// Esc 关闭
await React.act(async () => { mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })
frame = await stepUntil(setup, (f) => !f.includes("假设排行榜"))
assert(frame !== null, "Esc 关闭假设排行榜")

// ---- /consensus 共识计量 ----
await runCommand("/consensus")
frame = await stepUntil(setup, (f) => f.includes("共识计量"))
assert(frame !== null, "/consensus 打开共识计量条")
assert(frame.includes("支持 2") && frame.includes("反驳 1"), "共识三段计数")
await React.act(async () => { mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })
await React.act(async () => { await new Promise((r) => setTimeout(r, 50)) })
await setup.renderOnce()

// ---- /matrix 证据矩阵 ----
await runCommand("/matrix")
frame = await stepUntil(setup, (f) => f.includes("证据矩阵"))
assert(frame !== null && frame.includes("文献一") && frame.includes("待补"), "/matrix 证据矩阵渲染")
await React.act(async () => { mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })

// ---- /experiments 实验树 ----
await runCommand("/experiments")
frame = await stepUntil(setup, (f) => f.includes("实验树"))

assert(frame !== null && frame.includes("基线实验") && frame.includes("└"), "/experiments 实验树渲染")
await React.act(async () => { mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })

// ---- /goal 目标模式：footer 徽标 + 提交注入 ----
await runCommand("/goal 复现论文实验")
frame = await stepUntil(setup, (f) => f.includes("◎ goal") || f.includes("目标已设置"))
assert(frame !== null, "/goal 设置后 footer 显示 ◎ goal")
await runCommand("帮我跑一下基线")
frame = await stepUntil(setup, (f) => f.includes("会话 s-90"), { maxSteps: 300 })
assert(frame !== null, "goal 注入后正常发起会话")
assert(bridge.requests.some((r) => String(r).includes("[研究目标] 复现论文实验") && String(r).includes("[请求] 帮我跑一下基线")),
  "提交时注入研究目标上下文（[研究目标]…[请求]）")

// ---- /novelty 新颖性检测 ----
await runCommand("/novelty 图神经网络异常检测")
frame = await stepUntil(setup, (f) => f.includes("新颖性检测"), { maxSteps: 300 })
assert(frame !== null, "/novelty 发起检测")
assert(bridge.requests.some((r) => String(r).includes("新颖性检测") && String(r).includes("图神经网络异常检测")),
  "novelty 走四段式特化 prompt")

// ---- 通用模式门禁 ----
await runCommand("/mode general")
frame = await stepUntil(setup, (f) => f.includes("已切换到通用模式"))
assert(frame !== null, "切到通用模式")
await runCommand("/hypotheses")
frame = await stepUntil(setup, (f) => f.includes("仅在科研模式可用"))
assert(frame !== null, "通用模式拒绝科研分析面板")

// ---- autoResume：启动即恢复最近会话 ----
const bridge2 = researchBridge()
const controller2 = createSessionController({ bridge: bridge2, sleep: () => Promise.resolve() })
const setup2 = await testRender(React.createElement(OpenTuiApp, { reducedMotion: false, controller: controller2, autoResume: true }), {
  width: 110, height: 26, targetFps: 30,
})
frame = await stepUntil(setup2, (f) => f.includes("恢复最近会话：s-continue-42"), { maxSteps: 300 })
assert(frame !== null, "autoResume 启动恢复最近会话")
assert(bridge2.calls.includes("chat_sessions") && bridge2.calls.includes("agent_continue"), "恢复链路 chat_sessions → agent_continue")
setup2.renderer.destroy()

setup.renderer.destroy()
if (failures === 0) {
  console.error("opentui-research-ui: ALL_PASS")
  process.exit(0)
}
console.error(`opentui-research-ui: FAILURES=${failures}`)
process.exit(1)
