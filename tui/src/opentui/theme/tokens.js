// 语义颜色 token —— 唯一颜色权威（组件禁止写字面量颜色，必须从这里取）。
// 终端主题按正文、表面、边框、状态和代码语法分组。
// 组件按语义选用文字、表面、边框、差异、Markdown 和代码语法颜色。
// 通用与科研模式分别使用浅蓝和浅紫；
// 辅助文本适当提高对比度，保证两种主题下实际文字均可读。
export const TOKENS = {
  // 文本三级（+ 选中态文本）
  text: "#e5e7eb",
  textStrong: "#f8fafc", // 选中/最强前景
  textMuted: "#94a3b8",
  textDim: "#7d8998",
  // 背景五级
  background: "#0f1419",
  panel: "#161b22",
  element: "#1f2937",
  menu: "#161b22",
  userMessage: "#30363d",
  // 边框三级
  border: "#3b4450",
  borderFocus: "#7dd3fc", // 焦点边框
  borderWarning: "#fbbf24",
  borderSubtle: "#26313d",
  // 语义色
  accent: "#7dd3fc",
  primary: "#7dd3fc",
  secondary: "#c4b5fd",
  success: "#86efac",
  warning: "#fbbf24",
  error: "#fca5a5",
  info: "#93c5fd",
  // 双模式品牌色
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
  markdownHeading: "#e5e7eb",
  markdownEmph: "#c4b5fd",
  markdownCodeBg: "#161b22",
  markdownText: "#e5e7eb",
  // 语法高亮家族
  syntaxComment: "#94a3b8",
  syntaxKeyword: "#7dd3fc",
  syntaxString: "#86efac",
  syntaxNumber: "#fbbf24",
  syntaxType: "#86efac",
  syntaxFunction: "#c4b5fd",
  syntaxVariable: "#e5e7eb",
  syntaxOperator: "#7dd3fc",
  syntaxPunctuation: "#94a3b8",
  codeBackground: "#161b22",
  // 思考内容
  thinkingOpacity: 0.68,
}

export const LIGHT_TOKENS = {
  text: "#111827",
  textStrong: "#0f172a", // 选中项前景
  textMuted: "#64748b",
  textDim: "#64748b",
  background: "#f8fafc",
  panel: "#ffffff",
  element: "#e2e8f0",
  menu: "#ffffff",
  userMessage: "#e0f2fe",
  border: "#cbd5e1",
  borderFocus: "#0284c1", // 焦点边框
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
  markdownHeading: "#0f172a",
  markdownEmph: "#7c3aed",
  markdownCodeBg: "#e2e8f0",
  markdownText: "#111827",
  syntaxComment: "#64748b",
  syntaxKeyword: "#0369a1",
  syntaxString: "#15803d",
  syntaxNumber: "#a16207",
  syntaxType: "#15803d",
  syntaxFunction: "#7c3aed",
  syntaxVariable: "#111827",
  syntaxOperator: "#0369a1",
  syntaxPunctuation: "#64748b",
  codeBackground: "#ffffff",
  thinkingOpacity: 0.72,
}

// 模式徽标配色（供侧栏/输入框标题/边框过渡使用）
export const MODE_TOKENS = {
  general: { key: "modeGeneral", label: "通用", symbol: "◇" },
  research: { key: "modeResearch", label: "科研", symbol: "◎" },
}

/** 字体栈含中文回退；终端字号与实际字形由宿主配置。 */
export const FONT_STACKS = {
  mono: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", monospace',
  sans: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif',
}

// Keep a separate immutable source: TOKENS is mutated by live theme switching.
export const DARK_TOKENS = Object.freeze({ ...TOKENS })

const CODE_PALETTES = {
  'github-dark': { syntaxComment: '#8b949e', syntaxKeyword: '#ff7b72', syntaxString: '#a5d6ff', syntaxNumber: '#79c0ff', syntaxFunction: '#d2a8ff', syntaxType: '#ffa657', syntaxVariable: '#c9d1d9', syntaxOperator: '#ff7b72', syntaxPunctuation: '#c9d1d9', codeBackground: '#0d1117' },
  github: { syntaxComment: '#57606a', syntaxKeyword: '#cf222e', syntaxString: '#0a3069', syntaxNumber: '#0550ae', syntaxFunction: '#8250df', syntaxType: '#953800', syntaxVariable: '#24292f', syntaxOperator: '#cf222e', syntaxPunctuation: '#24292f', codeBackground: '#ffffff' },
  'atom-one-dark': { syntaxComment: '#8b949e', syntaxKeyword: '#c678dd', syntaxString: '#98c379', syntaxNumber: '#d19a66', syntaxFunction: '#61afef', syntaxType: '#e5c07b', syntaxVariable: '#abb2bf', syntaxOperator: '#56b6c2', syntaxPunctuation: '#abb2bf', codeBackground: '#282c34' },
  'atom-one-light': { syntaxComment: '#626772', syntaxKeyword: '#a626a4', syntaxString: '#397300', syntaxNumber: '#986801', syntaxFunction: '#4078f2', syntaxType: '#986801', syntaxVariable: '#383a42', syntaxOperator: '#0184bc', syntaxPunctuation: '#383a42', codeBackground: '#fafafa' },
}

export function paletteFor(mode = "dark", codeTheme) {
  return { ...(mode === "light" ? LIGHT_TOKENS : DARK_TOKENS), ...(CODE_PALETTES[codeTheme] || {}) }
}

export function modeColor(palette, workspaceMode) {
  const t = MODE_TOKENS[workspaceMode] || MODE_TOKENS.general
  return palette[t.key]
}
