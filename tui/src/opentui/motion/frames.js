// 帧序列与切换过渡编排 —— 纯函数/纯数据，可无终端单测
// 双 spinner 语义化：盲文=工作中，静态点=完成态（避免每帧重绘闪烁）

/** 盲文 spinner：80ms/帧，10 帧一轮（思考/等待/流式） */
export const BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
export const BRAILLE_FRAME_MS = 80

/** 模式切换回执帧（对齐既有 workspace-mode-feedback 语义 ·◌●✓，80ms/帧） */
export const SWITCH_FRAMES = ["·", "◌", "●", "✓"]
export const SWITCH_FRAME_MS = 80

/** 切换过渡总时长（测试契约：既有 MODE_TRANSITION_MS 同名导出） */
export const MODE_TRANSITION_MS = SWITCH_FRAMES.length * SWITCH_FRAME_MS

/**
 * 双模式切换过渡编排（纯函数）：给定起点/终点/进度 t∈[0,1]，返回各环节进度。
 * 环节：logo 交叠淡入(0-0.5) → 边框/标题换色(0.2-0.7) → 命令 stagger(0.3-1)
 * 用于 Phase 3 组件按 t 插值；reducedMotion 时调用方直接取 t=1。
 */
export function transitionPlan(t) {
  const clamp01 = (v) => Math.min(1, Math.max(0, v))
  const seg = (a, b) => clamp01((t - a) / (b - a))
  return {
    logoCrossfade: seg(0, 0.5),
    chromeTint: seg(0.2, 0.7),
    commandStagger: seg(0.3, 1),
    done: t >= 1,
  }
}

/** stagger 递延：第 i 条在窗口 [0,1] 内按 30ms 递延出现 */
export function staggerDelayMs(index, stepMs = 30) {
  return index * stepMs
}

/** spinner 当前帧字符 */
export function brailleFrame(frameIndex) {
  return BRAILLE[((frameIndex % BRAILLE.length) + BRAILLE.length) % BRAILLE.length]
}

/**
 * 启动屏图案（双模式品牌设计）：
 *   通用 = 开放格点（四散星点，呼应"开放"）；科研 = 聚焦格点（向心汇聚，呼应"聚焦"）
 * 逐行揭示动画：第 frame 帧显示前 N 行（reducedMotion 由调用方取全量）。
 */
export const STARTUP_ART = {
  general: [
    "·  ✦  ·  ✦  ·  ✦  ·  ✦",
    "   ╭───────────────────╮",
    "   │                   │",
    "   │      格   致      │",
    "   │      SciZhi       │",
    "   │                   │",
    "   ╰───────────────────╯",
    "·  ✦  ·  ✦  ·  ✦  ·  ✦",
  ],
  research: [
    "           ✦",
    "         · ✦ ·",
    "      ╭─────────╮",
    "      │         │",
    "      │  格 致  │",
    "      │  SciZhi │",
    "      │         │",
    "      ╰─────────╯",
    "         · ✦ ·",
    "           ✦",
  ],
}

/** 逐行揭示：第 frame 帧应显示的图案行数（每 3 帧揭示一行，封顶全量） */
export function revealStartupArt(mode, frame) {
  const art = STARTUP_ART[mode] || STARTUP_ART.general
  const rows = Math.min(art.length, Math.floor(frame / 3) + 1)
  return art.slice(0, rows)
}

/** 启动屏底部脉冲点（连接等待感）：3 帧循环 */
export const PULSE_DOTS = ["·", "●", "·"]
export function pulseDot(frameIndex) {
  return PULSE_DOTS[((frameIndex % PULSE_DOTS.length) + PULSE_DOTS.length) % PULSE_DOTS.length]
}
