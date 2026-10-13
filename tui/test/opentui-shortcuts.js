// 快捷键与 /config 集成验证（fake bridge）
// 运行: node test/opentui-shortcuts.js
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import { OpenTuiApp } from "../src/opentui/runtime.js"
import { createSessionController } from "../src/opentui/session-controller.js"
import { flattenConfig } from "../src/opentui/components/config-panel.js"
import { SHORTCUTS_ROWS, SHORTCUT_GROUPS } from "../src/opentui/components/shortcuts-panel.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}

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

function bridge() {
  const calls = []
  return {
    calls,
    connection: () => ({ mode: "local" }),
    whenReady: async () => true,
    async call(req) {
      this.calls.push(req.cmd)
      switch (req.cmd) {
        case "workbench_mode": return req.mode ? { ok: true, mode: req.mode, revision: 2 } : { ok: true, mode: "research", revision: 1 }
        case "settings_get":
          return { ok: true, settings: { llm_model: "glm-5", appearance_dark_mode: true, ui_animations: true, appearance_density: "comfortable", workspace_mode: "research", lit_threshold: 0.18, memory_enabled: true, default_offline: true } }
        case "settings_save": return { ok: true, settings: req.settings }
        case "llm_status": return { ok: true, status: { model: "glm-5", models: ["glm-5", "deepseek-chat"], reasoning_effort: "medium" } }
        case "todo_list": return { ok: true, todos: [{ id: 't1', title: '复现基线实验', status: 'doing' }, { id: 't2', title: '整理文献笔记', status: 'pending' }] }
        case "command":
          if (req.text.startsWith("/tasks")) return { ok: true, text: "任务列表：0 个" }
          return { ok: true, text: req.text + " 已执行" }
        default: return { ok: true }
      }
    },
  }
}

// ---- 1) 纯函数：配置条目与快捷键面板数据 ----
const rows = flattenConfig()
assert(rows.length === 11 && rows.some((r) => r.key === "llm_model") && rows.some((r) => r.group === "实验能力"), "配置面板 11 项含模型与实验能力组")
assert(SHORTCUTS_ROWS > 20 && SHORTCUT_GROUPS.length === 5, "快捷键面板 5 组")
assert(SHORTCUT_GROUPS.some((g) => g.items.some((i) => i.keys.includes("Ctrl+K"))), "Ctrl+K 在帮助表")

// ---- 2) runtime 集成 ----
const b = bridge()
const controller = createSessionController({ bridge: b, sleep: () => Promise.resolve() })
let exited = false
const setup = await testRender(React.createElement(OpenTuiApp, { reducedMotion: false, controller, onExit: () => { exited = true } }), {
  width: 110, height: 28, targetFps: 30, // 28 行：config/快捷键面板窗口化后小终端仍可分页（溢出指示 + 滚动）
})

let frame = await stepUntil(setup, (f) => f.includes("◎"))
assert(frame !== null, "启动同步科研模式")

// /config 面板
await React.act(async () => { await setup.mockInput.typeText("/config") })
await React.act(async () => { setup.mockInput.pressEnter() })
frame = await stepUntil(setup, (f) => f.includes("配置 · Enter"))
assert(frame !== null && frame.includes("默认模型") && frame.includes("glm-5"), "/config 面板渲染（含模型值）")
assert(b.calls.includes("settings_get") && b.calls.includes("llm_status"), "config 拉取 settings + llm_status")

// ↑↓ 移动 + Enter 循环切换 reasoning_effort（第 2 项）
await React.act(async () => { setup.mockInput.pressArrow("down") })
await React.act(async () => { setup.mockInput.pressEnter() })
frame = await stepUntil(setup, (f) => f.includes("推理强度 → high") || f.includes("保存失败"))
assert(frame !== null, "config Enter 循环切换并即存（reasoning_effort → high）")
assert(b.calls.includes("settings_save"), "settings_save 已调用")

// Esc 关闭
await React.act(async () => { setup.mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })
frame = await stepUntil(setup, (f) => !f.includes("配置 · Enter"))
assert(frame !== null, "Esc 关闭配置面板")

// F1 快捷键面板
await React.act(async () => { setup.mockInput.pressKey("F1") })
frame = await stepUntil(setup, (f) => f.includes("快捷键") && f.includes("Ctrl+G"))
assert(frame !== null, "F1 快捷键帮助面板")
await React.act(async () => { setup.mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })

// Ctrl+R 历史搜索：有历史后打开 → 输入过滤 → Enter 填入
await React.act(async () => { await setup.mockInput.typeText("/new") })
await React.act(async () => { setup.mockInput.pressEnter() })
await React.act(async () => { await setup.mockInput.typeText("历史条目甲") })
await React.act(async () => { setup.mockInput.pressEnter() })
await stepUntil(setup, (f) => f.includes("就绪"), { maxSteps: 200 })
await React.act(async () => { setup.mockInput.pressKey("r", { ctrl: true }) })
frame = await stepUntil(setup, (f) => f.includes("历史搜索"))
assert(frame !== null, "Ctrl+R 打开历史搜索")
await React.act(async () => { await setup.mockInput.typeText("甲") })
frame = await stepUntil(setup, (f) => f.includes("历史条目甲"))
assert(frame !== null, "历史搜索输入过滤")
await React.act(async () => { setup.mockInput.pressEnter() })
frame = await stepUntil(setup, (f) => !f.includes("历史搜索") && f.includes("历史条目甲"))
assert(frame !== null, "历史搜索 Enter 填入草稿")

// Ctrl+T 待办面板
await React.act(async () => { setup.mockInput.pressKey("t", { ctrl: true }) })
frame = await stepUntil(setup, (f) => f.includes("研究待办") && f.includes("复现基线实验"))
assert(frame !== null, "Ctrl+T 研究待办面板")
await React.act(async () => { setup.mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })

// Ctrl+L 清屏
await React.act(async () => { setup.mockInput.pressKey("l", { ctrl: true }) })
frame = await stepUntil(setup, (f) => f.includes("已清屏"))
assert(frame !== null, "Ctrl+L 清屏")

// Ctrl+K 删到行尾：输入 abc → Home → Ctrl+K → 草稿空
await React.act(async () => { await setup.mockInput.typeText("abc") })
await React.act(async () => { setup.mockInput.pressKey("HOME") })
await React.act(async () => { setup.mockInput.pressKey("k", { ctrl: true }) })
frame = await stepUntil(setup, (f) => !f.includes("abc"))
assert(frame !== null, "Ctrl+K 删除到行尾")

// Ctrl+U 清草稿
await React.act(async () => { await setup.mockInput.typeText("xyz") })
await React.act(async () => { setup.mockInput.pressKey("u", { ctrl: true }) })
frame = await stepUntil(setup, (f) => !f.includes("xyz"))
assert(frame !== null, "Ctrl+U 清空草稿")

// Ctrl+D 退出
await React.act(async () => { setup.mockInput.pressKey("d", { ctrl: true }) })
await new Promise((r) => setTimeout(r, 100))
assert(exited, "Ctrl+D 触发退出")

// /tasks 走 BRIDGE_COMMAND_MAP（list 直通）
await React.act(async () => { await setup.mockInput.typeText("/tasks") })
await React.act(async () => { setup.mockInput.pressEnter() })
frame = await stepUntil(setup, (f) => f.includes("暂无任务"))
assert(frame !== null, "/tasks 走 BRIDGE_COMMAND_MAP 直通")
// /notebook 走 command_center 兜底分发（未映射命令）
await React.act(async () => { await setup.mockInput.typeText("/notebook") })
await React.act(async () => { setup.mockInput.pressEnter() })
frame = await stepUntil(setup, (f) => f.includes("/notebook 已执行"))
assert(frame !== null, "/notebook 走 command_center 兜底分发")

setup.renderer.destroy()
await new Promise((r) => setTimeout(r, 150))
if (failures === 0) {
  console.error("opentui-shortcuts: ALL_PASS")
  process.exit(0)
}
console.error(`opentui-shortcuts: FAILURES=${failures}`)
process.exit(1)
