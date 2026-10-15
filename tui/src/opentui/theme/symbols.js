// 符号系统 —— 每个符号须注释选型理由；宽度口径以 string-width 为准（与 src/textfmt.py 一致）。
// 每个按键独立显示；组合键标签保留实际修饰键，提示使用次级文字层。
export const SYMBOLS = {
  /** 状态点：U+25CF 实心圆，不用 U+23FA（部分终端 emoji 回退破版）；完成态静态展示用 */
  bullet: "●",
  /** 运行中 spinner 用盲文帧（见 motion/frames.js BRAILLE） */
  pointer: "❯", // 选中指针 / 用户消息引导符
  success: "✓",
  failure: "✗",
  approval: "▶", // 审批/确认面板标题
  banner: "✦", // 横幅/品牌标签
  current: "← current", // 建议面板当前项标注
  modeGeneral: "◇", // 通用 · 开放网格
  modeResearch: "◎", // 科研 · 聚焦格点
  switchPending: "◌", // 切换过渡中间帧
  more: "…",
  truncated: "[truncated]",
  // ---- 状态灯（workflow timeline 词汇：● 完成 / ◐ 运行 / ○ 待定 / ✕ 失败 / ┈ 空洞）----
  lampDone: "●",
  lampRunning: "◐",
  lampPending: "○",
  lampFailed: "✕",
  lampHole: "┈",
  /** 流箭头*/
  chevrons: "›››",
  /** 双轨（并行两站之间的 twin rail）*/
  twinRail: "═",
  /** 单轨 */
  rail: "─",
}

export const BORDER = {
  rounded: "rounded", // 输入框 / 品牌卡（╭╮╰╯）
  single: "single", // 列表 / 面板（平直 ─│）
}

/**
 * 按键名 → 紧凑标签（KBD）。
 * 方括号短标签或空格分隔键名都可用；Control 与 Command 不混用。
 */
export const KBD = {
  esc: "⎋",
  enter: "⏎",
  tab: "⇥",
  backspace: "⌫",
  space: "␣",
  up: "↑",
  down: "↓",
  left: "←",
  right: "→",
  pageup: "PgUp",
  pagedown: "PgDn",
  home: "Home",
  end: "End",
  shift: "⇧",
  ctrl: "Ctrl", // Control 绑定在各终端平台保持一致，与 Command 区分
  alt: "⌥",
  cmd: "⌘",
  delete: "Del",
}

/**
 * 把快捷键提示规范为紧凑标签，保留实际的 Control 修饰键。
 * 只做词汇替换，不改动其余文案；已是 glyph 的原样保留。
 */
export function shortcutHint(text = "") {
  return String(text)
    .replace(/\bctrl\s*\+\s*([a-z0-9])/gi, (_, k) => `${KBD.ctrl}+${k.toUpperCase()}`)
    .replace(/\bctrl\s+\+?\s*([a-z0-9])/gi, (_, k) => `${KBD.ctrl} ${k.toUpperCase()}`)
    .replace(/\bshift\s*\+\s*([a-z0-9])/gi, (_, k) => `${KBD.shift}${k.toUpperCase()}`)
    .replace(/\bescape\b|\besc\b/gi, KBD.esc)
    .replace(/\benter\b/gi, KBD.enter)
    .replace(/\btab\b/gi, KBD.tab)
    .replace(/\bup\s*\/\s*down\b|\bup\s*and\s*down\b/gi, `${KBD.up}${KBD.down}`)
}

/**
 * 提示文案词汇表（最短、键帽化、动词置前）。
 * footer/面板统一从这里取，禁止自创格式。
 */
export const HINTS = {
  navigate: `${KBD.up}${KBD.down} 选择 · ${KBD.enter} 确认 · ${KBD.esc} 取消`,
  dismiss: `${KBD.esc} 取消`,
  expand: `${KBD.ctrl}+O 展开`,
  collapse: `${KBD.ctrl}+O 收起`,
  confirm: `${KBD.enter} 确认 · ${KBD.esc} 取消`,
  scroll: `${KBD.up}${KBD.down} 滚动 · PgUp/PgDn 翻页 · ${KBD.esc} 关闭`,
}
