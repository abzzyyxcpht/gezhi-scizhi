// OpenTUI 运行时 —— 真终端入口（0.9.0 起唯一交互引擎，由 cli.js 直接分发到这里）
// 键盘路由 → composer → 切换过渡 → AppShell 渲染；会话交互经 session-controller 走 bridge
// 运行: node src/cli.js
import React, { useEffect, useRef, useState } from "react"
import { createRoot, useKeyboard, useTerminalDimensions, useRenderer, usePaste } from "@mbears/opentui-react"
import { createCliRenderer } from "@mbears/opentui-core"
import { AppShell } from "./app-shell.js"
import { brandHeaderRows } from "./components/brand-header.js"
import { createKeyRouter, resolveEscape, resolveCtrlCIntent } from "./keyboard/router.js"
import { MODE_TOKENS, modeColor } from "./theme/tokens.js"
import { SWITCH_FRAMES, SWITCH_FRAME_MS } from "./motion/frames.js"
import { createSessionController } from "./session-controller.js"
import { completionCandidates, completionWindow } from "./components/slash-suggest.js"
import { parseMentionQuery, listDirEntries, fileCandidates } from "./components/file-mention.js"
import { PalettePanel, palettePanelGeometry } from "./components/palette.js"
import { ApprovalPanel, APPROVAL_OPTIONS } from "./components/approval-panel.js"
import { ResearchStepsPanel, RESEARCH_STEPS } from "./components/research-steps.js"
import { ResearchInsightPanel, insightPanelRows } from "./components/research-panels.js"
import { ConfigPanel, flattenConfig, configRowModel, configPanelGeometry, configScrollFor } from "./components/config-panel.js"
import { ConnectionsPanel, connectionsRowModel, connectionsGeometry, connectionsScrollFor, connectionsCursorRow } from "./components/connections-panel.js"
import { ResearchTimelinePanel, timelineRowModel, timelineGeometry, timelineScrollFor, timelineCursorRow } from "./components/research-timeline.js"
import { ShortcutsPanel, shortcutsGeometry, shortcutsScrollFor } from "./components/shortcuts-panel.js"
import { HistorySearchPanel, filterHistory } from "./components/history-search-panel.js"
import { ReaderPanel, jumpToMatch } from "./components/reader.js"
import { TodosPanel } from "./components/todos-panel.js"
import { ContextPanel } from "./components/context-panel.js"
import { ModelPickerPanel, modelPickerGeometry } from "./components/model-picker.js"
import { GateMenuPanel, GATE_MENU_ROWS } from "./components/gate-menu.js"
import { REVIEW_MENU } from "../commands.js"
import { StartupScreen } from "./components/startup-screen.js"
import { SubagentsPanel, SubagentView } from "./components/subagent-view.js"
import { chromeRows, clampScrollOffset } from "./components/transcript.js"
import { COMMANDS, parseCommand } from "../commands.js"
import { paletteCommands, CONTROL_COMMANDS } from "../command-palette.js"
import { commandsForMode } from "../workspace-mode.js"
import { TOKENS } from "./theme/tokens.js"
import { applyTheme, currentTheme, detectTheme, watchTheme } from "./theme/theme.js"
import { SYMBOLS } from "./theme/symbols.js"
import { insertText, normalizePaste } from "../composer.js"
import { safeCommandEcho } from "../sensitive-text.js"
import { normalizeAppearance } from "./theme/preferences.js"
import { sidebarLayoutForTerminal, contentWidthForTerminal } from "./layout/width-budget.js"
import { prepareTranscriptRows } from "./components/markup-lines.js"

const h = React.createElement

// useRenderer 在无 renderer 上下文时返回 null（测试桩）
function useRendererSafe() {
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useRenderer()
  } catch {
    return null
  }
}

function motionEnabled() {
  return !process.env.GEZHI_REDUCED_MOTION
}

// 科研分析面板命令集（本地打开，不经过引擎排队）
const INSIGHT_COMMANDS = new Set(["hypotheses", "consensus", "matrix", "experiments", "facts"])
const INSIGHT_LABEL = { hypotheses: "假设排行榜", consensus: "共识计量", matrix: "证据矩阵", experiments: "实验树", facts: "世界模型" }

/** 主组件：持有会话状态，键盘经路由分层分发 */
export function OpenTuiApp({ onExit, reducedMotion: forceReducedMotion = !motionEnabled(), controller: controllerProp, autoResume = false } = {}) {
  const [appearance, setAppearance] = useState(() => normalizeAppearance())
  const [appearanceReady, setAppearanceReady] = useState(false)
  const appearanceReadyRef = useRef(false)
  const markAppearanceReady = () => {
    if (!appearanceReadyRef.current) { appearanceReadyRef.current = true; setAppearanceReady(true) }
  }
  const appearanceRef = useRef(appearance)
  appearanceRef.current = appearance
  const preferenceRevision = useRef(0)
  const preferenceReadSequence = useRef(0)
  const preferenceWrites = useRef(0)
  const reducedMotion = forceReducedMotion || !appearance.ui_animations || !appearanceReady
  const [workspaceMode, setWorkspaceMode] = useState("general")
  const [switching, setSwitching] = useState(null) // {to, frame}
  const [shimmerFrame, setShimmerFrame] = useState(null)
  const [draft, setDraft] = useState("")
  const [cursor, setCursor] = useState(0) // 光标位置（码点索引）
  const cursorRef = useRef(0) // 同步镜像：避免一批按键内闭包读到旧值
  const draftRef = useRef("") // draft 同步镜像（left/right/end 取最新长度）
  draftRef.current = draft
  const applyCursor = (c) => {
    cursorRef.current = c
    setCursor(c)
  }
  const [history, setHistory] = useState([])
  const historyIdx = useRef(-1)
  const historyDraft = useRef("")
  const [lines, setLines] = useState([
    { text: "格致 SciZhi（OpenTUI 引擎）· 输入消息回车发送，Ctrl+G 切换模式" },
  ])
  const [sidebarSections, setSidebarSections] = useState([])
  // 忙时排队的消息；ref 为权威数据，state 仅渲染镜像
  const queuedRef = useRef([])
  const [queued, setQueued] = useState([])
  const flushingRef = useRef(false)
  const pushQueue = (text) => {
    queuedRef.current = [...queuedRef.current, text]
    setQueued(queuedRef.current)
  }
  const shiftQueue = () => {
    queuedRef.current = queuedRef.current.slice(1)
    setQueued(queuedRef.current)
  }
  const [status, setStatus] = useState("就绪")
  const [streaming, setStreaming] = useState(false)
  const [spinnerFrame, setSpinnerFrame] = useState(0)
  const dims = useTerminalDimensions()
  const [sidebarUserHidden, setSidebarUserHidden] = useState(false)
  const [sidebarOverlayOpen, setSidebarOverlayOpen] = useState(false)
  const [sidebarScroll, setSidebarScroll] = useState(0)
  const sidebarLayout = sidebarLayoutForTerminal(dims.width, { mode: appearance.sidebar_mode, userHidden: sidebarUserHidden })
  const displayLineCount = React.useMemo(() => prepareTranscriptRows(lines,
    contentWidthForTerminal(dims.width, { sidebarVisible: sidebarLayout === "docked" }), appearance).length,
    [lines, dims.width, sidebarLayout, appearance.code_show_line_numbers, appearance.code_wrap_long_lines])
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState("")
  const [paletteIndex, setPaletteIndex] = useState(0)
  const [suggestIndex, setSuggestIndex] = useState(0)
  const [mentionIndex, setMentionIndex] = useState(0)
  const cwdRef = useRef(typeof process !== "undefined" ? process.cwd() : ".")
  const [scrollOffset, setScrollOffset] = useState(0)
  const [themeMode, setThemeMode] = useState(() => currentTheme())
  const [, refreshThemeTokens] = useState(0)
  const [expanded, setExpanded] = useState(false) // Ctrl+O 全局展开
  const expandedRef = useRef(false)
  const [stepsOpen, setStepsOpen] = useState(false) // 科研步骤面板
  const [insight, setInsight] = useState(null) // 科研分析面板 {kind, data, index, loading, error}
  const [configOpen, setConfigOpen] = useState(false) // 配置面板（/config）
  const [configValues, setConfigValues] = useState({}) // 配置面板当前值
  const [configIndex, setConfigIndex] = useState(0)
  const [configScroll, setConfigScroll] = useState(0) // config 面板窗口化滚动起点
  const [connectionsOpen, setConnectionsOpen] = useState(false) // 连接总览面板（/connections）
  const [connectionsData, setConnectionsData] = useState(null) // {connections, profiles, routes, active*}
  const [connectionsIndex, setConnectionsIndex] = useState(0) // 条目序号（光标只落条目行）
  const [connectionsScroll, setConnectionsScroll] = useState(0) // 窗口化滚动起点
  const [timelineOpen, setTimelineOpen] = useState(false) // 科研时间线面板（/timeline）
  const [timelineData, setTimelineData] = useState(null) // {goal, stages, counts}
  const [timelineIndex, setTimelineIndex] = useState(0) // 站点序号
  const [timelineScroll, setTimelineScroll] = useState(0) // 窗口化滚动起点
  const [modelPicker, setModelPicker] = useState(null) // 科研模型配置面板（/models）
  const [shortcutsOpen, setShortcutsOpen] = useState(false) // 快捷键帮助（F1）
  const [shortcutsScroll, setShortcutsScroll] = useState(0) // 快捷键面板窗口化滚动起点
  const [historySearch, setHistorySearch] = useState(null) // 历史搜索 {query, index}（Ctrl+R）
  const [todosOpen, setTodosOpen] = useState(false) // 待办面板（Ctrl+T）
  const [todosList, setTodosList] = useState([])
  const [goalText, setGoalText] = useState("") // 研究目标徽标
  const [steerArmed, setSteerArmed] = useState(false) // Ctrl+S 回合补充指导待输入
  const [planMode, setPlanMode] = useState(false) // Plan 模式徽标
  const [contextOpen, setContextOpen] = useState(false) // 上下文分解面板
  const [contextBreakdown, setContextBreakdown] = useState(null)
  const [reader, setReader] = useState(null) // 全屏阅读器 {title, lines, offset, query, searchMode}
  const [pluginCommands, setPluginCommands] = useState([]) // 插件命令（面板候选）
  const readerRows = (r) => Math.max(3, (dims.height || 24) - 10)
  const [subagentsOpen, setSubagentsOpen] = useState(false)
  const [subagentsList, setSubagentsList] = useState([])
  const [subagentsIndex, setSubagentsIndex] = useState(0)
  const [subagentDrill, setSubagentDrill] = useState(null) // 只读下钻状态
  const [booting, setBooting] = useState(true) // 启动屏：桥接就绪前
  const [bootError, setBootError] = useState("")
  const [stepsIndex, setStepsIndex] = useState(0)
  const [approval, setApproval] = useState(null) // {tool, callId, reason}
  const [approvalIndex, setApprovalIndex] = useState(0)
  const decidingRef = useRef(false)
  const [gateMenu, setGateMenu] = useState(null) // 人工确认关口 {taskId}（waiting_human）
  const [gateIndex, setGateIndex] = useState(0)
  const scrollOffsetRef = useRef(0) // 回看锚定：新行到达时补偿偏移（视口贴住原内容）
  const linesLenRef = useRef(0)
  const timers = useRef([])
  const insightSeq = useRef(0) // 面板代令牌：Esc/重开使旧代 fetch 结果丢弃（防异步复活）
  // 科研分析面板：打开时按 kind 异步拉取（notebook_list / evidence_brief），失败降级提示不阻塞
  const closeInsight = () => {
    insightSeq.current += 1
    setInsight(null)
  }
  const openConfig = async () => {
    const ctrl = controllerRef.current
    const revision = preferenceRevision.current
    const readSequence = ++preferenceReadSequence.current
    setConnectionsOpen(false) // 与连接总览互斥（同一 block 位）
    const values = (await ctrl?.configItems?.()) || {}
    if (revision !== preferenceRevision.current || preferenceWrites.current) return
    if (readSequence === preferenceReadSequence.current) {
      const next = normalizeAppearance(values)
      appearanceRef.current = next
      setConfigValues(values)
      setAppearance(next)
    } else {
      // Still open the requested panel, using the newer appearance snapshot.
      setConfigValues({ ...values, ...appearanceRef.current })
    }
    markAppearanceReady()
    setConfigIndex(0)
    setConfigScroll(0)
    setConfigOpen(true)
    setStatus("配置面板（↑↓ 选择 · Enter 修改 · Esc 关闭）")
  }
  // The revision guards in-flight reads; a single-key patch never replaces
  // settings edited by the desktop while this terminal is running.
  const savePreference = async (key, value) => {
    const revision = ++preferenceRevision.current
    preferenceWrites.current++
    try {
      const result = (await controllerRef.current?.configSet?.(key, value)) || { ok: true }
      const applied = result.ok && revision === preferenceRevision.current
      if (applied) {
        const next = normalizeAppearance({ ...appearanceRef.current, [key]: value })
        appearanceRef.current = next
        setAppearance(next)
        markAppearanceReady()
        setConfigValues((v) => ({ ...v, [key]: value }))
      }
      return { ...result, applied }
    } finally { preferenceWrites.current-- }
  }
  /** 连接总览（/connections）：只读面板，数据 model_registry.describe 派生 */
  const openConnections = async () => {
    const ctrl = controllerRef.current
    setConfigOpen(false) // 与配置面板互斥（同一 block 位）
    setStatus("连接总览（加载中…）")
    const data = (await ctrl?.connections?.()) || { connections: [], profiles: [], routes: {} }
    setConnectionsData(data)
    setConnectionsIndex(0)
    setConnectionsScroll(0)
    setConnectionsOpen(true)
    const count = (data.connections?.length || 0) + (data.profiles?.length || 0)
    setStatus(`连接总览（${count} 项 · ↑↓ 移动 · Esc 关闭）`)
  }
  const closeConnections = () => {
    setConnectionsOpen(false)
    setStatus("已关闭连接总览")
  }
  const moveConnections = (dir) => {
    setConnectionsIndex((i) => {
      const rows = connectionsRowModel(connectionsData)
      const count = rows.filter((r) => r.kind === "item").length || 1
      const next = Math.max(0, Math.min((Number.isInteger(i) ? i : 0) + dir, count - 1))
      const { maxVisibleRows } = connectionsGeometry(dims.height, connectionsData)
      setConnectionsScroll((s) => connectionsScrollFor(rows, connectionsCursorRow(rows, next), s, maxVisibleRows))
      return next
    })
  }
  /** 科研时间线（/timeline）：只读流水线一览，站内光标 + 空洞提示 */
  const openTimeline = async () => {
    const ctrl = controllerRef.current
    setStatus("科研时间线（加载中…）")
    const data = (await ctrl?.timeline?.()) || { goal: "", stages: [], counts: [] }
    setTimelineData(data)
    setTimelineIndex(0)
    setTimelineScroll(0)
    setTimelineOpen(true)
    const done = (data.stages || []).filter((s) => s.status === "done").length
    setStatus(`科研时间线（${done}/${(data.stages || []).length} 阶段已有数据 · ↑↓ 移动 · Esc 关闭）`)
  }
  const closeTimeline = () => {
    setTimelineOpen(false)
    setStatus("已关闭科研时间线")
  }
  const moveTimeline = (dir) => {
    setTimelineIndex((i) => {
      const rows = timelineRowModel(timelineData)
      const count = rows.filter((r) => r.kind === "station" || r.kind === "hole").length || 1
      const next = Math.max(0, Math.min((Number.isInteger(i) ? i : 0) + dir, count - 1))
      const { maxVisibleRows } = timelineGeometry(dims.height, timelineData)
      setTimelineScroll((s) => timelineScrollFor(rows, timelineCursorRow(rows, next), s, maxVisibleRows))
      return next
    })
  }
  /** 配置项 Enter：bool 取反 / cycle 循环 / model 切下一个 / number 微调 */
  const cycleConfig = async () => {
    const rows = flattenConfig()
    const item = rows[Math.min(configIndex, rows.length - 1)]
    if (!item) return
    const current = configValues[item.key]
    let next
    if (item.type === "bool") next = !current
    else if (item.type === "cycle") {
      const opts = item.options || []
      next = opts[(opts.indexOf(current) + 1) % opts.length] ?? opts[0]
    } else if (item.type === "model") {
      const models = configValues.models || []
      if (models.length) next = models[(models.indexOf(current) + 1) % models.length]
      else return setStatus("（无可选模型，请先 /connections 配置）")
    } else if (item.type === "number") {
      next = Math.round(Math.min(1, Math.max(0, Number(current) + 0.02)) * 100) / 100
    }
    if (next === undefined) return
    const r = await savePreference(item.key, next)
    setStatus(r?.ok ? `✓ ${item.label} → ${String(next)}` : `✗ 保存失败：${r?.error || "未知"}`)
    if (r?.applied) {
      if (item.key === "sidebar_mode") {
        setSidebarUserHidden(false)
        setSidebarOverlayOpen(false)
      }
    }
  }
  // 模型选择器：控制器持有状态机，runtime 只做打开/保存/按键镜像
  const pickerState = () => controllerRef.current?.modelPicker ?? null
  const openModelPicker = () => {
    const ctrl = controllerRef.current
    const state = ctrl?.modelPickerOpen?.()
    if (state?.remote) {
      setLines((ls) => [...ls, { text: "模型配置请在主机本机 TUI 或桌面操作。", dim: true }])
      return
    }
    if (!state) return // 已关闭/被新面板取代：迟到响应不复活面板
    setModelPicker(state)
    setStatus("模型配置（↑↓ 选择 · Tab 切换 · Enter 保存 · Esc 返回）")
  }
  const saveModelPicker = async () => {
    const ctrl = controllerRef.current
    const r = await ctrl?.modelPickerSave?.()
    if (r?.ok) {
      setModelPicker(null)
      const target = r.catalog.items.find((item) => item.id === r.catalog.active_id)
      setLines((ls) => [...ls, { text: `模型配置已保存 · ${r.catalog.stage} → ${target?.label || r.catalog.active_id}` }])
      if (r.catalog.scope) setLines((ls) => [...ls, { text: r.catalog.scope, dim: true }])
      setStatus("就绪")
      return
    }
    if (r?.remote) { setModelPicker(null); return }
    setModelPicker(pickerState()) // 失败：保留面板并刷新错误行
  }
  const moveModelPicker = (dir) => {
    controllerRef.current?.modelPickerMove?.(dir)
    setModelPicker(pickerState())
  }
  const tabModelPicker = () => {
    controllerRef.current?.modelPickerTab?.()
    setModelPicker(pickerState())
  }
  const closeModelPicker = () => {
    controllerRef.current?.modelPickerClose?.()
    setModelPicker(null)
    setStatus("已关闭模型配置")
  }
  const refreshModelPicker = async () => {
    const stage = modelPicker?.stage || "planning"
    const state = await controllerRef.current?.modelPickerOpen?.(stage)
    if (state && !state.remote) setModelPicker(state)
  }
  // 控制器异步加载完成/失败时推送新状态（迟到结果已被代令牌丢弃，不会复活）
  useEffect(() => controllerRef.current?.onPickerState?.((state) => setModelPicker(state)), [])
  const openInsight = async (kind) => {
    const ctrl = controllerRef.current
    const seq = (insightSeq.current += 1)
    setInsight({ kind, data: kind === "consensus" ? null : [], index: 0, loading: true, error: "" })
    setStatus(`加载${INSIGHT_LABEL[kind] || "面板"}…`)
    const fetchers = {
      hypotheses: () => ctrl?.hypotheses?.(),
      consensus: () => ctrl?.consensus?.(),
      matrix: () => ctrl?.matrix?.(),
      experiments: () => ctrl?.experimentTree?.(),
      facts: () => ctrl?.facts?.(),
    }
    try {
      const data = (await fetchers[kind]?.()) ?? (kind === "consensus" ? null : [])
      if (insightSeq.current !== seq) return // 已关闭或被新面板取代：丢弃过期数据
      setInsight({ kind, data, index: 0, loading: false, error: "" })
      setStatus(`${INSIGHT_LABEL[kind] || "面板"}（↑↓ 选择 · Esc 关闭）`)
    } catch (error) {
      if (insightSeq.current !== seq) return
      setInsight({ kind, data: kind === "consensus" ? null : [], index: 0, loading: false, error: String(error?.message || error).slice(0, 60) })
      setStatus("面板加载失败")
    }
  }
  const renderer = typeof process !== "undefined" && process.env.NODE_ENV === "test" ? null : useRendererSafe()
  const lastCtrlCAt = useRef(0)
  const controllerRef = useRef(null)
  if (!controllerRef.current && controllerProp) controllerRef.current = controllerProp

  // slash 补全候选（草稿以 / 开头且未输入空格时打开）
  const shellMode = draft.startsWith("!") // shell 模式
  const suggestOpen = !shellMode && draft.startsWith("/") && !draft.includes(" ") && draft.length >= 1
  const candidates = React.useMemo(
    () => (suggestOpen ? completionCandidates(draft, workspaceMode,
        { busy: streaming, remote: controllerRef.current?.connectionMode === "remote" }) : []),
    [draft, workspaceMode, streaming, suggestOpen]
  )
  // @ 文件补全（草稿末尾有未闭合 @query 时打开；Esc 关闭后同 query 不重开）
  const mentionQuery = parseMentionQuery(draft)
  const mentionLastQuery = useRef(null)
  const [mentionHidden, setMentionHidden] = useState(false)
  if (mentionQuery !== mentionLastQuery.current) {
    mentionLastQuery.current = mentionQuery
    if (mentionHidden) setMentionHidden(false)
  }
  const mentionOpen = mentionQuery !== null && !mentionHidden && !paletteOpen
  const mentionEntries = React.useMemo(
    () => (mentionOpen ? listDirEntries(cwdRef.current) : []),
    [mentionOpen]
  )
  const mentionCandidates = React.useMemo(
    () => (mentionOpen ? fileCandidates(mentionQuery, mentionEntries) : []),
    [mentionOpen, mentionQuery, mentionEntries]
  )
  const insertMention = (name) => {
    const replaced = draft.replace(/@([^\s@]*)$/, `@${name} `)
    setDraft(replaced)
    applyCursor([...replaced].length)
    setMentionHidden(true)
  }
  const closePalette = () => {
    setPaletteOpen(false)
    setPaletteQuery("")
    setPaletteIndex(0)
  }
  const decide = async (decision) => {
    if (!approval || decidingRef.current) return
    decidingRef.current = true
    const ctrl = controllerRef.current
    const r = await ctrl?.decideApproval?.(decision, approval.callId)
    decidingRef.current = false
    setApproval(null)
    setApprovalIndex(0)
    if (r?.ok) {
      const label = APPROVAL_OPTIONS.find((o) => o.key === decision)?.label || decision
      setStatus(`✓ 已${label}`)
      setLines((ls) => [...ls, { text: `✓ 审批：${label} ${approval.tool}`, dim: true }])
    } else {
      setStatus(`审批失败：${r?.error || "未知错误"}`)
    }
  }

  // 关口菜单选择：执行 REVIEW_MENU 映射的命令（directText 直提，读旧草稿会提交空串）
  const chooseGate = (i) => {
    const item = REVIEW_MENU[Math.min(Math.max(0, i), REVIEW_MENU.length - 1)]
    if (!item) return
    setGateMenu(null)
    setGateIndex(0)
    setLines((ls) => [...ls, { text: `❯ ${item.cmd}`, user: true }])
    setStatus(`已选择：${item.label}`)
    void submitRef.current(item.cmd)
  }
  const dismissGate = () => {
    setGateMenu(null)
    setGateIndex(0)
    setStatus("已收起人工确认（/open 重新打开可再次呼出）")
  }

  const keyboardCtx = {
    suggestOpen: suggestOpen && candidates.length > 0,
    paletteOpen,
    mentionOpen: mentionOpen && mentionCandidates.length > 0,
    approvalOpen: Boolean(approval),
    gateMenuOpen: Boolean(gateMenu),
    subagentDrill: Boolean(subagentDrill),
    readerOpen: Boolean(reader),
    readerSearchMode: Boolean(reader?.searchMode),
    readerMove: (delta) => setReader((r) => (r ? { ...r, offset: r.offset + delta } : r)),
    readerPage: (dir) => setReader((r) => (r
      ? { ...r, offset: r.offset + dir * Math.max(3, readerRows(r)) }
      : r)),
    closeReader: () => {
      setReader(null)
      setStatus("已关闭阅读器")
    },
    readerSearchOpen: () => {
      setReader((r) => (r ? { ...r, searchMode: true } : r))
      setStatus("阅读器搜索（Enter 跳转 · Esc 取消）")
    },
    readerSearchCancel: () => {
      setReader((r) => (r ? { ...r, searchMode: false, query: "" } : r))
      setStatus("已取消搜索")
    },
    readerSearchKey: (ch) => setReader((r) => {
      if (!r) return r
      if (ch === "backspace") return { ...r, query: r.query.slice(0, -1) }
      return { ...r, query: (r.query + ch).slice(0, 40) }
    }),
    readerSearchSubmit: () => {
      setReader((r) => {
        if (r && r.query) {
          return { ...r, offset: jumpToMatch(r.offset, r.lines, r.query, 1), searchMode: false }
        }
        return r ? { ...r, searchMode: false } : r
      })
      setStatus("已跳转到下一处命中")
    },
    readerSearchNext: (dir) => setReader((r) => {
      if (!r || !r.query) return r
      return { ...r, offset: jumpToMatch(r.offset, r.lines, r.query, dir) }
    }),
    stepsOpen,
    subagentsOpen,
    insightOpen: Boolean(insight),
    insightKind: insight?.kind || "",
    configOpen,
    modelPickerOpen: Boolean(modelPicker),
    moveModelPicker,
    tabModelPicker,
    saveModelPicker,
    closeModelPicker,
    refreshModelPicker,
    moveConfig: (dir) => {
      setConfigIndex((i) => {
        const next = Math.max(0, Math.min(i + dir, flattenConfig().length - 1))
        const { maxVisibleRows } = configPanelGeometry(dims.height)
        setConfigScroll((s) => configScrollFor(configRowModel(), next, s, maxVisibleRows))
        return next
      })
    },
    confirmConfig: () => void cycleConfig(),
    connectionsOpen,
    moveConnections,
    timelineOpen,
    moveTimeline,
    shortcutsOpen,
    scrollShortcuts: (dir) => {
      const geom = shortcutsGeometry(dims.height)
      setShortcutsScroll((s) => shortcutsScrollFor(s + dir * 3, geom.totalRows, geom.maxVisibleRows))
    },
    toggleShortcuts: () => {
      setShortcutsOpen((v) => {
        if (!v) setShortcutsScroll(0) // 每次打开回到顶部
        setStatus(v ? "已关闭快捷键帮助" : "快捷键帮助（F1 关闭）")
        return !v
      })
    },
    historyOpen: Boolean(historySearch),
    todosOpen,
    toggleTodos: async () => {
      const next = !todosOpen
      setTodosOpen(next)
      if (next) {
        const list = (await controllerRef.current?.todos?.()) || []
        setTodosList(list)
        setStatus(`研究待办（${list.length} 项）· Esc 关闭`)
      }
    },
    scrollBy: (delta) => setScrollOffset((o) => clampScrollOffset(o + delta, displayLineCount, transcriptHeight)),
    toggleHistorySearch: () => {
      setHistorySearch((v) => {
        setStatus(v ? "已关闭历史搜索" : "历史搜索（输入过滤 · Enter 填入）")
        return v ? null : { query: "", index: 0 }
      })
    },
    historyType: (ch) => setHistorySearch((v) => (v ? { ...v, query: v.query + ch, index: 0 } : v)),
    historyBackspace: () => setHistorySearch((v) => (v ? { ...v, query: [...v.query].slice(0, -1).join(""), index: 0 } : v)),
    moveHistory: (dir) => setHistorySearch((v) => (v ? { ...v, index: Math.max(0, v.index + dir) } : v)),
    confirmHistory: () => {
      const cur = historySearch
      if (!cur) return
      const rows = filterHistory(history, cur.query)
      const picked = rows[Math.min(cur.index, Math.max(0, rows.length - 1))]
      setHistorySearch(null)
      if (picked) {
        setDraft(picked)
        applyCursor([...picked].length)
        setStatus("已填入历史输入")
      }
    },
    moveInsight: (dir) => setInsight((v) => (v ? { ...v, index: Math.max(0, v.index + dir) } : v)),
    confirmInsight: () => {
      // 假设排行榜 Enter：把选中假设一键转为新颖性检测请求（排行榜→检测联动）
      const cur = insight
      if (cur?.kind === "hypotheses") {
        const row = (cur.data || [])[Math.min(cur.index, Math.max(0, (cur.data || []).length - 1))]
        if (row) {
          closeInsight()
          setDraft(`/novelty ${row.title}`)
          applyCursor([...`/novelty ${row.title}`].length)
          setStatus(`已填入「${row.title}」新颖性检测，回车发起`)
          return
        }
      }
      // 世界模型 Enter：一键支持并刷新面板
      if (cur?.kind === "facts") {
        const row = (cur.data || [])[Math.min(cur.index, Math.max(0, (cur.data || []).length - 1))]
        if (row) {
          void controllerRef.current?.factVote?.(row.id, "support").then(() => openInsight("facts"))
          setStatus(`已支持事实：${row.claim.slice(0, 30)}`)
          return
        }
      }
      closeInsight()
    },
    moveSubagents: (dir) => setSubagentsIndex((i) => Math.max(0, Math.min(i + dir, Math.max(0, subagentsList.length - 1)))),
    moveSteps: (dir) => setStepsIndex((i) => Math.max(0, Math.min(i + dir, RESEARCH_STEPS.length - 1))),
    chooseSteps: (i) => {
      const step = RESEARCH_STEPS[i]
      setStepsOpen(false)
      if (step) {
        const text = step.request("")
        setDraft(text)
        applyCursor([...text].length)
        setStatus(`已填入「${step.name}」请求，回车发起`)
      }
    },
    confirmSteps: () => {
      const step = RESEARCH_STEPS[Math.min(stepsIndex, RESEARCH_STEPS.length - 1)]
      setStepsOpen(false)
      if (step) {
        const text = step.request("")
        setDraft(text)
        applyCursor([...text].length)
        setStatus(`已填入「${step.name}」请求，回车发起`)
      }
    },
    moveApproval: (dir) => setApprovalIndex((i) => Math.max(0, Math.min(i + dir, APPROVAL_OPTIONS.length - 1))),
    confirmApproval: () => void decide(APPROVAL_OPTIONS[Math.min(approvalIndex, APPROVAL_OPTIONS.length - 1)].key),
    chooseApproval: (i) => void decide(APPROVAL_OPTIONS[i]?.key),
    moveGate: (dir) => setGateIndex((i) => Math.max(0, Math.min(i + dir, REVIEW_MENU.length - 1))),
    confirmGate: () => chooseGate(gateIndex),
    chooseGate: (i) => chooseGate(i),
    dismissGate,
    moveMention: (dir) => setMentionIndex((i) => Math.max(0, Math.min(i + dir, Math.max(0, mentionCandidates.length - 1)))),
    completeMention: () => {
      const c = mentionCandidates[Math.min(mentionIndex, mentionCandidates.length - 1)]
      if (c) insertMention(c.label)
    },
    moveSuggest: (dir) => setSuggestIndex((i) => Math.max(0, Math.min(i + dir, Math.max(0, candidates.length - 1)))),
    // slash 补全确认：禁用项只提示不提交；未写全先补全；已写全交回提交
    confirmSuggest: () => {
      const c = candidates[Math.min(suggestIndex, candidates.length - 1)]
      if (!c) return { submit: false }
      if (c.available === false) {
        setStatus(`${c.disabled_reason || "不可用"}；草稿保留。`)
        return { submit: false }
      }
      if (draft.trim().toLowerCase() === `/${c.name}`) return { submit: true }
      const text = `/${c.name} `
      setDraft(text)
      applyCursor([...text].length)
      return { submit: false }
    },
    submitDraft: () => submitRef.current(),
    togglePalette: () => {
      if (paletteOpen) closePalette()
      else {
        setPaletteOpen(true)
        setPaletteQuery("")
        setPaletteIndex(0)
      }
    },
    toggleSidebar: () => {
      if (appearance.sidebar_mode === "docked" && dims.width >= 120) setSidebarUserHidden((v) => !v)
      else { setSidebarOverlayOpen((v) => !v); setSidebarScroll(0) }
    },
    openSubagents: async () => {
      const ctrl = controllerRef.current
      if (!ctrl?.subagents) return
      const list = await ctrl.subagents()
      setSubagentsList(list)
      setSubagentsIndex(0)
      setSubagentsOpen(true)
      setStatus(list.length ? "选择 Subagent 计划下钻" : "暂无 Subagent 计划")
    },
    confirmSubagents: async () => {
      const s = subagentsList[Math.min(subagentsIndex, Math.max(0, subagentsList.length - 1))]
      setSubagentsOpen(false)
      if (!s) return
      const detail = await controllerRef.current?.subagentDetail?.(s.plan_id)
      setSubagentDrill(detail || s)
      setStatus("Subagent 只读视图（Esc 返回）")
    },
    toggleContext: () => {
      const next = !contextOpen
      setContextOpen(next)
      if (next) setStatus("上下文分解（Esc 关闭）")
    },
    toggleExpanded: () => {
      const next = !expandedRef.current
      expandedRef.current = next
      setExpanded(next)
      controllerRef.current?.setExpanded?.(next)
      setStatus(next ? "已展开工具详情（ctrl+o 折叠）" : "已折叠工具详情（ctrl+o 展开）")
    },
    movePalette: (dir) => {
      const count = paletteCommands(COMMANDS, pluginCommands, paletteQuery, workspaceMode,
        { busy: streaming, remote: controllerRef.current?.connectionMode === "remote" }).length
      setPaletteIndex((i) => Math.max(0, Math.min(i + dir, count - 1)))
    },
    confirmPalette: () => {
      const rows = paletteCommands(COMMANDS, pluginCommands, paletteQuery, workspaceMode,
        { busy: streaming, remote: controllerRef.current?.connectionMode === "remote" })
      const c = rows[paletteIndex]
      // 禁用项（远程门/忙碌门）不可执行：面板保持打开，草稿与光标不动
      if (c && c.available !== false) {
        const text = `/${c.name} `
        setDraft(text)
        applyCursor([...text].length)
        closePalette()
      }
    },
    paletteType: (ch) => {
      setPaletteQuery((q) => q + ch)
      setPaletteIndex(0)
    },
    paletteBackspace: () => {
      setPaletteQuery((q) => [...q].slice(0, -1).join(""))
      setPaletteIndex(0)
    },
  }

  // 启动：从引擎同步当前模式（bridge 未就绪时保持 general 并提示）
  useEffect(() => {
    let cancelled = false
    const ctrl = controllerRef.current
    if (!ctrl) return undefined
    const settingsRevision = preferenceRevision.current
    const settingsReadSequence = ++preferenceReadSequence.current
    ctrl.readMode?.().then((mode) => {
      if (!cancelled && mode) {
        setWorkspaceMode(mode)
        setStatus(`就绪 · ${MODE_TOKENS[mode]?.symbol ?? ""} ${MODE_TOKENS[mode]?.label ?? mode}模式`)
      }
    }).catch(() => {})
    Promise.resolve(ctrl.configItems?.()).then((values) => {
      if (!cancelled && settingsRevision === preferenceRevision.current && settingsReadSequence === preferenceReadSequence.current && !preferenceWrites.current) {
        const next = normalizeAppearance(values || {})
        appearanceRef.current = next
        setAppearance(next)
        setConfigValues(values || {})
        markAppearanceReady()
      }
    }).catch(() => { if (!cancelled) markAppearanceReady() })
    // --continue：恢复最近 Agent 会话
    if (autoResume) {
      ctrl.resumeLatest?.().then((r) => {
        if (!cancelled && r?.sessionId) {
          setLines((ls) => [...ls, { text: `恢复最近会话：${r.sessionId}`, dim: true }])
        } else if (!cancelled) {
          setLines((ls) => [...ls, { text: r?.message || "（没有可恢复的会话）", dim: true }])
        }
      }).catch(() => {})
    }
    ctrl.sidebar?.().then((sections) => {
      if (!cancelled && Array.isArray(sections)) setSidebarSections(sections)
    }).catch(() => {})
    ctrl.pluginCommands?.().then((list) => {
      if (!cancelled && Array.isArray(list)) setPluginCommands(list)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  // Quiet cross-client appearance refresh: no model status requests, no
  // concurrent reads, and no state update when the twelve fields are unchanged.
  useEffect(() => {
    const ctrl = controllerRef.current
    if (!ctrl?.appearancePrefs) return undefined
    let disposed = false, inFlight = false
    const refresh = async () => {
      if (disposed || inFlight || preferenceWrites.current) return
      inFlight = true
      const revision = preferenceRevision.current
      const readSequence = ++preferenceReadSequence.current
      try {
        const next = normalizeAppearance(await ctrl.appearancePrefs())
        if (disposed || revision !== preferenceRevision.current || readSequence !== preferenceReadSequence.current || preferenceWrites.current) return
        if (JSON.stringify(next) !== JSON.stringify(appearanceRef.current)) {
          appearanceRef.current = next
          setAppearance(next)
          setConfigValues((values) => ({ ...values, ...next }))
        }
        markAppearanceReady()
      } catch { /* Keep current preferences on transient bridge failures. */ }
      finally { inFlight = false }
    }
    const timer = setInterval(() => { void refresh() }, 2000)
    return () => { disposed = true; clearInterval(timer) }
  }, [])

  // 提交后刷新侧栏（会话状态/上下文可能变化）
  const refreshPlugins = async () => {
    const list = await controllerRef.current?.pluginCommands?.()
    if (Array.isArray(list)) setPluginCommands(list)
  }

  const refreshSidebar = async () => {
    const ctrl = controllerRef.current
    if (!ctrl?.sidebar) return
    try {
      const sections = await ctrl.sidebar()
      if (Array.isArray(sections)) setSidebarSections(sections)
    } catch { /* 侧栏降级：保留旧数据 */ }
  }

  // 切换过渡：乐观换模式 + 回执帧 + 品牌 shimmer（reducedMotion 直切）；同步写回引擎
  const changeMode = (to) => {
    if (to === workspaceMode || switching) return
    setWorkspaceMode(to)
    const label = MODE_TOKENS[to]?.label ?? to
    if (reducedMotion) {
      setStatus(`✓ 已切换到${label}模式`)
    } else {
      setStatus(`切换中… ${MODE_TOKENS[to]?.symbol ?? ""}`)
      const play = (frame) => {
        if (frame >= SWITCH_FRAMES.length) {
          setSwitching(null)
          setShimmerFrame(null)
          setStatus(`✓ 已切换到${label}模式`)
          setLines((ls) => [...ls, { text: `${MODE_TOKENS[to]?.symbol ?? ""} 已切换到${label}模式`, dim: true }])
          return
        }
        setSwitching({ to, frame })
        setShimmerFrame(frame * 3)
        timers.current.push(setTimeout(() => play(frame + 1), SWITCH_FRAME_MS))
      }
      play(0)
    }
    // 引擎侧持久化（失败回滚 + 提示）
    const ctrl = controllerRef.current
    if (ctrl?.setMode) {
      ctrl.setMode(to).then((r) => {
        if (r?.ok) return
        setWorkspaceMode(workspaceMode)
        setStatus(`模式未切换：${r?.error || "引擎拒绝"}`)
      }).catch(() => {})
    }
  }
  const toggleMode = () => changeMode(workspaceMode === "general" ? "research" : "general")
  const toggleRef = useRef(toggleMode)
  toggleRef.current = toggleMode

  // 提交：斜杠命令走 controller 命令子集；普通消息发起 agent 会话并渲染事件
  // 共享本地命令处理（draft 提交与队列冲刷两条路径复用）；返回 true = 已处理
  const handleLocalCommand = async (parsed) => {
    if (!parsed) return false
    const name = parsed.name
    if (name === "subagents") {
      void keyboardCtx.openSubagents()
      return true
    }
    if (INSIGHT_COMMANDS.has(name)) {
      if (workspaceMode !== "research") {
        setStatus("科研分析面板仅在科研模式可用（Ctrl+G 切换）")
        return true
      }
      void openInsight(name)
      return true
    }
    if (name === "timeline") {
      // 科研时间线（创新模块）：流水线阶段/空洞/证据计量，仅科研模式
      if (workspaceMode !== "research") {
        setStatus("科研时间线仅在科研模式可用（Ctrl+G 切换）")
        return true
      }
      void openTimeline()
      return true
    }
    if (name === "config") {
      void openConfig()
      return true
    }
    if (name === "connections") {
      // /connections：只读连接总览面板（写入仍走 Python model_registry 的 /connection save|delete）
      void openConnections()
      return true
    }
    if (name === "exit" || name === "quit") {
      if (onExit) onExit()
      return true
    }
    if (name === "menu") {
      // OpenTUI 无菜单模式：以命令面板等价替代（Ctrl+P）
      setPaletteOpen(true)
      setPaletteQuery("")
      setPaletteIndex(0)
      setStatus("命令面板（菜单等价入口）")
      return true
    }
    if (name === "novelty") {
      const topic = (parsed.args || "").trim()
      if (!topic) {
        setStatus("用法：/novelty <课题或方向>")
        return true
      }
      const ctrl = controllerRef.current
      if (!ctrl?.novelty) return true
      setStreaming(true)
      setStatus("新颖性检测中…")
      try {
        const r = await ctrl.novelty(topic)
        setLines((ls) => [...ls, { text: `🔍 新颖性检测：${topic}`, dim: true }, ...(r?.lines || [])])
        setStatus("就绪")
      } catch (error) {
        setLines((ls) => [...ls, { text: `✗ ${error?.message || error}`, dim: true }])
        setStatus("就绪")
      } finally {
        setStreaming(false)
        void refreshSidebar()
      }
      return true
    }
    if (name === "goal") {
      const ctrl = controllerRef.current
      const arg = (parsed.args || "").trim()
      if (arg && !["status", "off", "clear", "done"].includes(arg)) {
        const goal = ctrl?.setGoal?.(arg) ?? ""
        setGoalText(goal)
        setStatus(goal ? `◎ 目标已设置：${goal.slice(0, 40)}` : "目标为空")
        return true
      }
      const r = await ctrl?.submit(`/goal ${arg}`)
      if (["off", "clear", "done"].includes(arg)) setGoalText("")
      setLines((ls) => [...ls, ...(r?.lines || [])])
      setStatus(ctrl?.goal ? `◎ 目标：${ctrl.goal.slice(0, 40)}` : "就绪")
      return true
    }
    if (name === "steps") {
      if (workspaceMode !== "research") {
        setStatus("科研步骤仅在科研模式可用（Ctrl+G 切换）")
        return true
      }
      setStepsOpen(true)
      setStepsIndex(0)
      setStatus("选择科研步骤")
      return true
    }
    if (name === "queue" || name === "reviews" || name === "history" || name === "progress") {
      // 实验队列 / 复核收件箱 / 对话记录：当前回合忙时也立即可用（不进消息队列，不产生新回合）
      const ctrl = controllerRef.current
      if (!ctrl?.submit) return true
      const full = `/${name}${parsed.args ? ` ${parsed.args}` : ""}`
      setLines((ls) => [...ls, { text: `❯ ${full}`, user: true }])
      const r = await ctrl.submit(full)
      if (r?.openReader) {
        setReader({ title: r.openReader.title, lines: r.openReader.lines, offset: 0, query: "", searchMode: false })
        setStatus(`阅读器：${r.openReader.title}（↑↓ 滚动 · / 搜索 · Esc/q 返回）`)
      } else {
        setLines((ls) => [...ls, ...(r?.lines || [])])
        setStatus(streaming ? "处理中…" : "就绪") // 忙时不覆盖 busy 状态
      }
      return true
    }
    if (name === "steer") {
      // 同轮补充指导：当前回合忙时也立即可用（这就是它的用途——纠正进行中的回合）
      const ctrl = controllerRef.current
      if (!ctrl?.submit) return true
      const full = `/steer${parsed.args ? ` ${parsed.args}` : ""}`
      setLines((ls) => [...ls, { text: `❯ ${full}`, user: true }])
      const r = await ctrl.submit(full)
      setLines((ls) => [...ls, ...(r?.lines || [])])
      setStatus(streaming ? "处理中…" : "就绪")
      return true
    }
    if (name === "theme") {
      const arg = (parsed.args || "").trim()
      const next = !arg || arg === "auto" ? "system" : arg
      if (!["system", "light", "dark"].includes(next)) { setStatus("用法：/theme dark|light|system（auto）"); return true }
      const result = await savePreference("theme_mode", next)
      if (result?.ok === false) { setStatus(`✗ 主题未保存：${result.error || "未知"}`); return true }
      setStatus(next === "system" ? "✓ 主题跟随终端" : `✓ 已切换到${next === "dark" ? "深色" : "浅色"}主题`)
      return true
    }
    return false
  }

  const submit = async (directText) => {
    const text = typeof directText === "string" ? directText.trim() : draft.trim()
    if (!text) return
    // 命令合约门：远程/静态不可用命令只提示且保留草稿，不进入分发。
    // 忙（busy）不走此门——本引擎忙时进队列（queue 路径）。
    if (typeof directText !== "string") {
      const gateParsed = parseCommand(text)
      if (gateParsed) {
        const contract = commandsForMode([...COMMANDS, ...CONTROL_COMMANDS], workspaceMode,
          { busy: false, remote: controllerRef.current?.connectionMode === "remote", command: gateParsed })
          .find((row) => row.name === gateParsed.name)
        if (contract?.available === false) {
          setLines((ls) => [...ls, { text: `${contract.disabled_reason}；草稿保留。`, dim: true }])
          setStatus("命令不可用；草稿已保留")
          return
        }
      }
    }
    if (typeof directText !== "string") {
      setDraft("")
      applyCursor(0)
    }
    // 敏感命令（令牌/密钥/配置 JSON）不进历史、回显脱敏；
    // 草稿与发送内容保持原文，仅展示层脱敏。
    const echo = safeCommandEcho(text)
    if (echo === text) setHistory((h) => (h[h.length - 1] === text ? h : [...h, text].slice(-100)))
    historyIdx.current = -1
    if (steerArmed && typeof directText !== "string") {
      setSteerArmed(false)
      const ctrl = controllerRef.current
      setLines((ls) => [...ls, { text: `↳ 指导：${text}`, dim: true }])
      if (ctrl) {
        const r = await ctrl.submit(`/steer ${text}`)
        setLines((ls) => [...ls, ...(r?.lines || [])])
      }
      return
    }
    // 本地命令（insight/novelty/goal/steps/theme/subagents）优先于排队判断：流式中也可立即执行
    if (typeof directText !== "string") {
      const localParsed = parseCommand(text)
      if (await handleLocalCommand(localParsed)) return
    }
    if (shellMode && typeof directText !== "string") {
      const command = draft.slice(1).trim()
      setDraft("")
      applyCursor(0)
      if (!command) return
      const ctrl = controllerRef.current
      if (!ctrl?.shell) return
      setStreaming(true)
      try {
        const r = await ctrl.shell(command)
        setLines((ls) => [...ls, ...(r?.lines || [])])
      } catch (error) {
        setLines((ls) => [...ls, { text: `✗ ${error?.message || error}`, dim: true }])
      } finally {
        setStreaming(false)
      }
      return
    }
    if (streaming && typeof directText !== "string") {
      // 忙时排队（上限 3 条 + more 提示），不阻塞当前回合
      if (queuedRef.current.length >= 3) {
        setStatus("队列已满（3 条）· 请等待当前回合完成")
        return
      }
      pushQueue(text)
      setStatus(`已排队（${queuedRef.current.length}）· 当前回合继续处理中`)
      return
    }
    setLines((ls) => [...ls, { text: `❯ ${echo}`, user: true }])
    setScrollOffset(0)
    const ctrl = controllerRef.current
    if (!ctrl) {
      setLines((ls) => [...ls, { text: "（未接入引擎：本地回显）", dim: true }])
      return
    }
    const parsed = parseCommand(text)
    if (await handleLocalCommand(parsed)) return
    setStreaming(true)
    setStatus("处理中…")
    try {
      const r = await ctrl.submit(text, {
        onUpdate: (rows) => {
          // 增量流式：事件边到边渲染（cursor 分页，避免整页重算）
          if (rows?.length) setLines((ls) => [...ls, ...rows])
        },
      })
      setLines((ls) => (r?.clear ? [{ text: r.notice || "已清空" }] : [...ls, ...(r?.lines || [])]))
      if (r?.openReader && !paletteOpenRef.current) {
        // /read：命令结果带全文，开全屏阅读器（Esc/q 返回，/ 搜索）
        setReader({ title: r.openReader.title, lines: r.openReader.lines, offset: 0, query: "", searchMode: false })
        setStatus(`阅读器：${r.openReader.title}（↑↓ 滚动 · / 搜索 · n 跳转 · Esc/q 返回）`)
      }
      if (r?.openPalette) {
        // /palette：空查询打开命令面板（与 Ctrl+P 等价）
        setPaletteOpen(true)
        setPaletteQuery("")
        setPaletteIndex(0)
        setStatus("命令面板已打开（输入过滤 · Enter 执行 · Esc 关闭）")
      }
      if (r?.openModelPicker) {
        // /models：本地打开科研模型配置面板（远程已在控制器侧交回主机）
        openModelPicker()
      }
      if (r?.openGateMenu) {
        // /open 命中人工确认关口：弹编号菜单（数字直选 · Esc 收起后可重新 /open 呼出）
        setGateMenu(r.openGateMenu)
        setGateIndex(0)
        setStatus(`待人工确认：${r.openGateMenu.taskId}（数字选择 · Esc 稍后）`)
      }
      if (r?.mode && r.mode !== workspaceMode) setWorkspaceMode(r.mode)
      if (r?.planMode !== undefined) setPlanMode(r.planMode)
      // /copy：最近助手消息写入系统剪贴板（OSC52）
      const copyLine = (r?.lines || []).find((l) => l.copy)
      if (copyLine && renderer?.copyToClipboardOSC52?.(copyLine.text)) {
        setStatus("✓ 已复制最近助手消息")
        r.lines = r.lines.filter((l) => !l.copy)
      }
      // 面板打开时保留面板指引，不被通用"就绪"覆盖（先就绪，面板状态最后设置）
      if (!r?.openReader && !r?.openPalette && !r?.openModelPicker && !r?.openGateMenu) {
        setStatus(r?.mode ? `就绪 · ${MODE_TOKENS[r.mode]?.symbol ?? ""} ${MODE_TOKENS[r.mode]?.label ?? r.mode}模式` : "就绪")
      }
    } catch (error) {
      setLines((ls) => [...ls, { text: `✗ ${error?.message || error}`, dim: true }])
      setStatus("就绪")
    } finally {
      setStreaming(false)
      void refreshSidebar()
      void flushQueue()
    }
  }
  // 队列冲刷：当前回合结束后自动发送下一条（串行，避免并发回合）
  const flushQueue = async () => {
    if (flushingRef.current) return
    flushingRef.current = true
    try {
      for (;;) {
        const next = queuedRef.current[0]
        if (next == null) break
        shiftQueue()
        await submitRef.current(next)
      }
    } finally {
      flushingRef.current = false
    }
  }
  const submitRef = useRef(submit)
  submitRef.current = submit
  // 迟到响应守卫：/context 的 session_status 悬挂期间用户开了命令面板，回来时不得抢视图
  const paletteOpenRef = useRef(false)
  paletteOpenRef.current = paletteOpen
  submitRef.current = submit
  // 编辑器：码点级光标 + 行内导航 + 历史（同步镜像避免一批按键内闭包旧值）
  const editRef = useRef(null)
  editRef.current = (key) => {
    if (key.name === "return" || key.name === "enter") return submitRef.current()
    if (key.name === "backspace") {
      const c = cursorRef.current
      if (c <= 0) return undefined
      setDraft((d) => {
        const chars = [...d]
        chars.splice(Math.min(c, chars.length) - 1, 1)
        return chars.join("")
      })
      return applyCursor(Math.max(0, c - 1))
    }
    if (key.name === "delete") {
      const c = cursorRef.current
      setDraft((d) => {
        const chars = [...d]
        if (c >= chars.length) return d
        chars.splice(c, 1)
        return chars.join("")
      })
      return undefined
    }
    if (key.ctrl && key.name === "k") {
      // Ctrl+K：删除光标到行尾
      const c = cursorRef.current
      setDraft((d) => [...d].slice(0, Math.min(c, [...d].length)).join(""))
      return undefined
    }
    if (key.ctrl && key.name === "u") {
      // Ctrl+U：清空草稿（readline 惯例；命令面板占用时由面板层优先处理）
      setDraft("")
      return applyCursor(0)
    }
    if (key.name === "left") return applyCursor(Math.max(0, cursorRef.current - 1))
    if (key.name === "right") return applyCursor(Math.min([...draftRef.current].length, cursorRef.current + 1))
    if (key.name === "home") return applyCursor(0)
    if (key.name === "end") return applyCursor([...draftRef.current].length)
    if (key.name === "up" || key.name === "down") {
      // 历史导航：up 更近 / down 回新；无历史时忽略
      if (!history.length) return undefined
      if (key.name === "up") {
        const next = historyIdx.current + 1
        if (next >= history.length) return undefined
        if (historyIdx.current === -1) historyDraft.current = draft
        historyIdx.current = next
      } else {
        if (historyIdx.current <= 0) {
          if (historyIdx.current === -1) return undefined
          historyIdx.current = -1
          setDraft(historyDraft.current)
          applyCursor([...historyDraft.current].length)
          return undefined
        }
        historyIdx.current = historyIdx.current - 1
      }
      const text = history[history.length - 1 - historyIdx.current] ?? ""
      setDraft(text)
      applyCursor([...text].length)
      return undefined
    }
    if (!key.ctrl && !key.meta && key.sequence && [...key.sequence].length === 1) {
      const c = cursorRef.current
      setDraft((d) => {
        const chars = [...d]
        chars.splice(Math.min(c, chars.length), 0, key.sequence)
        return chars.join("")
      })
      return applyCursor(c + 1)
    }
    return undefined
  }

  // 键盘路由：selection 层（补全/面板）→ global 层（切模式/退出）→ Esc 语义 → 编辑器
  const routerRef = useRef(null)
  if (!routerRef.current) {
    routerRef.current = createKeyRouter([
      // ---- approval 层：审批期间键盘独占 ----
      {
        layer: "approval",
        match: (k, ctx) => ctx.approvalOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveApproval(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "approval",
        match: (k, ctx) => ctx.approvalOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.confirmApproval()
          return { handled: true }
        },
      },
      {
        layer: "approval",
        match: (k, ctx) => ctx.approvalOpen && ["1", "2", "3"].includes(k.name),
        run: (k, ctx) => {
          ctx.chooseApproval(Number(k.name) - 1)
          return { handled: true }
        },
      },
      {
        layer: "approval",
        match: (k, ctx) => ctx.approvalOpen && k.name === "escape",
        run: (k, ctx) => {
          ctx.chooseApproval(2) // Esc = 拒绝
          return { handled: true }
        },
      },
      // ---- selection 层：人工确认关口菜单（数字直选） ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.gateMenuOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveGate(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.gateMenuOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.confirmGate()
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.gateMenuOpen && ["1", "2", "3", "4"].includes(k.name),
        run: (k, ctx) => {
          ctx.chooseGate(Number(k.name) - 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.gateMenuOpen && k.name === "escape",
        run: (k, ctx) => {
          ctx.dismissGate()
          return { handled: true }
        },
      },
      // ---- selection 层：Subagent 面板 ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.subagentsOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveSubagents(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.subagentsOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.confirmSubagents()
          return { handled: true }
        },
      },
      // ---- selection 层：科研步骤面板 ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.stepsOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveSteps(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },      {
        layer: "selection",
        match: (k, ctx) => ctx.stepsOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.confirmSteps()
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.stepsOpen && ["1", "2", "3"].includes(k.name),
        run: (k, ctx) => {
          ctx.chooseSteps(Number(k.name) - 1)
          return { handled: true }
        },
      },
      // ---- selection 层：科研分析面板（假设排行/共识/矩阵/实验树）----
      {
        layer: "selection",
        match: (k, ctx) => ctx.insightOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveInsight(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.insightOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.confirmInsight()
          return { handled: true }
        },
      },
      // ---- selection 层：配置面板 ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.configOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveConfig(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.configOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.confirmConfig()
          return { handled: true }
        },
      },
      // ---- selection 层：连接总览（/connections）↑↓ 移动 ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.connectionsOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveConnections(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      // ---- selection 层：科研时间线（/timeline）↑↓ 光标 + 翻页 ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.timelineOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveTimeline(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.timelineOpen && (k.name === "pageup" || k.name === "pagedown"),
        run: (k, ctx) => {
          const rows = timelineRowModel(timelineData)
          const count = rows.filter((r) => r.kind === "station" || r.kind === "hole").length || 1
          ctx.moveTimeline(k.name === "pageup" ? -3 : Math.min(3, count - timelineIndex))
          return { handled: true }
        },
      },
      // ---- selection 层：快捷键帮助（F1）↑↓ 滚动 ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.shortcutsOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.scrollShortcuts(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.shortcutsOpen && (k.name === "pageup" || k.name === "pagedown"),
        run: (k, ctx) => {
          const geom = shortcutsGeometry(dims.height)
          ctx.scrollShortcuts(k.name === "pageup" ? -Math.max(1, geom.maxVisibleRows - 2) : Math.max(1, geom.maxVisibleRows - 2))
          return { handled: true }
        },
      },
      // ---- selection 层：模型选择器（可打印键不拦截，草稿保持可编辑） ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.modelPickerOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveModelPicker(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.modelPickerOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.saveModelPicker()
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.modelPickerOpen && k.name === "tab",
        run: (k, ctx) => {
          ctx.tabModelPicker()
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.modelPickerOpen && k.name === "escape",
        run: (k, ctx) => {
          ctx.closeModelPicker()
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.modelPickerOpen && k.ctrl && (k.name === "r" || k.sequence === "\u0012"),
        run: (k, ctx) => {
          ctx.refreshModelPicker()
          return { handled: true }
        },
      },
      // ---- selection 层：历史搜索（字符输入进 query）----
      {
        layer: "selection",
        match: (k, ctx) => ctx.historyOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveHistory(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.historyOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.confirmHistory()
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.historyOpen && k.name === "backspace",
        run: (k, ctx) => {
          ctx.historyBackspace()
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.historyOpen && !k.ctrl && !k.meta && k.sequence >= " " && [...k.sequence].length === 1,
        run: (k, ctx) => {
          ctx.historyType(k.sequence)
          return { handled: true }
        },
      },
      // ---- selection 层：@ 文件补全 ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.mentionOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.moveMention(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.mentionOpen && (k.name === "tab" || k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.completeMention()
          return { handled: true }
        },
      },
      // ---- selection 层：slash 补全 ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.suggestOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          const dir = k.name === "up" ? -1 : 1
          ctx.moveSuggest(dir)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.suggestOpen && k.name === "tab",
        run: (k, ctx) => {
          ctx.confirmSuggest()
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.suggestOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          const r = ctx.confirmSuggest()
          if (r?.submit) ctx.submitDraft()
          return { handled: true }
        },
      },
      // ---- selection 层：命令面板 ----
      {
        layer: "selection",
        match: (k, ctx) => ctx.paletteOpen && (k.name === "up" || k.name === "down"),
        run: (k, ctx) => {
          ctx.movePalette(k.name === "up" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.paletteOpen && (k.name === "return" || k.name === "enter"),
        run: (k, ctx) => {
          ctx.confirmPalette()
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.paletteOpen && !k.ctrl && !k.meta && k.sequence >= " " && [...k.sequence].length === 1,
        run: (k, ctx) => {
          ctx.paletteType(k.sequence)
          return { handled: true }
        },
      },
      {
        layer: "selection",
        match: (k, ctx) => ctx.paletteOpen && k.name === "backspace",
        run: (k, ctx) => {
          ctx.paletteBackspace()
          return { handled: true }
        },
      },
      // ---- global 层 ----
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "p",
        run: (k, ctx) => {
          ctx.togglePalette()
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.name === "f12",
        run: () => {
          // Debug overlay：框架原生渲染统计浮层
          if (renderer?.debugOverlay) {
            renderer.debugOverlay.enabled = !renderer.debugOverlay.enabled
            setStatus(renderer.debugOverlay.enabled ? "Debug overlay 已开启（F12 关闭）" : "Debug overlay 已关闭")
          }
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "s",
        run: () => {
          setSteerArmed((v) => {
            setStatus(v ? "已取消补充指导" : "补充指导模式：输入后回车追加到当前回合（Esc 取消）")
            return !v
          })
          return { handled: true }
        },
      },
      // ---- readonly 层：全屏阅读器（/read）----
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && k.name === "up",
        run: (k, ctx) => {
          ctx.readerMove(-1)
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && k.name === "down",
        run: (k, ctx) => {
          ctx.readerMove(1)
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && (k.name === "left" || k.name === "right"),
        run: (k, ctx) => {
          ctx.readerMove(k.name === "left" ? -10 : 10)
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && (k.name === "pageup" || k.name === "pagedown"),
        run: (k, ctx) => {
          ctx.readerPage(k.name === "pageup" ? -1 : 1)
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && k.name === "escape",
        run: (k, ctx) => {
          if (ctx.readerSearchMode) ctx.readerSearchCancel()
          else ctx.closeReader()
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && ctx.readerSearchMode && k.name === "return",
        run: (k, ctx) => {
          ctx.readerSearchSubmit()
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && ctx.readerSearchMode
          && (k.name === "backspace" || k.name === "delete"),
        run: (k, ctx) => {
          ctx.readerSearchKey("backspace")
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && ctx.readerSearchMode
          && !k.ctrl && !k.meta && k.sequence >= " " && [...k.sequence].length === 1,
        run: (k, ctx) => {
          ctx.readerSearchKey(k.sequence)
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && !ctx.readerSearchMode && k.sequence === "/",
        run: (k, ctx) => {
          ctx.readerSearchOpen()
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && !ctx.readerSearchMode
          && (k.sequence === "n" || k.sequence === "N"),
        run: (k, ctx) => {
          ctx.readerSearchNext(k.sequence === "n" ? 1 : -1)
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.readerOpen && !ctx.readerSearchMode
          && (k.sequence === "q" || k.sequence === "Q"),
        run: (k, ctx) => {
          ctx.closeReader()
          return { handled: true }
        },
      },
      {
        layer: "readonly",
        match: (k, ctx) => ctx.subagentDrill && k.name === "escape",
        run: () => {
          setSubagentDrill(null)
          setStatus("已返回主会话")
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "i",
        run: (k, ctx) => {
          ctx.toggleContext()
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "o",
        run: (k, ctx) => {
          ctx.toggleExpanded()
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "l",
        run: () => {
          // Ctrl+L 清屏：保留模式与草稿
          setLines([{ text: "格致 SciZhi（OpenTUI 引擎）· 已清屏，Ctrl+G 切换模式" }])
          setScrollOffset(0)
          setStatus("✓ 已清屏")
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "d",
        run: () => {
          // Ctrl+D 退出（readline EOF 惯例）
          if (onExit) onExit()
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.name === "pageup" || k.name === "pagedown",
        run: (k, ctx) => {
          const delta = (k.name === "pageup" ? 1 : -1) * 10
          ctx.scrollBy?.(delta)
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "t",
        run: (k, ctx) => {
          // Ctrl+T 研究待办面板
          void ctx.toggleTodos()
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "r",
        run: (k, ctx) => {
          // Ctrl+R 历史搜索
          ctx.toggleHistorySearch()
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.name === "f1",
        run: (k, ctx) => {
          ctx.toggleShortcuts()
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "c",
        run: (k) => {
          // 三级语义：选区复制 > 清草稿 > 2s 双击退出
          const plan = resolveCtrlCIntent(k, { hasSelection: renderer?.hasSelection, draft: draftRef.current, lastCtrlCAt: lastCtrlCAt.current }, Date.now())
          if (plan.markCtrlCAt) lastCtrlCAt.current = plan.markCtrlCAt
          if (plan.action === "copy_selection") {
            lastCtrlCAt.current = 0
            const text = renderer?.getSelection?.()?.getSelectedText?.() || ""
            if (text && renderer?.copyToClipboardOSC52?.(text)) setStatus("✓ 已复制选区文本")
            return { handled: true }
          }
          if (plan.action === "clear_draft") {
            lastCtrlCAt.current = 0
            setDraft("")
            applyCursor(0)
            setStatus("已清空草稿")
            return { handled: true }
          }
          if (plan.action === "exit") {
            if (onExit) onExit()
            return { handled: true }
          }
          setStatus("再按一次 Ctrl+C 退出")
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "b",
        run: (k, ctx) => {
          ctx.toggleSidebar()
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => (k.ctrl && k.name === "g") || (k.shift && k.name === "tab"),
        run: () => {
          toggleRef.current()
          return { handled: true }
        },
      },
      {
        layer: "global",
        match: (k) => k.ctrl && k.name === "y",
        run: () => {
          const text = renderer?.getSelection?.()?.getSelectedText?.() || ""
          if (text && renderer?.copyToClipboardOSC52?.(text)) setStatus("✓ 已复制选区文本")
          else setStatus("没有可复制的选区")
          return { handled: true }
        },
      },
    ])
  }

  useKeyboard((key) => {
    if (!(key.ctrl && key.name === "c")) lastCtrlCAt.current = 0
    // The drawer owns keyboard focus without ever changing the composer state.
    if (sidebarOverlayOpen) {
      if (key.name === "escape" || (key.ctrl && key.name === "b")) setSidebarOverlayOpen(false)
      else if (key.name === "up" || key.name === "down") {
        const count = sidebarSections.reduce((n, section) => n + 1 + (section.collapsed ? 0 : section.lines?.length || 0), 0)
        const max = Math.max(0, count - Math.max(1, dims.height - 6))
        setSidebarScroll((v) => Math.max(0, Math.min(max, v + (key.name === "up" ? -1 : 1))))
      }
      return
    }
    const r = routerRef.current.dispatch(key, { streaming, ...keyboardCtx })
    if (r.consumed) return
    if (key.name === "escape") {
      if (subagentsOpen) {
        setSubagentsOpen(false)
        setStatus("已关闭 Subagent 面板")
        return
      }
      if (insight) {
        closeInsight()
        setStatus("已关闭科研分析面板")
        return
      }
      if (timelineOpen) {
        closeTimeline()
        return
      }
      if (configOpen) {
        setConfigOpen(false)
        setStatus("已关闭配置面板")
        return
      }
      if (connectionsOpen) {
        closeConnections()
        return
      }
      if (shortcutsOpen) {
        setShortcutsOpen(false)
        setStatus("已关闭快捷键帮助")
        return
      }
      if (historySearch) {
        setHistorySearch(null)
        setStatus("已关闭历史搜索")
        return
      }
      if (todosOpen) {
        setTodosOpen(false)
        setStatus("已关闭研究待办")
        return
      }
      if (contextOpen) {
        setContextOpen(false)
        setStatus("已关闭上下文分解")
        return
      }
      if (steerArmed) {
        setSteerArmed(false)
        setStatus("已取消补充指导")
        return
      }
      if (stepsOpen) return setStepsOpen(false)
      if (paletteOpen) return closePalette()
      if (mentionOpen) return setMentionHidden(true)
      if (suggestOpen) return setSuggestIndex(0)
      const plan = resolveEscape({ selectionOpen: false, streaming })
      if (plan.action === "hint_exit" && draftRef.current) {
        // 空闲 Esc 清草稿（无可关层时先清输入，空草稿再按才是退出提示）
        setDraft("")
        applyCursor(0)
        setStatus("草稿已清空")
        return
      }
      setStatus(plan.action === "hint_exit" ? "无进行中输出 · 再按一次 Ctrl+C 退出" : "已暂停输出")
      return
    }
    editRef.current(key)
  })

  // bracketed 粘贴：阅读器/审批打开时不吃粘贴；
  // 命令面板打开时归一化后进查询；默认插入草稿（超长保留原草稿并提示，不静默丢弃）。
  usePaste((event) => {
    const pasted = event?.bytes ? new TextDecoder().decode(event.bytes) : String(event?.text || "")
    if (!pasted) return
    if (reader || approval || sidebarOverlayOpen) return
    if (paletteOpen) {
      setPaletteQuery((q) => (q + normalizePaste(pasted)).slice(0, 200))
      return
    }
    const caret16 = [...draftRef.current].slice(0, cursorRef.current).join("").length
    const next = insertText({ text: draftRef.current, caret: caret16 }, pasted)
    if (next.rejected) {
      setStatus("粘贴内容过长，原草稿保留；请缩小内容后重试。")
      return
    }
    setDraft(next.text)
    applyCursor([...next.text.slice(0, next.caret)].length)
  })

  // 启动屏：桥接就绪 + 模式同步后进入主界面（3s 超时兜底，不卡死）
  useEffect(() => {
    let cancelled = false
    const ctrl = controllerRef.current
    const finish = (err = "") => {
      if (cancelled) return
      setBootError(err)
      setBooting(false)
    }
    const timer = setTimeout(() => finish("连接超时，已进入主界面（可重试）"), 3000)
    if (ctrl?.whenReady) {
      ctrl.whenReady().then(() => ctrl.readMode?.()).then(() => { clearTimeout(timer); finish() }).catch((e) => { clearTimeout(timer); finish(String(e?.message || e).slice(0, 60)) })
    } else {
      clearTimeout(timer)
      finish()
    }
    return () => { cancelled = true; clearTimeout(timer) }
  }, [])

  // 审批轮询：有会话时每 2s 探查等待中的审批（后台会话也能捕获）
  useEffect(() => {
    const id = setInterval(() => {
      const ctrl = controllerRef.current
      if (!ctrl?.pollApproval || approval || decidingRef.current) return
      ctrl.pollApproval().then((pending) => {
        if (pending && !decidingRef.current) {
          setApproval(pending)
          setApprovalIndex(0)
          setStatus("等待审批")
        }
      }).catch(() => {})
    }, 1200)
    return () => clearInterval(id)
  }, [approval])

  // Read local session metadata without overlapping requests or keeping a
  // previous session's counts visible after switching sessions.
  useEffect(() => {
    const ctrl = controllerRef.current
    if (!ctrl?.contextBreakdown || (!appearance.inspector_visible && !contextOpen)) {
      setContextBreakdown(null)
      return undefined
    }
    let cancelled = false, inFlight = false, previousSession = Symbol("initial")
    const refresh = async () => {
      const session = ctrl.sessionId
      if (session !== previousSession) { previousSession = session; setContextBreakdown(null) }
      if (inFlight) return
      inFlight = true
      try {
        const value = await ctrl.contextBreakdown()
        if (!cancelled && ctrl.sessionId === session) setContextBreakdown(value || null)
      } catch { if (!cancelled && ctrl.sessionId === session) setContextBreakdown(null) }
      finally { inFlight = false }
    }
    void refresh()
    const timer = setInterval(refresh, 2000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [streaming, appearance.inspector_visible, contextOpen])

  // Explicit preferences win over terminal events; only system mode subscribes.
  useEffect(() => {
    let cancelled = false
    const update = (mode) => {
      if (cancelled) return
      applyTheme(mode, mode === "light" ? appearance.code_light_theme : appearance.code_dark_theme)
      setThemeMode(mode)
      refreshThemeTokens((revision) => revision + 1)
    }
    const system = appearance.theme_mode === "system"
    if (system) detectTheme(renderer, 250, false).then(update).catch(() => {})
    else update(appearance.theme_mode)
    const unwatch = system ? watchTheme(renderer, update, false) : () => {}
    return () => { cancelled = true; unwatch() }
  }, [appearance.theme_mode, appearance.code_light_theme, appearance.code_dark_theme])

  useEffect(() => {
    if (!appearance.inspector_visible) setContextOpen(false)
  }, [appearance.inspector_visible])
  useEffect(() => {
    if (sidebarLayout === "docked") setSidebarOverlayOpen(false)
  }, [sidebarLayout])

  // spinner 动画时钟
  useEffect(() => {
    if (reducedMotion) {
      timers.current.forEach(clearTimeout)
      timers.current = []
      setSwitching(null)
      setShimmerFrame(null)
      setSpinnerFrame(0)
      return undefined
    }
    const id = setInterval(() => setSpinnerFrame((f) => f + 1), 80)
    return () => clearInterval(id)
  }, [reducedMotion])
  useEffect(() => () => timers.current.forEach(clearTimeout), [])

  // 回看锚定：视口停在历史时，新行到达按增量补偿偏移，视图不漂移；
  // offset=0（吸底）时不受影响，流式输出自动跟随。
  scrollOffsetRef.current = scrollOffset
  useEffect(() => {
    const prev = linesLenRef.current
    linesLenRef.current = displayLineCount
    if (prev > 0 && displayLineCount > prev && scrollOffsetRef.current > 0) {
      setScrollOffset((o) => o + (displayLineCount - prev))
    }
  }, [displayLineCount])

  const paletteGeom = palettePanelGeometry(dims.height, paletteOpen
    ? paletteCommands(COMMANDS, pluginCommands, paletteQuery, workspaceMode,
      { busy: streaming, remote: controllerRef.current?.connectionMode === "remote" }).length : 0,
    draft.split("\n").length)
  // 面板节点：slash 补全（composer 上方内联）或 Ctrl+P 命令面板（互斥）
  const panel = paletteOpen
    ? h(PalettePanel, {
        query: paletteQuery,
        index: paletteIndex,
        maxVisibleRows: paletteGeom.maxVisibleRows,
        commands: COMMANDS,
        plugins: pluginCommands,
        mode: workspaceMode,
        width: dims.width,
        context: { busy: streaming, remote: controllerRef.current?.connectionMode === "remote" },
        onHover: (i) => setPaletteIndex(i),
        onSelect: (i) => {
          const rows = paletteCommands(COMMANDS, pluginCommands, paletteQuery, workspaceMode,
        { busy: streaming, remote: controllerRef.current?.connectionMode === "remote" })
          const c = rows[i]
          // 禁用项（远程门/忙碌门）不可执行：面板保持打开，草稿与光标不动
          if (c && c.available !== false) {
            const text = `/${c.name} `
            setDraft(text)
            applyCursor([...text].length)
            closePalette()
          }
        },
      })
    : suggestOpen && candidates.length > 0
      ? (() => {
          const win = completionWindow(candidates, suggestIndex)
          return h(
            "box",
            { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
            ...win.rows.map((c, i) => {
              const disabled = c.available === false
              return h(
                "box",
                {
                  key: c.name,
                  flexDirection: "row",
                  onMouseOver: () => !disabled && setSuggestIndex(win.start + i),
                  onMouseDown: () => {
                    if (disabled) return
                    const text = `/${c.name} `
                    setDraft(text)
                    applyCursor([...text].length)
                    setSuggestIndex(0)
                  },
                },
                h("text", { content: win.start + i === suggestIndex && !disabled ? `${SYMBOLS.pointer} ` : "  ", fg: win.start + i === suggestIndex && !disabled ? TOKENS.accent : TOKENS.textMuted }),
                h("text", { content: `/${c.name}`, fg: disabled ? TOKENS.textDim : win.start + i === suggestIndex ? TOKENS.textStrong : TOKENS.text }),
                h("text", { content: ` ${c.desc || ""}`.slice(0, 36), fg: TOKENS.textMuted }),
                disabled ? h("text", { content: " （忙）", fg: TOKENS.warning }) : null
              )
            }),
            h("text", { content: ` ↑↓ 命令 ${suggestIndex + 1}/${candidates.length} · Tab 填入 · Enter 提交 · 鼠标可点选`, fg: TOKENS.textDim })
          )
        })()
      : mentionOpen && mentionCandidates.length > 0
      ? (() => {
          const win = completionWindow(mentionCandidates, mentionIndex)
          return h(
            "box",
            { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
            ...win.rows.map((c, i) =>
              h(
                "box",
                {
                  key: c.name,
                  flexDirection: "row",
                  onMouseOver: () => setMentionIndex(win.start + i),
                  onMouseDown: () => insertMention(c.label),
                },
                h("text", { content: win.start + i === mentionIndex ? `${SYMBOLS.pointer} ` : "  ", fg: win.start + i === mentionIndex ? TOKENS.accent : TOKENS.textMuted }),
                h("text", { content: c.isDir ? "📁 " : "📄 ", fg: TOKENS.textMuted }),
                h("text", { content: c.label, fg: win.start + i === mentionIndex ? TOKENS.textStrong : TOKENS.text })
              )
            ),
            h("text", { content: ` ↑↓ 文件 ${mentionIndex + 1}/${mentionCandidates.length} · Tab/Enter 插入 · Esc 忽略`, fg: TOKENS.textDim })
          )
        })()
      : null

  // 科研步骤面板
  const stepsPanel = stepsOpen
    ? h(ResearchStepsPanel, {
        index: stepsIndex,
        onHover: (i) => setStepsIndex(i),
        onSelect: (i) => {
          const step = RESEARCH_STEPS[i]
          setStepsOpen(false)
          if (step) {
            const text = step.request("")
            setDraft(text)
            applyCursor([...text].length)
            setStatus(`已填入「${step.name}」请求，回车发起`)
          }
        },
      })
    : null

  // 上下文分解面板
  const contextPanel = contextOpen ? h(ContextPanel, { breakdown: contextBreakdown, width: dims.width }) : null

  // 科研分析面板（假设排行/共识/矩阵/实验树）
  const insightPanel = insight
    ? h(ResearchInsightPanel, { kind: insight.kind, data: insight.data, index: insight.index, width: dims.width })
    : null

  // 配置面板（/config）：窗口化渲染，预算与几何同源（configPanelGeometry）
  const configGeom = configPanelGeometry(dims.height)
  const configPanel = configOpen
    ? h(ConfigPanel, {
        values: configValues,
        index: configIndex,
        scroll: configScrollFor(configRowModel(), configIndex, configScroll, configGeom.maxVisibleRows),
        maxVisibleRows: configGeom.maxVisibleRows,
        width: dims.width,
      })
    : null

  // 科研时间线面板（/timeline，科研模式创新模块）：窗口化，预算与渲染同源
  const timelineGeom = timelineGeometry(dims.height, timelineData)
  const timelineRows = timelineRowModel(timelineData || {})
  const timelineCursor = timelineCursorRow(timelineRows, timelineIndex)
  const timelinePanel = timelineOpen
    ? h(ResearchTimelinePanel, {
        data: timelineData || {},
        cursor: timelineCursor,
        scroll: timelineScrollFor(timelineRows, timelineCursor, timelineScroll, timelineGeom.maxVisibleRows),
        maxVisibleRows: timelineGeom.maxVisibleRows,
        width: dims.width,
      })
    : null

  // 连接总览面板（/connections）：窗口化，预算与渲染同源（connectionsGeometry）
  const connectionsGeom = connectionsGeometry(dims.height, connectionsData)
  const connectionRows = connectionsRowModel(connectionsData || {})
  const connectionCursor = connectionsCursorRow(connectionRows, connectionsIndex)
  const connectionsPanel = connectionsOpen
    ? h(ConnectionsPanel, {
        data: connectionsData || {},
        cursor: connectionCursor,
        scroll: connectionsScrollFor(connectionRows, connectionCursor, connectionsScroll, connectionsGeom.maxVisibleRows),
        maxVisibleRows: connectionsGeom.maxVisibleRows,
        width: dims.width,
      })
    : null

  // 科研模型配置面板（/models）：候选窗口与布局预算同源（modelPickerGeometry）
  const modelPickerGeom = modelPickerGeometry(dims.height, modelPicker)
  const modelPickerPanel = modelPicker
    ? h(ModelPickerPanel, {
        state: modelPicker,
        width: dims.width,
        itemRows: modelPickerGeom.itemRows,
        maxContentRows: modelPickerGeom.maxContentRows,
      })
    : null

  // 快捷键帮助面板（F1）：窗口化 + ↑↓/PageUp/PageDown 滚动
  const shortcutsGeom = shortcutsGeometry(dims.height)
  const shortcutsPanel = shortcutsOpen
    ? h(ShortcutsPanel, {
        width: dims.width,
        scroll: shortcutsScrollFor(shortcutsScroll, shortcutsGeom.totalRows, shortcutsGeom.maxVisibleRows),
        maxVisibleRows: shortcutsGeom.maxVisibleRows,
      })
    : null

  // 历史搜索面板（Ctrl+R）
  const historyPanel = historySearch
    ? h(HistorySearchPanel, { history, query: historySearch.query, index: historySearch.index, width: dims.width })
    : null

  // 研究待办面板（Ctrl+T）
  const todosPanel = todosOpen ? h(TodosPanel, { todos: todosList, width: dims.width }) : null

  // 全屏阅读器（/read digest|outline|cites|board|graph|ledger）
  const readerPanel = reader
    ? h(ReaderPanel, {
        title: reader.title,
        lines: reader.lines,
        offset: reader.offset,
        query: reader.query,
        searchMode: reader.searchMode,
        width: dims.width,
        height: readerRows(reader),
      })
    : null

  // Subagent 面板
  const subagentsPanel = subagentsOpen
    ? h(SubagentsPanel, {
        swarms: subagentsList,
        index: subagentsIndex,
        onHover: (i) => setSubagentsIndex(i),
        onSelect: (i) => {
          const s = subagentsList[i]
          setSubagentsOpen(false)
          if (s) {
            controllerRef.current?.subagentDetail?.(s.plan_id).then((detail) => {
              setSubagentDrill(detail || s)
              setStatus("Subagent 只读视图（Esc 返回）")
            }).catch(() => {})
          }
        },
      })
    : null

  // Subagent 只读下钻视图（整页替换会话区）
  const drillView = subagentDrill
    ? h(SubagentView, { swarm: subagentDrill, events: subagentDrill.events || [], onBack: () => setSubagentDrill(null) })
    : null

  // 审批面板（最高优先级，替换其他面板）
  const approvalPanel = approval
    ? h(ApprovalPanel, {
        approval,
        index: approvalIndex,
        onHover: (i) => setApprovalIndex(i),
        onSelect: (i) => void decide(APPROVAL_OPTIONS[i]?.key),
      })
    : null

  // 人工确认关口菜单（审批面板之后、其他面板之前）
  const gateMenuPanel = gateMenu
    ? h(GateMenuPanel, {
        taskId: gateMenu.taskId,
        index: gateIndex,
        onHover: (i) => setGateIndex(i),
        onSelect: (i) => chooseGate(i),
      })
    : null

  // 排队面板（无其他面板时显示）
  const queuePanel = queued.length > 0
    ? h(
        "box",
        { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.borderWarning },
        h("text", { content: ` 排队中（${queued.length}/3）· 当前回合结束后自动发送`, fg: TOKENS.warning }),
        ...queued.map((q, i) => h("text", { key: i, content: ` ${i + 1}. ${q}`.slice(0, 70), fg: TOKENS.textMuted }))
      )
    : null
  const activePanel = approvalPanel || gateMenuPanel || contextPanel || subagentsPanel || stepsPanel || insightPanel || timelinePanel || configPanel || connectionsPanel || modelPickerPanel || shortcutsPanel || historyPanel || todosPanel || panel || queuePanel || readerPanel

  const panelRows = activePanel
    ? approval
      ? APPROVAL_OPTIONS.length + 3
      : gateMenu
        ? GATE_MENU_ROWS
        : contextOpen
        ? 5
        : subagentsOpen
          ? Math.min(8, Math.max(1, subagentsList.length)) + 3
          : stepsOpen
        ? RESEARCH_STEPS.length + 3
          : insight
            ? insightPanelRows(insight.kind, insight.data)
            : timelineOpen
              ? timelineGeom.panelRows // 窗口化：科研时间线（含溢出指示行）
              : configOpen
            ? configGeom.panelRows // 窗口化：与 ConfigPanel 实际渲染同源（含溢出指示行）
            : connectionsOpen
              ? connectionsGeom.panelRows // 窗口化：连接总览（含溢出指示行）
              : modelPicker
              ? modelPickerGeom.panelRows // 窗口化：候选窗口 + 固定行 + 边框
              : shortcutsOpen
                ? shortcutsGeom.panelRows // 窗口化：可视行 + 头/脚 + 边框（含溢出指示）
                : historySearch
                ? Math.min(8, filterHistory(history, historySearch.query).length) + 3
                : todosOpen
                  ? Math.min(10, todosList.length) + 3
                  : reader
                    ? readerRows(reader) + 4
                    : paletteOpen
      ? paletteGeom.panelRows
      : suggestOpen
        ? candidates.length + 2
        : mentionOpen && mentionCandidates.length > 0
          ? mentionCandidates.length + 2
          : queued.length + 1
    : 0
  const headerRows = brandHeaderRows({ workspaceMode, status,
    width: contentWidthForTerminal(dims.width, { sidebarVisible: sidebarLayout === "docked" }) + 4 })
  const transcriptHeight = Math.max(1, (dims.height || 24) - chromeRows(panelRows, draft.split("\n").length) - (headerRows - 1))

  return h(AppShell, {
    state: {
      workspaceMode,
      terminalWidth: dims.width,
      terminalHeight: dims.height,
      draft,
      cursor,
      status,
      shimmerFrame,
      reducedMotion,
      appearance,
      lines,
      expanded,
      panel: activePanel,
      panelRows,
      scrollOffset,
      onScroll: (delta) => setScrollOffset((o) => clampScrollOffset(o + delta, displayLineCount, transcriptHeight)),
      spinner: { active: streaming, frame: spinnerFrame },
      shellMode,
      drill: drillView,
      booting,
      bootError,
      spinnerFrame,
      slotValues: {
        mode: planMode ? "⏸ plan" : goalText ? "◎ goal" : `${MODE_TOKENS[workspaceMode]?.symbol ?? "◇"} ${MODE_TOKENS[workspaceMode]?.label ?? "通用"}`,
        cwd: process.cwd(),
        theme: themeMode === "light" ? "☀ 浅色" : "🌙 深色",
      },
      tips: [
        { text: "Ctrl+P 命令面板", weight: 2 },
        { text: "Ctrl+G 切换通用/科研", weight: 2 },
        { text: goalText ? `◎ 目标：${goalText.slice(0, 24)}` : "/goal 设置研究目标", weight: 2 },
        { text: "双击 Ctrl+C 退出", weight: 1 },
      ],
      used: contextBreakdown?.input ?? null,
      total: contextBreakdown?.limit ?? null,
      sidebar: { hidden: sidebarUserHidden, mode: appearance.sidebar_mode, overlayOpen: sidebarOverlayOpen, scroll: sidebarScroll, sections: sidebarSections },
    },
  })
}

export async function run({ stdin, stdout, autoResume = false } = {}) {
  const reducedMotion = !motionEnabled()
  const renderer = await createCliRenderer({
    targetFps: 30,
    useMouse: true,
    enableMouseMovement: true,
    exitOnCtrlC: false,
    stdin: stdin || process.stdin,
    stdout: stdout || process.stdout,
    backgroundColor: "#0f1419",
  })
  const root = createRoot(renderer)
  const { Bridge } = await import("../bridge.js")
  const bridge = new Bridge()
  const controller = createSessionController({ bridge })
  const shutdown = () => {
    try { renderer.destroy() } catch {}
    try { bridge.close() } catch {}
    process.exit(0)
  }
  root.render(
    h(OpenTuiApp, {
      reducedMotion,
      controller,
      autoResume: Boolean(autoResume),
      onExit: shutdown,
    })
  )
  if (process.env.GEZHI_TUI_SELFTEST) {
    // 真控制台自检：渲染 2s，把帧统计写文件后退出（无 GUI 环境下验证原生渲染管线）
    const { writeFileSync } = await import("node:fs")
    let frames = 0
    let keys = 0
    let mice = 0
    renderer.on("frame", () => frames++)
    renderer.keyHandler?.on?.("keypress", () => keys++)
    renderer.on("raw_mouse", () => mice++)
    setTimeout(() => {
      const stats = renderer.stats || {}
      // 阈值取 10：Git Bash 管道环境下 stdout sync 受限（真控制台无此问题）
      const result = {
        ok: frames >= 10,
        frames,
        keys,
        mice,
        fps: Math.round(stats.fps ?? 0),
        mouseEnabled: renderer.useMouse !== false,
        terminalWidth: renderer.width,
        terminalHeight: renderer.height,
      }
      try { writeFileSync(process.env.GEZHI_TUI_SELFTEST, JSON.stringify(result, null, 2), "utf-8") } catch {}
      shutdown()
      process.exit(result.ok ? 0 : 1)
    }, 2000)
  }
  return renderer
}
