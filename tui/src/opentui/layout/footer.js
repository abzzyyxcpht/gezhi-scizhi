// Footer 两行槽位系统 —— 纯逻辑
// 行 1：可配置槽位 + 右侧 expand 提示 + SWRR 技巧轮转（tick 驱动，非墙钟）
// 行 2：左侧预警 + 右侧上下文占用

export const DEFAULT_SLOTS = ["mode", "model", "tasks", "cwd", "git"]

/** SWRR（加权平滑轮转）技巧选择器：按权重平滑轮转且不错位重复 */
export function createTipRotator(tips, { periodMs = 10000 } = {}) {
  // nginx SWRR 思路：每次取当前权重+步进最大者，实现平滑加权轮转
  let current = tips.map(() => 0)
  return {
    /** tick(nowMs) 返回当前应显示的技巧索引（只随 periodMs 边界变化） */
    indexAt(nowMs) {
      if (!tips.length) return -1
      const steps = Math.floor(nowMs / periodMs)
      let idx = 0
      const weights = tips.map((t) => t.weight ?? 1)
      const state = tips.map(() => 0)
      for (let s = 0; s <= steps; s++) {
        let best = -1
        for (let i = 0; i < tips.length; i++) {
          state[i] += weights[i]
          if (best === -1 || state[i] > state[best]) best = i
        }
        state[best] -= weights.reduce((a, b) => a + b, 0)
        idx = best
      }
      current = state
      return idx
    },
    /** 预演接下来 n 个周期的序列（测试与文档用） */
    preview(n = 10, startMs = 0) {
      const seq = []
      for (let k = 0; k < n; k++) seq.push(this.indexAt(startMs + k * periodMs))
      return seq
    },
  }
}

/** 槽位值裁剪：窄终端/footer 单行不折行（mode/cwd 等） */
export function clipSlot(value, max = 14) {
  const s = String(value)
  const chars = [...s]
  return chars.length > max ? `${chars.slice(0, max - 1).join("")}…` : s
}

/** 行 1 槽位渲染数据源：槽位值存在才占位（无 git 仓库则不显示 git 槽） */
export function resolveSlots(slotValues, order = DEFAULT_SLOTS) {
  return order.filter((s) => slotValues[s] != null && slotValues[s] !== "").map((s) => ({ slot: s, value: slotValues[s] }))
}

/**
 * 上下文占用文案：`context: 47% (128.4K/272K)`。
 * 紧凑数字格式：
 * <1k 原样 / <1M → 一位小数 K（去 .0）/ else M；使用大写 K/M。
 */
export function formatContextUsage(used, total) {
  const pct = total > 0 ? Math.round((used / total) * 100) : 0
  const fmt = (n) => {
    const v = Number(n) || 0
    if (v < 1000) return `${v}`
    if (v < 1_000_000) return `${(v / 1000).toFixed(1).replace(/\.0$/, "")}K`
    return `${(v / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`
  }
  return `context: ${pct}% (${fmt(used)}/${fmt(total)})`
}

/** 行 2 预警：/goal 超长等；返回 null 表示无预警 */
export function resolveWarning({ goalLength, goalMax = 200 } = {}) {
  if (goalLength && goalLength > goalMax) return `⚠ /goal 超长 ${goalLength}/${goalMax}，建议精简`
  return null
}
