// 键盘路由分层 —— 纯函数，可无终端穷举单测（对齐 ZCode app-keyboard.ts 分层独占思想）
// 层级（自上而下，命中即 consume，下层不可见）：
//   readonly 视图 → approval 审批 → selection 选择面板 → 全局快捷键 → 编辑器
// 模态冲突被结构性消灭：每层返回 {handled, consumed}，consumed=true 时停止下传

export const LAYERS = ["readonly", "approval", "selection", "global", "editor"]

/** 路由表项：{ layer, match(key), run(key, ctx) }；match 返回布尔 */
export function createKeyRouter(routes) {
  return {
    routes,
    /** 分发一个按键事件；返回 { layer, handled, consumed } */
    dispatch(key, ctx = {}) {
      for (const layer of LAYERS) {
        for (const route of routes) {
          if (route.layer !== layer) continue
          if (!route.match(key, ctx)) continue
          const result = route.run ? route.run(key, ctx) : { handled: true }
          const handled = result?.handled !== false
          return { layer, handled, consumed: handled }
        }
      }
      return { layer: null, handled: false, consumed: false }
    },
  }
}

// ---- 常用匹配器 ----

export const ctrl = (name) => (key) => key.ctrl && !key.shift && key.name === name
export const shiftTab = (key) => key.shift && key.name === "tab"
export const esc = (key) => key.name === "escape" && !key.ctrl && !key.shift
export const bare = (name) => (key) => !key.ctrl && !key.meta && !key.shift && key.name === name

/**
 * Esc 分层关闭（对齐 ZCode）：
 *   有 selection 面板 → 关面板；有进行中输出 → 暂停；否则提示双击 Ctrl+C 退出
 * 返回语义动作字符串，由壳层执行。
 */
export function resolveEscape(ctx) {
  if (ctx.selectionOpen) return { action: "dismiss_selection", consumed: true }
  if (ctx.streaming) return { action: "pause_output", consumed: true }
  return { action: "hint_exit", consumed: true }
}

/**
 * Ctrl+C 三级语义（对齐 ZCode resolveCtrlCExitIntent）：
 *   有文本选区 → 复制；草稿非空 → 清空；否则 2 秒窗口内双击才退出
 */
export function resolveCtrlCIntent(key, ctx, now = Date.now()) {
  if (key.ctrl && key.name !== "c") return { action: "pass", consumed: false }
  if (ctx.hasSelection) return { action: "copy_selection", consumed: true }
  if (ctx.draft) return { action: "clear_draft", consumed: true }
  if (ctx.lastCtrlCAt && now - ctx.lastCtrlCAt <= 2000) return { action: "exit", consumed: true }
  return { action: "hint_exit", consumed: true, markCtrlCAt: now }
}
