// UI 排版刻度（ZCode DESIGN.md「Typography / Spacing」的终端映射，单一权威）。
// 来源：packages/ui DESIGN.md（--ui-font-size=14px 与 text-ui-* 刻度、4px 间距基数、类型角色表）。
// 终端没有 px 字号，故把刻度映射为「层级角色」：颜色层（text/textMuted/textDim）+ 权重（bold/常规）。
// 用法：typeStyle("sm") → { tier, strong }；组件禁止自造字号/层级。

/** ZCode 字号刻度（px 值，用于文档与测试对齐；终端只消费 tier 语义） */
export const TYPE_SCALE = {
  xl: { px: 18, role: "一级阅读标题（Markdown h1）" },
  lg: { px: 16, role: "二级阅读标题（Markdown h2）" },
  base: { px: 14, role: "正文/通用按钮/工作区标题等主要 UI 文本" },
  caption: { px: 13, role: "紧凑辅助说明" },
  sm: { px: 12, role: "次要说明/工具提示/Markdown 行内代码" },
  xs: { px: 10, role: "快捷键/徽标/计数器/极弱元数据" },
  "2xs": { px: 9, role: "仅限工作流图轴刻度（禁止用于内容）" },
}

/** ZCode 默认界面字号（Appearance 设置只改这一个变量） */
export const UI_FONT_SIZE = 14

/** 层级角色 → 终端实现（tier 取 theme tokens 的文本三级；strong=加粗） */
const ROLES = {
  xl: { tier: "textStrong", strong: true },
  lg: { tier: "text", strong: true },
  base: { tier: "text", strong: false },
  caption: { tier: "textMuted", strong: false },
  sm: { tier: "textMuted", strong: false },
  xs: { tier: "textDim", strong: false },
}

/** 取角色对应的终端样式（ZCode：字号与颜色层级是独立决策，这里保持独立参数） */
export function typeStyle(role = "base", { strong = null } = {}) {
  const r = ROLES[role] || ROLES.base
  return { role, tier: r.tier, strong: strong === null ? r.strong : Boolean(strong) }
}

/** ZCode 间距基数 4px → 终端列/行（tight=4px=1 格，compact=8px=2 格，dense=12px=3 格，panel=16px=4 格） */
export const SPACING = { tight: 1, compact: 2, dense: 3, panel: 4, section: 5 }

/** ZCode 控件高度阶梯（h-5..h-8 = 20/24/28/32px）→ 终端行内边距释义 */
export const CONTROL_HEIGHTS = { xs: 0, sm: 0, default: 1, lg: 2 }

/** ZCode Button size 契约（packages/ui/src/components/ui/button.tsx） */
export const BUTTON_SIZES = {
  xs: { h: 20, rounded: "sm", px: 2, gap: 1, icon: 2.5 },
  sm: { h: 24, rounded: "md", px: 2, icon: 3 },
  default: { h: 28, rounded: "md", px: 2, gap: 1, icon: 3.5 },
  lg: { h: 32, rounded: "lg", px: 2.5, gap: 1, icon: 4 },
  icon: { h: 28, w: 28, rounded: "md" },
  "icon-lg": { h: 32, w: 32, rounded: "lg" },
  "icon-sm": { h: 24, w: 24, rounded: "md" },
  "icon-xs": { h: 20, w: 20, rounded: "sm" },
}
