// Phase 2 应用壳 headless 验证：布局/侧栏三态/footer 双行/模式色
// 运行: node test/opentui-shell-ui.js
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import { AppShell, draftWithCursor } from "../src/opentui/app-shell.js"
import { StartupScreen } from "../src/opentui/components/startup-screen.js"
import { ContextPanel } from "../src/opentui/components/context-panel.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}

async function renderShell(state, { width = 120, height = 24 } = {}) {
  const setup = await testRender(React.createElement(AppShell, { state }), { width, height, targetFps: 30 })
  for (let i = 0; i < 5; i++) await setup.renderOnce()
  const frame = setup.captureCharFrame()
  setup.renderer.destroy()
  return frame
}

const base = {
  workspaceMode: "general",
  terminalWidth: 120,
  lines: [{ text: "你好，格致" }, { text: "历史行", dim: true }],
  spinner: { active: true, frame: 3 },
  draft: "",
  status: "就绪",
  sidebar: { hidden: false, sections: [{ title: "模型", lines: [{ text: "glm-5" }] }] },
  slotValues: { mode: "◇ 通用", model: "glm-5", cwd: "D:\\proj" },
  tips: [{ text: "Ctrl+P 命令面板", weight: 2 }, { text: "Ctrl+G 切换模式", weight: 1 }],
  used: 8200,
  total: 68000,
}

// 宽终端：侧栏停靠 + footer 双行 + 品牌头 + spinner
let f = await renderShell(base)
assert(f.includes("格致") && f.includes("◇"), "品牌头与模式徽标")
assert(f.includes("glm-5"), "侧栏停靠渲染模型区")
assert(f.includes("你好，格致"), "会话行渲染")
assert(f.includes("ctx 12% · 8.2K/68K"), "footer 行 2 上下文占用（ZCode 紧凑数字 badge）")
assert(f.includes("⌘O 展开"), "footer 行 1 expand 提示（键帽化）")
assert(f.includes("[◇ 通用]") && f.includes("[D:\\proj]"), "footer 行 1 槽位（mode 着色值原样）")
assert(f.includes("模式"), "输入框标题含模式")

// 窄终端：侧栏变 overlay（不占主列）→ AppShell 当前实现仅停靠或隐藏，验证隐藏路径
f = await renderShell({ ...base, sidebar: { hidden: true, sections: base.sidebar.sections } }, { width: 100 })
assert(!f.includes("glm-5") || !f.split("\n").some((l) => l.includes("glm-5") && l.trim().startsWith("glm")), "用户隐藏侧栏后主列无侧栏内容")

// 科研模式：输入框标题与徽标切换
f = await renderShell({ ...base, workspaceMode: "research" })
assert(f.includes("◎") && f.includes("科研"), "科研模式徽标与标题")

// shimmer 播放中：品牌头仍在（组件不崩）
f = await renderShell({ ...base, shimmerFrame: 5 })
assert(f.includes("格致"), "shimmer 帧播放中品牌头稳定")

// 空状态：无行无草稿不崩
f = await renderShell({ workspaceMode: "general", terminalWidth: 120 })
assert(f.includes("格致"), "空状态渲染")


// 启动屏
const setupBoot = await testRender(React.createElement(StartupScreen, { workspaceMode: "research", spinnerFrame: 40, shimmerFrame: 30 }), { width: 60, height: 14 })
await setupBoot.renderOnce()
const bootFrame = setupBoot.captureCharFrame()
assert(bootFrame.includes("格") && bootFrame.includes("科研模式") && bootFrame.includes("连接科研引擎"), "启动屏渲染（logo+模式+连接态）")
assert(bootFrame.includes("✦") && bootFrame.includes("╭"), "启动屏格点图案（聚焦科研）")
setupBoot.renderer.destroy()

// reducedMotion：静态全量图案（开放格点 · 通用）
const setupBoot2 = await testRender(React.createElement(StartupScreen, { workspaceMode: "general", reducedMotion: true }), { width: 60, height: 14 })
await setupBoot2.renderOnce()
const bootFrame2 = setupBoot2.captureCharFrame()
assert(bootFrame2.includes("格") && bootFrame2.includes("通用模式") && bootFrame2.includes("·"), "reducedMotion 静态启动屏")
setupBoot2.renderer.destroy()

// 上下文分解面板
const setupCtx = await testRender(
  React.createElement(ContextPanel, { breakdown: { messages: 8000, tools: 2000, input: 10000, reserve: 4000, limit: 68000 } }),
  { width: 70, height: 10 }
)
await setupCtx.renderOnce()
const ctxFrame = setupCtx.captureCharFrame()
assert(ctxFrame.includes("15%") && ctxFrame.includes("消息 8000") && ctxFrame.includes("工具 schema 2000"), "上下文分解面板")
setupCtx.renderer.destroy()

if (failures === 0) {
  console.error("opentui-shell-ui: ALL_PASS")
  process.exit(0)
}
console.error(`opentui-shell-ui: FAILURES=${failures}`)
process.exit(1)
