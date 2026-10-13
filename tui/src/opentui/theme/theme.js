// 主题单例 —— 原地热切换（组件render时读 TOKENS，无需重建树）
// 探测顺序：renderer theme_mode 事件（OSC 11）→ COLORFGBG → 默认 dark
import { TOKENS, paletteFor } from "./tokens.js"

let mode = "dark"

export function applyTheme(next) {
  mode = next === "light" ? "light" : "dark"
  Object.assign(TOKENS, paletteFor(mode))
  return mode
}

export function currentTheme() {
  return mode
}

/** 终端明暗探测：250ms 超时 fallback，不阻塞首帧 */
export async function detectTheme(renderer, timeoutMs = 250) {
  try {
    const detected = await renderer?.waitForThemeMode?.(timeoutMs)
    if (detected === "light" || detected === "dark") return applyTheme(detected)
  } catch { /* 探测失败走兜底 */ }
  const fg = process.env.COLORFGBG
  if (fg) {
    const bg = fg.split(";").pop()
    const n = Number(bg)
    if (Number.isFinite(n)) return applyTheme(n < 8 ? "dark" : "light")
  }
  return applyTheme("dark")
}

/** 订阅终端主题变化（用户切换系统/终端明暗时实时跟随） */
export function watchTheme(renderer, onChange) {
  const handler = (m) => {
    if (m === "light" || m === "dark") {
      applyTheme(m)
      onChange?.(m)
    }
  }
  renderer?.on?.("theme_mode", handler)
  return () => renderer?.off?.("theme_mode", handler)
}
