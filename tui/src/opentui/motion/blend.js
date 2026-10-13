// 颜色插值与 shimmer 微光 —— 纯函数，可无终端单测（对齐 ZCode app-motion.tsx 思路）
// shimmer：一个高亮带头扫过文本，逐字符用余弦函数计算与扫过带的距离→强度→base↔accent 插值

/** hex 线性插值，t∈[0,1] */
export function blendHex(a, b, t) {
  const pa = parseInt(a.slice(1), 16)
  const pb = parseInt(b.slice(1), 16)
  const c = [16, 8, 0].map((s) => {
    const ca = (pa >> s) & 0xff
    const cb = (pb >> s) & 0xff
    return Math.round(ca + (cb - ca) * Math.min(1, Math.max(0, t)))
  })
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`
}

/**
 * 产出一帧 shimmer 着色的逐字符颜色序列。
 * @param text 文本（按码点切分，CJK 安全）
 * @param frame 帧序号（外部以固定间隔递增）
 * @param options.accent 扫过色 / base 基色 / period 半影宽度(字符) / cycle 扫完一轮的帧数
 * @returns [{ch, fg}]
 */
export function shimmerText(text, frame, { accent = "#7dd3fc", base = "#e6edf3", period = 8, cycle = 64 } = {}) {
  const chars = [...text]
  const head = ((frame % cycle) / cycle) * (chars.length + period * 2) - period
  return chars.map((ch, i) => {
    const d = Math.abs(i - head)
    const t = Math.max(0, Math.cos((Math.PI * Math.min(d, period)) / period))
    return { ch, t, fg: blendHex(base, accent, t) }
  })
}
