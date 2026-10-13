// UI 纯函数零件 —— ZCode 视觉语言的终端构件（meter/spark/lamp/count/stat），可无终端单测。
// 取值口径全部来自 theme/tokens.js 与 ZCode 常量：
//   计量条/迷你图：ZCode WorkflowRosterParts（█ 分段、min 格 0.5）与 FrameChart（双 sparkline）；
//   灯词汇：run-status-presentation STATUS_DOT（done● pending○ running◐/✕）；
//   计数行：WorkflowRosterParts 计数行（✓n · ◌n · ✕n · ○n，0 缺席）；
//   键帽：KBD（每键独立 chip，组合间隔 1 空格）。
import { SYMBOLS, KBD } from "../theme/symbols.js"

/** 计量条：value∈[0,1] → █▉▊▋▌▍▎▏ 填充 + · 剩余（ZCode 段序计量，min 0.5 格） */
export function meterBar(value, width = 10) {
  const w = Math.max(1, Math.floor(width))
  const v = Math.min(1, Math.max(0, Number(value) || 0))
  const filled = Math.min(w, Math.max(v > 0 ? 0.5 : 0, Math.round(v * w * 2) / 2))
  const steps = ["▏", "▎", "▍", "▌", "▋", "▊", "▉"]
  let bar = ""
  for (let i = 0; i < w; i++) {
    if (filled >= i + 1) bar += "█"
    else if (filled > i) bar += steps[Math.min(steps.length - 1, Math.floor((filled - i) * 8 - 1))]
    else bar += "·"
  }
  return bar
}

/** 迷你图：数值序列 → ▁▂▃▄▅▆▇█（ZCode FrameChart sparkline；空/全零给平线） */
export function sparkline(values = [], width = 12) {
  const blocks = "▁▂▃▄▅▆▇█"
  const w = Math.max(1, Math.floor(width))
  const nums = (values || []).filter((n) => Number.isFinite(n)).map(Number)
  if (!nums.length) return "·".repeat(w)
  const max = Math.max(...nums)
  const min = Math.min(...nums)
  const span = max - min
  const out = []
  const seq = nums.slice(-w)
  for (const n of seq) {
    if (span === 0) out.push("▁") // 等值不放大（诚实呈现）
    else out.push(blocks[Math.min(7, Math.max(0, Math.round(((n - min) / span) * 7)))])
  }
  return out.join("")
}

/** 状态灯（ZCode STATUS_DOT：done 实点 / failed 实点+文字 / pending 空心 / running 脉冲） */
export function statusLamp(status) {
  switch (status) {
    case "done": return SYMBOLS.lampDone
    case "failed": return SYMBOLS.lampFailed
    case "running": return SYMBOLS.lampRunning
    case "pending":
    default: return SYMBOLS.lampPending
  }
}

/** 灯 + 文字通道（ZCode run-status-presentation：状态永不只靠颜色，必配文字） */
export function statusLabel(status) {
  switch (status) {
    case "done": return "完成"
    case "failed": return "失败"
    case "running": return "进行中"
    case "pending":
    default: return "待定"
  }
}

/** 计数行（ZCode WorkflowRosterParts：0 缺席） */
export function countLine(parts = []) {
  return parts
    .filter((p) => p && (p.n || 0) > 0)
    .map((p) => `${p.symbol || SYMBOLS.bullet} ${p.n}`)
    .join(" · ")
}

/** 段序统计条（done █ / failed ▓ / running ▒ / pending ░，按数量定宽） */
export function segmentBar(parts = [], width = 20) {
  const w = Math.max(1, Math.floor(width))
  const total = parts.reduce((n, p) => n + Math.max(0, Number(p.n) || 0), 0)
  if (!total) return "·".repeat(w)
  const order = [
    { key: "done", ch: "█" },
    { key: "failed", ch: "▓" },
    { key: "running", ch: "▒" },
    { key: "pending", ch: "░" },
  ]
  let bar = ""
  for (const seg of order) {
    const n = (parts.find((p) => p.key === seg.key)?.n) || 0
    if (!n) continue
    const cells = Math.max(n >= total ? w : 0, Math.round((n / total) * w))
    bar += seg.ch.repeat(cells)
  }
  return bar.slice(0, w).padEnd(w, "·")
}

/** 键帽渲染（ZCode Kbd：每键一颗 chip；终端用空格分隔 + 可选方括号） */
export function kbd(keys = [], { bracket = false } = {}) {
  const chips = (Array.isArray(keys) ? keys : [keys]).filter(Boolean).map((k) => KBD[String(k).toLowerCase()] || String(k))
  const text = chips.join(" ")
  return bracket ? `[${text}]` : text
}

/** 紧凑数字（ZCode formatCompactTokenCount：<1k 原样 / <1M → K / else M，去 .0） */
export function compactNumber(n) {
  const v = Number(n) || 0
  if (v < 1000) return String(v)
  if (v < 1_000_000) return `${(v / 1000).toFixed(1).replace(/\.0$/, "")}K`
  return `${(v / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`
}

/** 通栏分隔线（ZCode Separator：-mx-1 my-1 通栏） */
export function divider(width = 20) {
  return SYMBOLS.rail.repeat(Math.max(1, Math.floor(width)))
}
