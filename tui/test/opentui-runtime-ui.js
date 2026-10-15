// OpenTUI 运行时 headless 验证（fake bridge，覆盖真对话路径）
// 运行: node test/opentui-runtime-ui.js
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import { OpenTuiApp } from "../src/opentui/runtime.js"
import { createSessionController } from "../src/opentui/session-controller.js"

let failures = 0
// 等待回到空闲态（spinner ● + 无排队面板），避免后续提交被排队
async function settle(setup) {
  await stepUntil(setup, (f) => f.includes("● 就绪") && !f.includes("排队中"), { maxSteps: 900 })
  await new Promise((r) => setTimeout(r, 200))
  await setup.renderOnce()
}
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}

async function stepUntil(setup, predicate, { maxSteps = 150, settleMs = 12 } = {}) {
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

// ---- fake bridge：按 cmd 脚本化响应 ----
function fakeBridge({ mode = "research", statusPolls = 1 } = {}) {
  const calls = []
  let polls = 0
  return {
    calls,
    connection: () => ({ mode: "local" }),
    whenReady: async () => true,
    async call(req) {
      calls.push(req.cmd)
      switch (req.cmd) {
        case "workbench_mode":
          return req.mode ? { ok: true, mode: req.mode, revision: 2 } : { ok: true, mode, revision: 1 }
        case "agent_launch":
        case "agent_run":
          return { ok: true, result: { session_id: "s-001", agent: "research", mode: "agent", workspace_mode: mode } }
        case "agent_launch_status":
          polls++
          return { ok: true, pending: polls <= statusPolls ? { id: "l1" } : null }
        case "session_snapshot":
          return { ok: true, snapshot: { session_id: "s-001", status: "done", request: "调研课题", agent: "research" } }
        case "session_events":
          return { ok: true, events: [
            { kind: "assistant_message", payload: { text: "已完成文献梳理" } },
            { kind: "tool_call", payload: { name: "search" } },
            { kind: "completed", payload: { status: "done" } },
          ] }
        default:
          return { ok: true }
      }
    },
  }
}

const bridge = fakeBridge({ mode: "research" })
const controller = createSessionController({ bridge, sleep: (ms) => new Promise((r) => setTimeout(r, ms)) })
const setup = await testRender(React.createElement(OpenTuiApp, { reducedMotion: false, controller }), {
  width: 100, height: 24, targetFps: 30,
})
const { mockInput } = setup

// 启动即从引擎同步模式（research）
let frame = await stepUntil(setup, (f) => f.includes("◎"))
assert(frame !== null, "启动同步引擎模式（科研）")
assert(bridge.calls.includes("workbench_mode"), "启动读取 workbench_mode")

// /mode general 走 controller 命令子集
await React.act(async () => {
  await mockInput.typeText("/mode general")
})
await React.act(async () => {
  mockInput.pressEnter()
})
frame = await stepUntil(setup, (f) => f.includes("已切换到通用模式"))
assert(frame !== null, "/mode general 命令渲染")
assert(bridge.calls.filter((c) => c === "workbench_mode").length >= 2, "切换写回引擎")

// 普通消息：agent_launch → 轮询 → 快照 + 事件渲染
await React.act(async () => {
  await mockInput.typeText("帮我调研这个课题")
})
await React.act(async () => {
  mockInput.pressEnter()
})
frame = await stepUntil(setup, (f) => f.includes("已完成文献梳理") && f.includes("会话 s-001"), { maxSteps: 300 })
assert(frame !== null, "agent 会话事件渲染（assistant 消息 + 快照行）")
assert(frame.includes("会话 s-001"), "会话 ID 摘要行")
assert(bridge.calls.includes("agent_launch") && bridge.calls.includes("session_events"), "bridge 调用链完整")

// Ctrl+G 乐观切换 + 引擎写回
await React.act(async () => {
  mockInput.pressKey("g", { ctrl: true })
})
frame = await stepUntil(setup, (f) => f.includes("已切换到科研模式"))
assert(frame !== null, "Ctrl+G 切换并写回")

// /new 清空会话区
await React.act(async () => {
  await mockInput.typeText("/new")
})
await React.act(async () => {
  mockInput.pressEnter()
})
frame = await stepUntil(setup, (f) => f.includes("已新建会话"))
assert(frame !== null, "/new 清空并提示")
// ---- 排队输入----
await React.act(async () => {
  await mockInput.typeText("第一条消息")
})
await React.act(async () => {
  mockInput.pressEnter()
})
// 上一条还在流式时立刻提交第二条 → 入队
await React.act(async () => {
  await mockInput.typeText("第二条消息")
})
await React.act(async () => {
  mockInput.pressEnter()
})
frame = await stepUntil(setup, (f) => f.includes("排队中"), { maxSteps: 30 })
assert(frame !== null, "忙时排队面板出现")
// 第一条完成后自动冲刷第二条
frame = await stepUntil(setup, (f) => f.includes("❯ 第二条消息"), { maxSteps: 400 })
assert(frame !== null, "队列自动冲刷发送")

await settle(setup)
// ---- 鼠标交互（Phase 5）----
// Ctrl+P 打开面板 → hover 候选行 → 键盘/鼠标共享单一 index
await React.act(async () => {
  mockInput.pressKey("p", { ctrl: true })
})
frame = await stepUntil(setup, (f) => f.includes("命令面板"))
assert(frame !== null, "Ctrl+P 打开面板")
// 从帧定位面板首个候选行的 Y 坐标（"命令面板" 标题行 + 2）
const frameLines = setup.captureCharFrame().split("\n")
const headerY = frameLines.findIndex((l) => l.includes("命令面板"))
const rowY = headerY + 2
await React.act(async () => {
  await setup.mockMouse.moveTo(4, rowY)
})
await stepUntil(setup, (f) => f.includes("❯ /"), { maxSteps: 20 })
const hoverFrame = setup.captureCharFrame()
const hoverY = hoverFrame.split("\n").findIndex((l) => l.includes("❯ /"))
assert(hoverY !== -1 && hoverY >= headerY && hoverY <= headerY + 8, `hover 使命中行成为选中（❯ y=${hoverY}，面板头 y=${headerY}）`)

// 点击 ❯ 所在行 → 填入草稿并关闭面板
await React.act(async () => {
  await setup.mockMouse.click(4, hoverY)
})
frame = await stepUntil(setup, (f) => !f.includes("搜索："), { maxSteps: 20 })
assert(frame !== null && /│\s*❯\s*\//.test(frame), "点击面板行填入草稿并关闭")
// 滚轮：累积多行后向上滚 → 出现回看提示；向下滚回底部
for (const q of ["消息一", "消息二", "消息三", "消息四"]) {
  await React.act(async () => {
    await mockInput.typeText(q)
  })
  await React.act(async () => {
    mockInput.pressEnter()
  })
  await stepUntil(setup, (f) => f.includes("会话 s-001") && f.includes("✓ 会话完成"), { maxSteps: 300 })
}
// 等最后一条提交的轮询落地（fake bridge 每次 800ms）
await settle(setup)
await new Promise((r) => setTimeout(r, 2200))
await setup.renderOnce()
await React.act(async () => {
  await setup.mockMouse.scroll(4, 1, "up")
})
frame = await stepUntil(setup, (f) => f.includes("回看"), { maxSteps: 30 })
assert(frame !== null, "滚轮向上回看（出现回看提示）")
await React.act(async () => {
  for (let i = 0; i < 6; i++) await setup.mockMouse.scroll(4, 1, "down")
})
frame = await stepUntil(setup, (f) => !f.includes("回看"), { maxSteps: 30 })
assert(frame !== null, "滚轮滚回底部（回看提示消失）")

// ---- 审批面板----
const bridgeA = fakeBridge({ mode: "research" })
const origCallA = bridgeA.call.bind(bridgeA)
bridgeA.call = async (req) => {
  if (req.cmd === "session_snapshot") {
    return { ok: true, snapshot: { session_id: "s-001", status: "waiting_approval", agent: "research", pending: { tool: "research.create", tool_call_id: "call-1", reason: "创建课题需要确认" } } }
  }
  return origCallA(req)
}
const controllerA = createSessionController({ bridge: bridgeA, sleep: (ms) => new Promise((r) => setTimeout(r, ms)) })
const setupA = await testRender(React.createElement(OpenTuiApp, { reducedMotion: true, controller: controllerA }), { width: 100, height: 24 })
await stepUntil(setupA, (f) => f.includes("格致"))
await React.act(async () => {
  await setupA.mockInput.typeText("跑一个课题")
})
await React.act(async () => {
  setupA.mockInput.pressEnter()
})
frame = await stepUntil(setupA, (f) => f.includes("需要审批"), { maxSteps: 800, settleMs: 15 })
assert(frame !== null, "审批面板自动弹出（轮询捕获）")
assert(frame.includes("允许一次") && frame.includes("本会话允许"), "审批三选项")
// 数字键 1 = 允许一次
await React.act(async () => {
  setupA.mockInput.pressKey("1")
})
frame = await stepUntil(setupA, (f) => f.includes("已允许一次"), { maxSteps: 30 })
assert(frame !== null, "数字键决策并关闭面板")
assert(bridgeA.calls.includes("agent_approve"), "审批写回引擎")
setupA.renderer.destroy()

await settle(setup)
// ---- shell 模式----
const bridgeS = fakeBridge({ mode: "research" })
const origS = bridgeS.call.bind(bridgeS)
bridgeS.call = async (req) => {
  if (req.cmd === "shell_exec") { bridgeS.calls.push("shell_exec"); return { ok: true, text: "total 42" + String.fromCharCode(10) + "drwxr-xr-x 2 root root 4096 .", code: 0 } }
  return origS(req)
}
const controllerS = createSessionController({ bridge: bridgeS, sleep: (ms) => new Promise((r) => setTimeout(r, ms)) })
const setupS = await testRender(React.createElement(OpenTuiApp, { reducedMotion: true, controller: controllerS }), { width: 100, height: 24 })
await stepUntil(setupS, (f) => f.includes("格致"))
await React.act(async () => {
  await setupS.mockInput.typeText("!echo hello")
})
await React.act(async () => {
  setupS.mockInput.pressEnter()
})
frame = await stepUntil(setupS, (f) => f.includes("total 42"), { maxSteps: 60 })
assert(frame !== null, "shell 模式执行并渲染输出")
assert(frame.includes("$ echo hello"), "shell 回显命令")
setupS.renderer.destroy()

// ---- Ctrl+S 回合补充指导----
const bridgeT = fakeBridge({ mode: "research" })
const origT = bridgeT.call.bind(bridgeT)
bridgeT.call = async (req) => {
  if (req.cmd === "steer_add") { bridgeT.calls.push("steer_add"); return { ok: true } }
  return origT(req)
}
const controllerT = createSessionController({ bridge: bridgeT, sleep: (ms) => new Promise((r) => setTimeout(r, ms)) })
const setupT = await testRender(React.createElement(OpenTuiApp, { reducedMotion: true, controller: controllerT }), { width: 100, height: 24 })
await stepUntil(setupT, (f) => f.includes("格致"))
await React.act(async () => {
  await setupT.mockInput.typeText("跑课题")
})
await React.act(async () => {
  setupT.mockInput.pressEnter()
})
await stepUntil(setupT, (f) => f.includes("处理中") || f.includes("会话 s-001"), { maxSteps: 30 })
await React.act(async () => {
  setupT.mockInput.pressKey("s", { ctrl: true })
})
frame = await stepUntil(setupT, (f) => f.includes("补充指导"), { maxSteps: 20 })
assert(frame !== null, "Ctrl+S 进入补充指导模式")
await React.act(async () => {
  await setupT.mockInput.typeText("侧重近三年")
})
await React.act(async () => {
  setupT.mockInput.pressEnter()
})
frame = await stepUntil(setupT, (f) => f.includes("已追加指导"), { maxSteps: 60 })
assert(frame !== null, "指导追加到当前回合")
assert(bridgeT.calls.includes("steer_add"), "steer_add 写回引擎")
setupT.renderer.destroy()

// ---- Subagent 下钻----
const bridgeU = fakeBridge({ mode: "research" })
const origU = bridgeU.call.bind(bridgeU)
bridgeU.call = async (req) => {
  if (req.cmd === "swarm_list") {
    bridgeU.calls.push("swarm_list")
    return { ok: true, swarms: [{ plan_id: "sw-1", status: "running", request: "课题A", branches: [{ id: "b1", agent: "literature", request: "q1", status: "running" }] }] }
  }
  if (req.cmd === "swarm_get") {
    bridgeU.calls.push("swarm_get")
    return { ok: true, swarm: { plan_id: "sw-1", status: "running", request: "课题A", branches: [{ id: "b1", agent: "literature", request: "q1", status: "done" }] } }
  }
  return origU(req)
}
const controllerU = createSessionController({ bridge: bridgeU, sleep: (ms) => new Promise((r) => setTimeout(r, ms)) })
const setupU = await testRender(React.createElement(OpenTuiApp, { reducedMotion: true, controller: controllerU }), { width: 100, height: 24 })
await stepUntil(setupU, (f) => f.includes("格致"))
await React.act(async () => {
  await setupU.mockInput.typeText("/subagents")
})
await React.act(async () => {
  setupU.mockInput.pressEnter()
})
frame = await stepUntil(setupU, (f) => f.includes("Subagent 计划"), { maxSteps: 40 })
assert(frame !== null, "/subagents 面板打开")
await React.act(async () => {
  setupU.mockInput.pressEnter()
})
frame = await stepUntil(setupU, (f) => f.includes("Subagent sw-1"), { maxSteps: 40 })
assert(frame !== null, "Enter 下钻只读视图")
assert(frame.includes("分支") && frame.includes("literature"), "下钻视图含分支状态")
await React.act(async () => {
  setupU.mockInput.pressEscape()
})
frame = await stepUntil(setupU, (f) => !f.includes("Subagent sw-1"), { maxSteps: 20 })
assert(frame !== null, "Esc 返回主会话")
setupU.renderer.destroy()

// ---- 科研步骤面板（创新项）----
await React.act(async () => {
  await mockInput.typeText("/steps")
})
await React.act(async () => {
  mockInput.pressEnter()
})
frame = await stepUntil(setup, (f) => f.includes("科研步骤"), { maxSteps: 20 })
assert(frame !== null, "/steps 打开科研步骤面板")
await React.act(async () => {
  mockInput.pressKey("2")
})
await React.act(async () => {
  mockInput.pressEnter()
})
frame = await stepUntil(setup, (f) => f.includes("证据评审"), { maxSteps: 20 })
assert(frame !== null, "选择步骤填入请求")
// 发起该步骤请求（顺带清空草稿）
await React.act(async () => {
  mockInput.pressEnter()
})
await stepUntil(setup, (f) => f.includes("会话 s-001") && f.includes("✓ 会话完成"), { maxSteps: 300 })

// ---- Ctrl+O 全局展开 ----
await React.act(async () => {
  mockInput.pressKey("o", { ctrl: true })
})
frame = await stepUntil(setup, (f) => f.includes("展开"), { maxSteps: 20 })
assert(frame !== null, "Ctrl+O 切换展开态")

// /theme 本地命令（客户端关注，不走 bridge）
await React.act(async () => {
  await mockInput.typeText("/theme light")
})
await React.act(async () => {
  mockInput.pressEnter()
})
frame = await stepUntil(setup, (f) => f.includes("浅色主题"), { maxSteps: 20 })
assert(frame !== null, "/theme light 切换")
await React.act(async () => {
  await mockInput.typeText("/theme dark")
})
await React.act(async () => {
  mockInput.pressEnter()
})
frame = await stepUntil(setup, (f) => f.includes("深色主题"), { maxSteps: 20 })
assert(frame !== null, "/theme dark 切回")

setup.renderer.destroy()

// reducedMotion + 失败回滚路径
const bridge2 = fakeBridge({ mode: "general" })
const origCall2 = bridge2.call.bind(bridge2)
bridge2.call = async (req) => {
  if (req.cmd === "workbench_mode" && req.mode) return { ok: false, error: "引擎不支持" }
  return origCall2(req)
}
const controller2 = createSessionController({ bridge: bridge2 })
const setup2 = await testRender(React.createElement(OpenTuiApp, { reducedMotion: true, controller: controller2 }), { width: 100, height: 24 })
await stepUntil(setup2, (f) => f.includes("格致"))
await React.act(async () => {
  setup2.mockInput.pressKey("g", { ctrl: true })
})
const frame2 = await stepUntil(setup2, (f) => f.includes("模式未切换"), { maxSteps: 30 })
assert(frame2 !== null, "引擎拒绝时回滚并提示")
setup2.renderer.destroy()

if (failures === 0) {
  console.error("opentui-runtime-ui: ALL_PASS")
  process.exit(0)
}
console.error(`opentui-runtime-ui: FAILURES=${failures}`)
process.exit(1)
