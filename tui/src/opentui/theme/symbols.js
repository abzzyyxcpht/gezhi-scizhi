// 符号系统 —— 每个符号须注释选型理由；宽度口径以 string-width 为准（与 src/textfmt.py 一致）。
// 2026-10-13 起按键与提示词汇对齐 ZCode：KBD 映射取自 ZCode kbd/menu 契约
// （packages/ui DESIGN.md「Typography/Components/Kbd」与 apps/zcode-cli 菜单/状态行实现）：
// 每个键独立成「键帽」语义（ZCode Kbd 每键一颗 chip），组合键间 1 空格；提示用最小字号层。
export const SYMBOLS = {
  /** 状态点：U+25CF 实心圆，不用 U+23FA（部分终端 emoji 回退破版）；完成态静态展示用 */
  bullet: "●",
  /** 运行中 spinner 用盲文帧（见 motion/frames.js BRAILLE，与 ZCode SPINNER_FRAMES 同值） */
  pointer: "❯", // 选中指针 / 用户消息引导符（ZCode selection row 用 "> "，我们保留 ZCode 邻近 glyph）
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
  /** 流箭头（ZCode WorkflowStreamChevrons）*/
  chevrons: "›››",
  /** 双轨（并行两站之间的 twin rail，ZCode WorkflowRunRail）*/
  twinRail: "═",
  /** 单轨 */
  rail: "─",
}

export const BORDER = {
  rounded: "rounded", // 输入框 / 品牌卡（╭╮╰╯）
  single: "single", // 列表 / 面板（平直 ─│）
}

/**
 * 按键名 → ZCode 键帽 glyph（KBD）。
 * ZCode Kbd：h-5 min-w-5 px-1 rounded-sm，ui-xs font-medium，组合键每键一颗 chip 间隔 1 空格。
 * 终端的对应物：方括号包裹的短标签（[⌘K]）或空格分隔（⌘ ⇧ K），两者都保留在 hint 里。
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
  ctrl: "⌘", // ZCode 跨平台菜单快捷键统一用 ⌘ 表示 Cmd/Ctrl
  alt: "⌥",
  cmd: "⌘",
  delete: "Del",
}

/**
 * 把"Ctrl+P 命令面板"类提示渲染成 ZCode 键帽风格（Ctrl→⌘ 等）。
 * 只做词汇替换，不改动其余文案；已是 glyph 的原样保留。
 */
export function shortcutHint(text = "") {
  return String(text)
    .replace(/\bctrl\s*\+\s*([a-z0-9])/gi, (_, k) => `${KBD.ctrl}${k.toUpperCase()}`)
    .replace(/\bctrl\s+\+?\s*([a-z0-9])/gi, (_, k) => `${KBD.ctrl} ${k.toUpperCase()}`)
    .replace(/\bshift\s*\+\s*([a-z0-9])/gi, (_, k) => `${KBD.shift}${k.toUpperCase()}`)
    .replace(/\bescape\b|\besc\b/gi, KBD.esc)
    .replace(/\benter\b/gi, KBD.enter)
    .replace(/\btab\b/gi, KBD.tab)
    .replace(/\bup\s*\/\s*down\b|\bup\s*and\s*down\b/gi, `${KBD.up}${KBD.down}`)
}

/**
 * 提示文案词汇表（ZCode hint 语言：最短、键帽化、动词置前）。
 * footer/面板统一从这里取，禁止自创格式。
 */
export const HINTS = {
  navigate: `${KBD.up}${KBD.down} 选择 · ${KBD.enter} 确认 · ${KBD.esc} 取消`,
  dismiss: `${KBD.esc} 取消`,
  expand: `⌘O 展开`,
  collapse: `⌘O 收起`,
  confirm: `${KBD.enter} 确认 · ${KBD.esc} 取消`,
  scroll: `${KBD.up}${KBD.down} 滚动 · PgUp/PgDn 翻页 · ${KBD.esc} 关闭`,
}
