// 引擎分发纯逻辑测试（0.9.0 起单引擎：任何开关都走 opentui；非 TTY 与 print 优先）
// 运行: node test/engine-select.js
import { resolveEngine } from "../src/engine-select.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}

const tty = { stdin: { isTTY: true }, stdout: { isTTY: true }, mode: "interactive" }

// 默认（无开关）→ opentui
assert(resolveEngine(tty) === "opentui", "TTY 无开关默认 opentui")
assert(resolveEngine({ ...tty, env: {} }) === "opentui", "空 env 默认 opentui")
assert(resolveEngine({ ...tty, env: { GEZHI_TUI_ENGINE: "" } }) === "opentui", "空字符串开关默认 opentui")

// 历史 ink 逃生舱已移除（0.9.0）：废弃取值统一按 opentui，大小写/空格容错不回归
assert(resolveEngine({ ...tty, env: { GEZHI_TUI_ENGINE: "ink" } }) === "opentui", "废弃 ink 取值统一 opentui")
assert(resolveEngine({ ...tty, env: { GEZHI_TUI_ENGINE: " INK " } }) === "opentui", "ink 大小写与空格容错后仍 opentui")

// 非 TTY：永远不进交互分支（即使显式 opentui）
assert(resolveEngine({ ...tty, stdin: { isTTY: false } }) === "headless-hint", "stdin 非 TTY 走提示")
assert(resolveEngine({ ...tty, stdout: { isTTY: false } }) === "headless-hint", "stdout 非 TTY 走提示")
assert(resolveEngine({ ...tty, stdin: { isTTY: false }, env: { GEZHI_TUI_ENGINE: "opentui" } }) === "headless-hint", "非 TTY 优先于引擎开关")

// print 模式优先
assert(resolveEngine({ ...tty, mode: "print" }) === "headless-print", "print 模式走 headless")

if (failures === 0) {
  console.error("engine-select: ALL_PASS")
  process.exit(0)
}
console.error(`engine-select: FAILURES=${failures}`)
process.exit(1)
