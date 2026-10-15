// Phase 2 纯逻辑测试：键盘路由 + footer 槽位（无终端）
// 运行: node test/opentui-shell-logic.js
import { createKeyRouter, ctrl, shiftTab, esc, bare, resolveEscape, resolveCtrlCIntent, LAYERS } from "../src/opentui/keyboard/router.js"
import { createTipRotator, resolveSlots, formatContextUsage, resolveWarning, clipSlot, DEFAULT_SLOTS } from "../src/opentui/layout/footer.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}

// ---- 键盘路由分层 ----
assert(LAYERS[0] === "readonly" && LAYERS.at(-1) === "editor", "层级顺序契约")

const router = createKeyRouter([
  { layer: "readonly", match: (k, ctx) => ctx.readonly && (k.name === "escape" || k.name === "q"), run: () => ({ handled: true }) },
  { layer: "approval", match: (k) => k.name === "1" || k.name === "2", run: () => ({ handled: true }) },
  { layer: "selection", match: esc, run: () => ({ handled: true }) },
  { layer: "global", match: ctrl("g"), run: () => ({ handled: true }) },
  { layer: "global", match: shiftTab, run: () => ({ handled: true }) },
  { layer: "editor", match: bare("x"), run: () => ({ handled: true }) },
])

// approval 独占：审批打开时数字键到 approval 层，全局层不可见
let r = router.dispatch({ name: "1", ctrl: false, shift: false }, { approvalOpen: true, readonly: false })
assert(r.layer === "approval" && r.consumed, "approval 层独占数字键")

// readonly 层最高优先：readonly 打开时 escape 先给 readonly
r = router.dispatch({ name: "escape", ctrl: false, shift: false }, { readonly: true })
assert(r.layer === "readonly", "readonly 层优先于 selection")

// selection 打开时 esc 关面板；全局 ctrl+g 仍可穿透（无 selection 路由命中 g）
r = router.dispatch({ name: "g", ctrl: true, shift: false }, { readonly: false })
assert(r.layer === "global", "全局层命中 ctrl+g")

// 未命中任何层：落到编辑器默认行为
r = router.dispatch({ name: "z", ctrl: false, shift: false }, {})
assert(r.layer === null && !r.consumed, "未命中不消费")

// ---- Esc / Ctrl+C 语义 ----
assert(resolveEscape({ selectionOpen: true, streaming: true }).action === "dismiss_selection", "Esc 先关面板")
assert(resolveEscape({ selectionOpen: false, streaming: true }).action === "pause_output", "Esc 再暂停输出")
assert(resolveEscape({ selectionOpen: false, streaming: false }).action === "hint_exit", "Esc 无层可关给提示")

assert(resolveCtrlCIntent({ name: "c", ctrl: true }, { hasSelection: true }).action === "copy_selection", "Ctrl+C 一级：复制选区")
assert(resolveCtrlCIntent({ name: "c", ctrl: true }, { draft: "abc" }).action === "clear_draft", "Ctrl+C 二级：清草稿")
const first = resolveCtrlCIntent({ name: "c", ctrl: true }, { lastCtrlCAt: 0 }, 10000)
const second = resolveCtrlCIntent({ name: "c", ctrl: true }, { lastCtrlCAt: first.markCtrlCAt }, 11000)
assert(first.action === "hint_exit" && second.action === "exit", "Ctrl+C 三级：2s 双击退出")
const late = resolveCtrlCIntent({ name: "c", ctrl: true }, { lastCtrlCAt: 0 }, 13000)
assert(late.action === "hint_exit" && late.markCtrlCAt === 13000, "Ctrl+C 超窗重置")

// ---- footer 槽位 ----
const slots = resolveSlots({ mode: "◇ 通用", model: "glm", cwd: "D:\\proj" })
assert(slots.length === 3 && slots[0].slot === "mode", "空槽不占位（无 tasks/git）")
assert(resolveSlots({ mode: "m", git: "main" }, ["git", "mode"])[0].slot === "git", "槽位顺序可配置")
assert(DEFAULT_SLOTS.includes("cwd"), "默认槽位含 cwd")

const rot = createTipRotator([{ weight: 3 }, { weight: 1 }, { weight: 1 }])
const seq = rot.preview(10)
assert(seq[0] === 0, "SWRR 首个周期取权重最高")
assert(new Set(seq).size === 3, "SWRR 平滑覆盖全部技巧（不错位重复）")
assert(seq.filter((i) => i === 0).length >= 5, "SWRR 权重比例合理（0 号占多数）")
assert(createTipRotator([]).indexAt(0) === -1, "空技巧表安全")

assert(formatContextUsage(128400, 272000) === "context: 47% (128.4K/272K)", "上下文占用文案")
assert(formatContextUsage(500, 68000) === "context: 1% (500/68K)", "小数值不缩写")
assert(clipSlot("D:\\deeplearning\\基于通用智能体的AI科研智能体应用开发", 16).endsWith("…"), "长 cwd 槽位裁剪")
assert(clipSlot("◇ 通用", 10) === "◇ 通用", "短槽位不裁剪")
assert(resolveWarning({ goalLength: 250, goalMax: 200 }) !== null, "goal 超长预警")
assert(resolveWarning({ goalLength: 100 }) === null, "无预警返回 null")

if (failures === 0) {
  console.error("opentui-shell-logic: ALL_PASS")
  process.exit(0)
}
console.error(`opentui-shell-logic: FAILURES=${failures}`)
process.exit(1)
