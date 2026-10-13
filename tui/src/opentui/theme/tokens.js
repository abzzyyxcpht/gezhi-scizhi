// 语义颜色 token —— 唯一颜色权威（组件禁止写字面量颜色，必须从这里取）。
// 2026-10-13 起**全量对齐 ZCode 官方终端主题**：值取自 ZCode 仓库
// apps/zcode-cli/packages/tui/src/theme/defaults.ts（DARK_TUI_THEME / LIGHT_TUI_THEME），
// 该文件是 ZCode CLI/TUI 的成文终端令牌（TuiThemeTokens，57 项），与 packages/ui 的
// DESIGN.md 令牌语义一一对应（primary/accent、text/textMuted、border/borderActive/borderSubtle、
// diff*/markdown*/syntax*、thinkingOpacity）。
// 模式色（通用/科研）取 ZCode 的 primary(sky-300) 与 secondary(violet-300)；
// 终端可读性适配：深色底 dim 文本用 slate-500 #64748b（对应 DESIGN.md foreground-subtlest 40%）。
export const TOKENS = {
  // 文本三级（+ 选中态文本）
  text: "#e5e7eb",
  textStrong: "#f8fafc", // ZCode selectedListItemText：选中/最强前景
  textMuted: "#94a3b8",
  textDim: "#64748b",
  // 背景五级
  background: "#0f1419",
  panel: "#161b22",
  element: "#1f2937",
  menu: "#161b22",
  userMessage: "#30363d",
  // 边框三级
  border: "#3b4450",
  borderFocus: "#7dd3fc", // ZCode borderActive
  borderWarning: "#fbbf24",
  borderSubtle: "#26313d",
  // 语义色（ZCode primary/secondary/accent/info/error/warning/success）
  accent: "#7dd3fc",
  primary: "#7dd3fc",
  secondary: "#c4b5fd",
  success: "#86efac",
  warning: "#fbbf24",
  error: "#fca5a5",
  info: "#93c5fd",
  // 双模式品牌色（ZCode primary=sky-300 / secondary=violet-300）
  modeGeneral: "#7dd3fc", // 通用 · 开放格点蓝
  modeResearch: "#c4b5fd", // 科研 · 聚焦格点紫
  // shell 模式（! 前缀）
  shellMode: "#c4b5fd",
  // diff 五色
  diffAdded: "#86efac",
  diffAddedBg: "#12351e",
  diffRemoved: "#fca5a5",
  diffRemovedBg: "#3b1717",
  diffHunk: "#93c5fd",
  diffContext: "#e5e7eb",
  diffHighlightAdded: "#bbf7d0",
  diffHighlightRemoved: "#fecaca",
  diffLineNumber: "#94a3b8",
  // markdown 家族
  markdownCode: "#86efac",
  markdownLink: "#7dd3fc",
  markdownStrong: "#e5e7eb",
  markdownBlockQuote: "#fbbf24",
  markdownListItem: "#7dd3fc",
  markdownHorizontalRule: "#3b4450",
  // 语法高亮家族
  syntaxComment: "#94a3b8",
  syntaxKeyword: "#7dd3fc",
  syntaxString: "#86efac",
  syntaxNumber: "#fbbf24",
  syntaxType: "#86efac",
  // 思考内容
  thinkingOpacity: 0.68,
}

export const LIGHT_TOKENS = {
  text: "#111827",
  textStrong: "#0f172a", // ZCode selectedListItemText
  textMuted: "#64748b",
  textDim: "#94a3b8",
  background: "#f8fafc",
  panel: "#ffffff",
  element: "#e2e8f0",
  menu: "#ffffff",
  userMessage: "#e0f2fe",
  border: "#cbd5e1",
  borderFocus: "#0284c1", // ZCode borderActive
  borderWarning: "#a16207",
  borderSubtle: "#e5e7eb",
  accent: "#0369a1",
  primary: "#0369a1",
  secondary: "#7c3aed",
  success: "#15803d",
  warning: "#a16207",
  error: "#b91c1c",
  info: "#2563eb",
  modeGeneral: "#0369a1",
  modeResearch: "#7c3aed",
  shellMode: "#7c3aed",
  diffAdded: "#166534",
  diffAddedBg: "#dcfce7",
  diffRemoved: "#991b1b",
  diffRemovedBg: "#fee2e2",
  diffHunk: "#1d4ed8",
  diffContext: "#111827",
  diffHighlightAdded: "#15803d",
  diffHighlightRemoved: "#b91c1c",
  diffLineNumber: "#64748b",
  markdownCode: "#15803d",
  markdownLink: "#0369a1",
  markdownStrong: "#111827",
  markdownBlockQuote: "#a16207",
  markdownListItem: "#0369a1",
  markdownHorizontalRule: "#cbd5e1",
  syntaxComment: "#64748b",
  syntaxKeyword: "#0369a1",
  syntaxString: "#15803d",
  syntaxNumber: "#a16207",
  syntaxType: "#15803d",
  thinkingOpacity: 0.72,
}

// 模式徽标配色（供侧栏/输入框标题/边框过渡使用）
export const MODE_TOKENS = {
  general: { key: "modeGeneral", label: "通用", symbol: "◇" },
  research: { key: "modeResearch", label: "科研", symbol: "◎" },
}

/** 字体栈（ZCode font-mono/font-sans 原文，含 CJK 兜底防宋体回落；终端只作说明用） */
export const FONT_STACKS = {
  mono: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", monospace',
  sans: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif',
}

export function paletteFor(mode = "dark") {
  return mode === "light" ? { ...LIGHT_TOKENS } : { ...TOKENS }
}

export function modeColor(palette, workspaceMode) {
  const t = MODE_TOKENS[workspaceMode] || MODE_TOKENS.general
  return palette[t.key]
}
