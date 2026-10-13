// 引擎分发解析（纯函数，供 cli.js 与单测共用）
// 0.9.0 起仅 OpenTUI 一个交互引擎；GEZHI_TUI_ENGINE 历史取值（ink）已废弃，统一按 OpenTUI 处理
// 非 TTY 永远走 headless/提示路径，不进交互分支。

/**
 * @param {{env?: object, stdin?: {isTTY?: boolean}, stdout?: {isTTY?: boolean}, mode?: string}} ctx
 * @returns {"headless-print"|"headless-hint"|"opentui"}
 */
export function resolveEngine({ env = {}, stdin = {}, stdout = {}, mode = "interactive" } = {}) {
  if (mode === "print") return "headless-print";
  if (!stdin?.isTTY || !stdout?.isTTY) return "headless-hint";
  return "opentui";
}
