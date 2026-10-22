// 会话控制器 —— 框架无关的 bridge 交互层（注入 bridge，便于无 Python 单测）
// 职责：模式同步/切换、斜杠命令子集、非斜杠消息的 agent 发起 + 轮询 + 快照渲染
import { parseCommand, COMMANDS } from "../commands.js"
import { executionPlanLines } from "../execution-plan.js"
import { normalizeAppearance } from "./theme/preferences.js"
import { rankHypotheses, consensusFromBrief, buildExperimentTree } from "./components/research-panels.js"
import { timelineCounts, timelineDetail } from "./components/research-timeline.js"
import { REASONING_EFFORTS, approvalSummaryLine } from "../research-commands.js"
import { executionContextIdentity, sessionContextLines, wrapContextLines, sessionStatusLines, compactResultLines } from "../session-status.js"
import { activityReaderPayload } from "../activity.js"
import { redactSettings, redactText } from "../sensitive-text.js"
import { readinessText, assertOfflineDemo } from "../onboarding.js"
import { parseOutputRequest, toolOutputLines, OUTPUT_USAGE } from "../output-commands.js"
import { normalizePaste } from "../composer.js"
import { subagentLine } from "../transcript.js"
import { runQueueCommand } from "../queue-commands.js"
import { runReviewCommand } from "../review-commands.js"
import { runHistoryCommand } from "../history-commands.js"
import { saveRemoteExport } from "../remote-export.js"
import { safeCommandEcho } from "../sensitive-text.js"

const DEFAULT_AGENT = { general: "general", research: "research" }

// /agent 角色前缀解析：`/agent research <请求>` → 角色 research
const AGENT_NAMES = new Set(["general", "research", "pipeline", "literature", "extraction",
  "synthesis", "stats", "citation", "critic", "explore"])

function parseAgentRequest(args, defaultAgent = "research") {
  const s = (args || "").trim()
  const parts = s.split(/\s+/).filter(Boolean)
  if (!parts[0]) return { agent: defaultAgent, request: s }
  const head = parts[0].startsWith("@") ? parts[0].slice(1) : parts[0]
  if (AGENT_NAMES.has(head) && parts.length > 1) {
    return { agent: head, request: s.slice(s.indexOf(parts[0]) + parts[0].length).trimStart() }
  }
  return { agent: defaultAgent, request: s }
}
const POLL_INTERVAL_MS = 800
const POLL_MAX = 40 // ~32s

// 通用命令→bridge 映射（仅收录 bridge.py 真实存在的 cmd；参数透传）
export const BRIDGE_COMMAND_MAP = {
  tasks: "list",
  nodes: "nodes",
  agents: "agents",
  stats: "stats",
  export: "export",
  pause: "pause",
  resume: "resume",
  restart: "restart",
  delete: "delete",
  drop: "drop",
  ledger: "ledger",
  graph: "graph",
  board: "board",
  audit: "research_audit",
  evidence: "evidence_brief",
  doctor: "doctor",
  usage: "usage",
  settings: "settings_get",
  "llm-status": "llm_status",
  "context-status": "context_status",
  mcp: "mcp_list",
  sessions: "sessions",
  rewind: "session_rewind",
}

/** 通用行摘要：取最常见的可读字段组合 */
export function summarizeRow(row) {
  if (typeof row === "string") return row
  const id = row.session_id || row.id || row.task_id || row.name || ""
  const status = row.status ? ` · ${row.status}` : ""
  const title = row.summary || row.title || row.topic || row.request || row.desc || row.description || ""
  return `${id}${status}${title ? ` · ${title}` : ""}`.trim() || JSON.stringify(row).slice(0, 80)
}

/** 终态按 status 映射：只有成功状态使用成功标记，受阻/取消/中断/失败不得冒充完成 */
const TERMINAL_STATE_LINES = new Map([
  ["done", "✓ 会话完成"],
  ["completed", "✓ 会话完成"],
  ["blocked", "✗ 已阻止"],
  ["cancelled", "⏹ 已取消"],
  ["cancelling", "⏹ 已取消"],
  ["error", "✗ 出错"],
  ["failed", "✗ 失败"],
  ["partial", "◐ 部分完成"],
  ["interrupted", "⏹ 已中断"],
])

/** 从 session_events 提取可读行（assistant_message / tool_call / tool_result / completed / error）
 *  @param expanded Ctrl+O 全局展开：工具调用显示参数与结果摘要（默认折叠为单行） */
export function renderEvents(events = [], { expanded = false } = {}) {
  const lines = []
  let deltaBuffer = ""
  const flushDelta = () => {
    if (deltaBuffer) {
      lines.push({ text: deltaBuffer })
      deltaBuffer = ""
    }
  }
  for (const ev of events) {
    const payload = ev?.payload || {}
    if (ev.kind !== "assistant_delta") flushDelta()
    if (ev.kind === "assistant_message" || ev.kind === "user_message") {
      if (payload.text) lines.push({ text: payload.text, ...(ev.kind === "user_message" ? { user: true } : {}) })
      // 论断-引用强绑定：助手消息带 citations 时渲染引用角标行
      const citations = ev.kind === "assistant_message" && Array.isArray(payload.citations) ? payload.citations : []
      citations.slice(0, 5).forEach((c, i) => {
        const label = c.title || c.id || c.doi || c.url || "来源未记录"
        const locator = c.doi || c.url || ""
        lines.push({ text: `  [${i + 1}] ${String(label).slice(0, 60)}${locator ? ` · ${String(locator).slice(0, 40)}` : ""}`, dim: true, kind: "citation" })
      })
    } else if (ev.kind === "assistant_delta") {
      // 流式增量：连续 delta 合并为一行，边到边渲染
      deltaBuffer += payload.delta || payload.text || ""
    } else if (ev.kind === "subagent_status") {
      // 子代理进度行：◆ 运行 / ✔ 完成 / ✘ 失败
      // journal 口径是 type，bridge 事件口径是 kind：统一转换后复用活模块
      const spans = subagentLine({ type: "subagent_status", payload: ev.payload, timestamp: ev.timestamp })
      if (spans) lines.push({ text: spans.map((s) => s.t).join(""), dim: false, kind: "subagent" })
    } else if (ev.kind === "tool_call") {
      const name = payload.name || payload.tool || "工具"
      if (!expanded) {
        lines.push({ text: `⚙ ${name}`, dim: true, kind: "tool" })
      } else {
        lines.push({ text: `⚙ ${name}`, kind: "tool" })
        const args = payload.args || payload.arguments || payload.input
        if (args) lines.push({ text: `  参数：${JSON.stringify(args).slice(0, 120)}`, dim: true, kind: "tool_detail" })
      }
    } else if (ev.kind === "tool_result") {
      const result = payload.result || {}
      const diff = result.diff || payload.diff
      if (typeof diff === "string" && diff.trim()) {
        lines.push({ text: `  ${result.path || result.file || "diff"}`, dim: true, kind: "diff", diff })
      } else if (expanded) {
        const brief = result.ok === false ? `✗ ${result.error || "失败"}` : `✓ ${String(result.text || result.message || "完成").slice(0, 80)}`
        lines.push({ text: `  ${brief}`, dim: result.ok === false, kind: "tool_detail" })
      }
    } else if (ev.kind === "completed") {
      const status = String(payload.status || "done").trim() || "done"
      const label = TERMINAL_STATE_LINES.get(status)
      lines.push({ text: label ? `${label}（${status}）` : `○ ${status}`, dim: true })
    } else if (ev.kind === "error") {
      lines.push({ text: `✗ 出错：${payload.message || payload.error || "未知错误"}`, dim: true })
    }
  }
  flushDelta()
  return lines
}

/**
 * @param bridge Bridge 实例（或同接口 fake：{call, connection, whenReady}）
 */
export function createSessionController({ bridge, now = () => Date.now(), sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  let workspaceMode = "general"
  let currentSessionId = null
  let currentTaskId = null // /open 后跟踪当前研究任务：阅读/池类命令带 task_id
  let lastLaunchPending = false
  let expanded = false // Ctrl+O 全局展开（工具调用详情）
  let planMode = false // Plan 模式：只读规划
  let goalText = "" // 研究目标：提交时注入上下文，footer 徽标常驻

  async function readMode() {
    await bridge.whenReady?.()
    const r = await bridge.call({ cmd: "workbench_mode" }, { timeoutMs: 10000 }).catch(() => ({ ok: false }))
    if (r?.ok && ["general", "research"].includes(r.mode)) workspaceMode = r.mode
    return workspaceMode
  }

  async function setMode(next) {
    await bridge.whenReady?.()
    const r = await bridge.call({ cmd: "workbench_mode", mode: next }, { timeoutMs: 10000 }).catch((e) => ({ ok: false, error: String(e.message || e) }))
    if (!r?.ok || !["general", "research"].includes(r.mode)) {
      return { ok: false, error: r?.error || "切换失败" }
    }
    workspaceMode = r.mode
    return { ok: true, mode: workspaceMode }
  }

  async function runCommand(parsed, raw = "") {
    const { name, args } = parsed
    if (name === "mode") {
      const next = (args || "").trim()
      if (!next || next === "toggle") {
        const target = workspaceMode === "general" ? "research" : "general"
        const r = await setMode(target)
        return { lines: [{ text: r.ok ? `✓ 已切换到${r.mode === "general" ? "通用" : "科研"}模式` : `✗ ${r.error}` }], mode: r.mode }
      }
      if (!["general", "research"].includes(next)) return { lines: [{ text: `✗ 未知模式：${next}（general|research|toggle）`, dim: true }] }
      const r = await setMode(next)
      return { lines: [{ text: r.ok ? `✓ 已切换到${r.mode === "general" ? "通用" : "科研"}模式` : `✗ ${r.error}` }], mode: r.mode }
    }
    if (name === "goal") {
      // 研究目标模式：设置后每次提交注入目标上下文
      const arg = (args || "").trim()
      if (arg === "off" || arg === "clear" || arg === "done") {
        goalText = ""
        return { lines: [{ text: "◎ 研究目标已清除" }] }
      }
      if (arg === "status" || !arg) {
        return goalText
          ? { lines: [{ text: `◎ 当前目标：${goalText}` }, { text: "每轮提交将附带目标与自评要求；/goal off 清除", dim: true }] }
          : { lines: [{ text: "（未设置目标）用法：/goal <研究目标>", dim: true }] }
      }
      goalText = arg.slice(0, 500)
      return { lines: [{ text: `◎ 研究目标已设置：${goalText}`.slice(0, 120) }, { text: "提交消息时将附带目标上下文；/goal off 清除", dim: true }] }
    }
    if (name === "fact") {
      // 世界模型事实：add/support/challenge/del/list
      const parts = (args || "").trim().split(/\s+/)
      const sub = parts[0] || "list"
      if (sub === "list") {
        const r = await facts()
        if (!r.length) return { lines: [{ text: "（世界模型暂无事实，/fact add <论断> 录入）", dim: true }] }
        return { lines: r.slice(0, 10).map((f) => ({ text: `${f.id} · ${f.claim.slice(0, 40)} · ${f.confidence}信心 · ${f.status} · 支${f.supports} 反${f.challenges}`.slice(0, 100) })) }
      }
      if (sub === "add") {
        const claim = parts.slice(1).join(" ").trim()
        if (!claim) return { lines: [{ text: "用法：/fact add <论断> [doi或url]", dim: true }] }
        const sourceArg = parts[1] && /^(https?:\/\/|10\.\d{4})/.test(parts[1]) ? parts[1] : ""
        const source = sourceArg.startsWith("10.")
          ? { id: sourceArg, doi: sourceArg, source_type: "user_provided" }
          : sourceArg
            ? { id: sourceArg, url: sourceArg, source_type: "user_provided" }
            : { id: `user_${now().toString(36)}`, source_type: "user_provided" }
        const r = await bridge.call({ cmd: "world_fact_save", fact: { claim, source, topic: "" } }).catch((e) => ({ ok: false, error: String(e.message || e) }))
        return { lines: [{ text: r?.ok ? `✓ 事实已录入：${r.fact.id}` : `✗ ${r?.error || "录入失败"}`, dim: !r?.ok }] }
      }
      if (sub === "support" || sub === "challenge") {
        const id = parts[1] || ""
        if (!id) return { lines: [{ text: `用法：/fact ${sub} <事实ID>`, dim: true }] }
        const r = await factVote(id, sub)
        return { lines: [{ text: r.ok ? `✓ 已${sub === "support" ? "支持" : "反驳"} ${id}` : `✗ ${r.error || "投票失败"}`, dim: !r.ok }] }
      }
      if (sub === "del" || sub === "delete") {
        const id = parts[1] || ""
        if (!id) return { lines: [{ text: "用法：/fact del <事实ID>", dim: true }] }
        const r = await bridge.call({ cmd: "world_fact_delete", id }).catch((e) => ({ ok: false, error: String(e.message || e) }))
        return { lines: [{ text: r?.ok ? `✓ 已删除 ${id}` : `✗ ${r?.error || "删除失败"}`, dim: !r?.ok }] }
      }
      return { lines: [{ text: "用法：/fact add <论断> | support|challenge|del <ID> | list", dim: true }] }
    }
    if (name === "peer-review") {
      // AI 审稿人：对科研记录做确定性多维评分
      const id = (args || "").trim()
      if (!id) return { lines: [{ text: "用法：/peer-review <科研记录ID>（/notebook 查看）", dim: true }] }
      const r = await bridge.call({ cmd: "peer_review", id }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      if (!r?.ok) return { lines: [{ text: `✗ ${r?.error || "审稿失败"}`, dim: true }] }
      const dimOrder = [["structure", "结构"], ["evidence", "证据"], ["testability", "可检验"], ["closure", "闭合"], ["depth", "深度"]]
      const lines = [
        { text: `🔍 AI 审稿 · ${r.record_id}（${r.kind} · ${r.status}）${r.title ? ` · ${r.title}` : ""}`.slice(0, 100) },
        { text: dimOrder.map(([k, label]) => `${label} ${r.dimensions[k]}`).join(" · ") + ` → 总评 ${r.overall}（${r.verdict}）` },
      ]
      for (const s of r.suggestions || []) lines.push({ text: `  · ${s}`.slice(0, 110), dim: true })
      lines.push({ text: ` ${r.boundary}`, dim: true })
      return { lines }
    }
    if (name === "reproduce") {
      // 论文复现流水线：计划→人工确认→白名单执行→实验记录
      const topic = (args || "").trim()
      if (!topic) return { lines: [{ text: "用法：/reproduce <论文课题或方向>", dim: true }] }
      const r = await bridge.call({ cmd: "workflow_run", workflow_id: "reproduce", topic }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      if (!r?.ok) return { lines: [{ text: `✗ ${r?.error || "启动失败"}`, dim: true }] }
      return { lines: [
        { text: `✓ 复现流水线已启动：${r.task_id}（${topic}）` },
        { text: "节点：复现计划 → 人工确认 → 白名单执行；/open " + r.task_id + " 跟踪", dim: true },
      ] }
    }
    if (name === "approve" || name === "deny") {
      if (!currentSessionId) return { lines: [{ text: "✗ 当前没有进行中的会话", dim: true }] }
      let callId = (args || "").trim()
      if (!callId) {
        const snap = await bridge.call({ cmd: "session_snapshot", session_id: currentSessionId }).catch(() => ({ ok: false }))
        callId = snap?.snapshot?.pending?.tool_call_id || snap?.snapshot?.pending?.id || ""
      }
      if (!callId) return { lines: [{ text: "✗ 没有待审批的调用", dim: true }] }
      const r = await bridge.call({ cmd: name === "approve" ? "agent_approve" : "agent_deny", session_id: currentSessionId, tool_call_id: callId }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      return { lines: [{ text: r?.ok ? `${name === "approve" ? "✓ 已允许" : "✓ 已拒绝"} ${callId}` : `✗ ${r?.error || "审批失败"}`, dim: !r?.ok }] }
    }
    if (name === "agent" || name === "spawn" || name === "review") {
      // 智能体执行：角色前缀解析，新开会话
      const text = (args || "").trim()
      if (!text) return { lines: [{ text: `用法：/${name} <任务请求>`, dim: true }] }
      const parsedAgent = parseAgentRequest(text, DEFAULT_AGENT[workspaceMode] || "research")
      const mode = name === "review" ? "review" : "agent"
      const agent = name === "review" && parsedAgent.agent !== "general" ? "critic" : parsedAgent.agent
      return launchAgent(parsedAgent.request, { agent, mode })
    }
    if (name === "queue" || name === "reviews" || name === "history") {
      // 实验队列 / 复核收件箱 / 对话记录
      // 复用活模块：重试标识、脱敏、分页身份校验全在模块内；busy 时也立即可用（本地命令优先于排队）
      const out = []
      let report = null
      const context = {
        bridge,
        out: (text) => out.push({ text: String(text) }),
        openReport: (title, text) => { report = { title, lines: String(text).split("\n") } },
        currentAgentSession: currentSessionId || "",
        onSettings: () => {},
        refreshSessionStatus: () => {},
      }
      try {
        const handled = name === "queue"
          ? await runQueueCommand("queue", args, context)
          : name === "reviews"
            ? await runReviewCommand("reviews", args, context)
            : await runHistoryCommand("history", args, context)
        if (!handled) return { lines: [{ text: `用法：/${name} …`, dim: true }] }
      } catch (error) {
        out.push({ text: `✗ ${error?.message || error}`, dim: true })
      }
      if (report) return { openReader: { title: report.title, lines: report.lines } }
      return { lines: out.length ? out : [{ text: "（无输出）", dim: true }] }
    }
    if (name === "session-export") {
      // 远程会话导出：内容本地落盘，服务器路径永不采用
      const parts = (args || "").trim().split(/\s+/).filter(Boolean)
      const id = parts[0] || currentSessionId || ""
      const format = (parts[1] || "markdown").toLowerCase()
      if (!id) return { lines: [{ text: "用法：/session-export <会话ID> [markdown|json]", dim: true }] }
      if (!["markdown", "json"].includes(format)) return { lines: [{ text: "格式仅支持 markdown|json", dim: true }] }
      try {
        const reply = await bridge.call({ cmd: "session_export", session_id: id, format })
          .catch((e) => ({ ok: false, error: String(e.message || e) }))
        if (reply?.ok !== true) throw new Error(redactText(reply?.error || "导出失败"))
        const destination = await saveRemoteExport(reply, id, format)
        return { lines: [{ text: "✓ 会话已导出" }, { text: destination, dim: true }] }
      } catch (error) {
        return { lines: [{ text: `✗ ${error?.message || error}`, dim: true }] }
      }
    }
    if (name === "cancel-agent") {
      // 取消当前/指定 Agent 会话（流式或等待中的回合均可取消）
      const id = (args || "").trim() || currentSessionId || ""
      if (!id) return { lines: [{ text: "✗ 当前没有进行中的会话", dim: true }] }
      const r = await bridge.call({ cmd: "agent_cancel", session_id: id }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      return { lines: [{ text: r?.ok ? `✓ 已取消 ${r?.result?.status || "会话"}` : `✗ ${r?.error || "取消失败"}`, dim: !r?.ok }] }
    }
    if (name === "digest") {
      // /digest 别名：摘要稿阅读器（与 /read digest 同一入口）
      return runCommand({ name: "read", args: "digest" }, raw)
    }
    // ---- Phase 6：bridge 直连命令子集 ----
    if (name === "status") {
      const r = await bridge.call({ cmd: "context_status" }).catch(() => ({ ok: false }))
      const c = r?.status || r?.context || {}
      const used = Number(c.used ?? c.tokens ?? 0)
      const total = Number(c.total ?? c.limit ?? 0)
      return { lines: [{ text: total > 0 ? `上下文占用 ${Math.round((used / total) * 100)}%（${used}/${total}）` : "上下文状态不可用", dim: !total }] }
    }
    if (name === "progress") {
      const id = (args || "").trim() || currentSessionId || "";
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) return { lines: [{ text: "用法：/progress [会话ID]", dim: true }] };
      const selected = currentSessionId;
      const response = await bridge.call({ cmd: "session_plan", session_id: id }).catch(error => ({ ok: false, error: String(error?.message || error) }));
      if (!args.trim() && selected !== currentSessionId) return { lines: [] };
      if (response?.ok !== true) return { lines: [{ text: `✗ ${redactText(response?.error || "计划读取失败")}`, dim: true }] };
      if (response.plan?.session_id !== id) return { lines: [{ text: "计划与会话不匹配，请重新读取", dim: true }] };
      return { lines: executionPlanLines(response.plan).map(text => ({ text })) };
    }
    if (name === "session-status" || name === "compact") {
      // 会话模型/上下文/预算/等待原因；compact 同族
      const id = (args || "").trim() || currentSessionId || ""
      if ((args || "").trim().split(/\s+/).length > 1 || !/^[A-Za-z0-9_-]{1,128}$/.test(id)) {
        return { lines: [{ text: `用法：/${name} [会话ID]（无参数使用当前会话；/sessions 查看档案）`, dim: true }] }
      }
      const r = await bridge.call({ cmd: name === "compact" ? "session_compact" : "session_status", session_id: id })
        .catch((e) => ({ ok: false, error: String(e.message || e) }))
      if (r?.ok !== true) return { lines: [{ text: `✗ ${r?.error || (name === "compact" ? "压缩失败" : "读取失败")}`, dim: true }] }
      if (name === "compact") return { lines: compactResultLines(r.result || {}).map((text) => ({ text })) }
      return { lines: sessionStatusLines(r.status || { session_id: id }).map((text) => ({ text })) }
    }
    if (name === "cancel-agent") {
      // 取消智能体会话：可带会话 ID，无参用当前会话
      const id = (args || "").trim() || currentSessionId
      if (!id) return { lines: [{ text: "✗ 当前没有进行中的会话", dim: true }] }
      const r = await bridge.call({ cmd: "agent_cancel", session_id: id }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      return { lines: [{ text: r?.ok ? `✓ 已取消会话 ${id}` : `✗ ${r?.error || "取消失败"}`, dim: !r?.ok }] }
    }
    if (name === "cancel" || name === "continue") {
      const id = (args || "").trim() || currentSessionId
      if (!id) return { lines: [{ text: "✗ 当前没有进行中的会话", dim: true }] }
      const r = await bridge.call({ cmd: name === "cancel" ? "agent_cancel" : "agent_continue", session_id: id })
        .catch((e) => ({ ok: false, error: String(e.message || e) }))
      if (!r?.ok) return { lines: [{ text: `✗ ${r?.error || "操作失败"}`, dim: true }] }
      if (name === "cancel") return { lines: [{ text: `✓ 已取消会话 ${id}` }] }
      // 继续回执：有待审批调用时给出 /approve /deny 指引
      const result = r.result || {}
      const lines = [{ text: `✓ 已继续会话 ${id}` }]
      if (result.pending) {
        lines.push({ text: `待审批：${result.pending.tool || ""} · ${result.pending.capability || ""}`, dim: true })
        lines.push({ text: `参数：${approvalSummaryLine(result.pending)}`, dim: true })
        lines.push({ text: `批准：/approve ${id} ${result.pending.id}` })
        lines.push({ text: `拒绝：/deny ${id} ${result.pending.id}` })
      }
      if (result.error) lines.push({ text: `✗ ${redactText(result.error)}`, dim: true })
      return { lines }
    }
    if (name === "skills") {
      const r = await bridge.call({ cmd: "skills_list" }).catch(() => ({ ok: false }))
      const rows = r?.skills || []
      if (!rows.length) return { lines: [{ text: "（暂无技能）", dim: true }] }
      const skillModes = { auto: "auto（按阶段选择）", on: "on（明确启用）", off: "off（停用）" }
      const invocationStates = { available: "可按需加载正文", disabled: "已停用，模型工具不能读取",
        manual_only: "禁止模型调用（手动入口待支持）", unsupported: "要求独立上下文执行；当前加载器尚不支持" }
      const lines = []
      for (const s of rows.slice(0, 10)) {
        lines.push({ text: `  ${s.enabled === false ? "○" : "●"} ${s.name} · 模式 ${skillModes[s.mode] || "未记录"} — ${s.desc || s.description || ""}`.slice(0, 110) })
        lines.push({ text: `    ${invocationStates[s.invocation_status] || "调用状态未记录"}`, dim: true })
        lines.push({ text: `    来源 ${typeof s.source === "string" && s.source.trim() ? s.source : "未记录"}`, dim: true })
      }
      lines.push({ text: "设置：/skill <名> <auto|on|off> · 索引按阶段选择，正文按需读取。", dim: true })
      lines.push({ text: "实际纳入与读取记录：选择会话后 /context。", dim: true })
      return { lines }
    }
    if (name === "skill") {
      // 技能模式/启停
      const m = (args || "").trim().split(/\s+/)
      if (m.length !== 2 || !["auto", "on", "off"].includes(m[1])) return { lines: [{ text: "用法：/skill <名> <auto|on|off>", dim: true }] }
      const r = await bridge.call(m[1] === "auto"
        ? { cmd: "skill_mode", name: m[0], mode: "auto" }
        : { cmd: "skill_toggle", name: m[0], on: m[1] === "on" }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      return { lines: [{ text: r?.ok
        ? (m[1] === "auto"
          ? `技能 ${m[0]} 已设为 auto（按阶段选择）`
          : `技能 ${m[0]} 已${m[1] === "on" ? "启用" : "停用"}（当前：${(r.active || []).join(", ") || "无"}）`)
        : `✗ ${r?.error || "操作失败"}`, dim: !r?.ok }] }
    }
    if (name === "context") {
      // 执行上下文来源：有会话读冻结清单，无会话只报规则发现
      if ((args || "").trim()) return { lines: [{ text: "用法：/context（当前会话的执行上下文；无会话时查看规则发现信息）", dim: true }] }
      if (currentSessionId) {
        const r = await bridge.call({ cmd: "session_status", session_id: currentSessionId }).catch((e) => ({ ok: false, error: String(e.message || e) }))
        if (!r?.ok) return { lines: [{ text: `✗ ${r?.error || "执行上下文暂不可读"}`, dim: true }] }
        if (r.status?.session_id !== currentSessionId) return { lines: [{ text: "✗ 执行上下文与当前会话不匹配，请重试。", dim: true }] }
        const contextLines = [`会话 ${currentSessionId}`, executionContextIdentity(r.status), ...sessionContextLines(r.status)]
        return { openReader: { title: "执行上下文来源", lines: wrapContextLines(contextLines, 66) } }
      }
      const r = await bridge.call({ cmd: "context_status" }).catch(() => ({ ok: false }))
      if (!r?.ok) return { lines: [{ text: `✗ ${r?.error || "读取失败"}`, dim: true }] }
      const cx = r.context || {}
      if (!cx.found) return { lines: [{ text: "未发现 GEZHI.md；/init 可生成模板。选择会话后 /context 查看实际记录。", dim: true }] }
      return { lines: [
        { text: `发现 ${cx.path}${Number.isSafeInteger(cx.bytes) ? `（预览 ${cx.bytes} 字节）` : ""}` },
        { text: "当前文件发现信息；选择会话后 /context 查看实际纳入的来源。", dim: true },
        ...(cx.sections || []).map((s) => ({ text: `  · ${s}`, dim: true })),
      ] }
    }
    if ((name === "models" || name === "model") && !(args || "").trim()) {
      // 科研模型配置：本地开面板，远程交回主机
      if (bridge.connection().mode !== "local") return { lines: [{ text: "模型配置请在主机本机 TUI 或桌面操作。", dim: true }] }
      return { openModelPicker: true }
    }
    if (name === "activity") {
      // 只读活动投影：工具/子代理/子任务全景，不推断缺失
      const sid = (args || "").trim() || currentSessionId || currentTaskId || ""
      if (!sid) return { lines: [{ text: "请先打开会话，再查看只读活动。", dim: true }] }
      try {
        const r = await bridge.call({ cmd: "session_activity", session_id: sid }).catch((e) => ({ ok: false, error: String(e.message || e) }))
        return { openReader: activityReaderPayload(r, sid) }
      } catch (error) {
        return { lines: [{ text: `✗ ${error?.message || error}`, dim: true }] }
      }
    }
    if (name === "outputs") {
      // 归档工具输出阅读器：字节偏移 list/read/search，读取不重放工具
      const sid = currentSessionId
      if (!sid) return { lines: [{ text: "先用 /session <ID> 选择会话。", dim: true }] }
      const parsed = parseOutputRequest(args, sid)
      if (!parsed) return { lines: [{ text: OUTPUT_USAGE, dim: true }] }
      const { request, format } = parsed
      const connection = JSON.stringify(bridge.connection())
      try {
        const reply = await bridge.call(request).catch((e) => ({ ok: false, error: String(e.message || e) }))
        if (reply?.ok !== true) throw new Error(redactText(reply?.error || "工具输出读取失败"))
        const rows = toolOutputLines(reply.output, format, sid, request)
        if (connection !== JSON.stringify(bridge.connection()) || currentSessionId !== sid) {
          return { lines: [{ text: "连接或会话已变化，工具输出未打开。", dim: true }] }
        }
        const text = normalizePaste(redactText(rows.join("\n")))
        return { openReader: { title: "工具输出", lines: text.split("\n") } }
      } catch (error) {
        return { lines: [{ text: `✗ ${error?.message || error}`, dim: true }] }
      }
    }
    if (name === "setup" || name === "demo") {
      // 首次运行检查 / 离线合成样例：只读 readiness，不写设置不开会话
      if ((args || "").trim()) return { lines: [{ text: `用法：/${name}`, dim: true }] }
      try {
        const r = await bridge.call({ cmd: name === "setup" ? "product_readiness" : "product_demo" })
          .catch((e) => ({ ok: false, error: String(e.message || e) }))
        if (r?.ok !== true) throw new Error(redactText(r?.error || "引擎未返回有效结果"))
        if (name === "setup") {
          const text = readinessText(r.readiness, bridge.connection().mode === "remote")
          return { openReader: { title: "首次运行检查", lines: text.split("\n") } }
        }
        const taskId = assertOfflineDemo(r)
        currentTaskId = taskId
        return { lines: [
          { text: `离线示例已启动：${taskId}` },
          { text: redactText(r.notice || "内置合成样例，仅供体验；不可作为真实科研证据。"), dim: true },
        ] }
      } catch (error) {
        return { lines: [{ text: `✗ ${error?.message || error}`, dim: true }] }
      }
    }
    if (name === "plugins") {
      const r = await bridge.call({ cmd: "plugins_summary" }).catch(() => ({ ok: false }))
      const rows = (r?.plugins || []).slice(0, 10).map((s) => ({ text: `${s.enabled === false ? "○" : "●"} ${s.name}`, dim: s.enabled === false }))
      return { lines: rows.length ? rows : [{ text: "（暂无插件）", dim: true }] }
    }
    if (name === "usage") {
      const r = await bridge.call({ cmd: "usage" }).catch(() => ({ ok: false }))
      const text = typeof r?.text === "string" && r.text.trim() ? redactText(r.text) : ""
      if (text) return { lines: text.split("\n").map((line) => ({ text: line })) }
      const u = r?.usage || {}
      return { lines: [{ text: `用量：${JSON.stringify(u).slice(0, 120)}`, dim: true }] }
    }
    if (name === "workflows") {
      const r = await bridge.call({ cmd: "workflows" }).catch(() => ({ ok: false }))
      const rows = (r?.workflows || []).slice(0, 10).map((w) => ({ text: `${w.id} — ${w.title || w.name || ""}`.slice(0, 90) }))
      return { lines: rows.length ? rows : [{ text: "（暂无工作流）", dim: true }] }
    }
    if (name === "tasks") {
      const r = await bridge.call({ cmd: "list" }).catch(() => ({ ok: false }))
      const rows = (r?.tasks || r?.items || []).slice(0, 10).map((t) => ({ text: `${t.id || t.task_id} · ${t.status || "?"} · ${(t.title || t.topic || "").slice(0, 40)}` }))
      return { lines: rows.length ? rows : [{ text: "（暂无任务）", dim: true }] }
    }
    if (name === "agents") {
      const r = await bridge.call({ cmd: "agents" }).catch(() => ({ ok: false }))
      const rows = (r?.agents || []).slice(0, 10).map((a) => ({ text: `${a.name || a.id} — ${(a.desc || a.description || "").slice(0, 50)}` }))
      return { lines: rows.length ? rows : [{ text: "（暂无 Agent）", dim: true }] }
    }
    if (name === "new" && (args || "").trim()) {
      // 建任务并启动流水线：本地 create，远程 workflow_run
      const [topic, keywords = ""] = (args || "").split(/\s+--\s+/)
      const local = bridge.connection().mode === "local"
      const r = await bridge.call(local
        ? { cmd: "create", topic: topic.trim(), keywords: keywords.trim(), cite_style: "gbt7714" }
        : { cmd: "workflow_run", workflow_id: "research_pipeline", topic: topic.trim() })
        .catch((e) => ({ ok: false, error: String(e.message || e) }))
      if (!r?.ok) return { lines: [{ text: `✗ 创建失败：${r?.error || "未知错误"}`, dim: true }] }
      if (r.task_id) currentTaskId = r.task_id
      return {
        lines: [
          { text: `✓ 任务 ${r.task_id} 已启动流水线：${topic.trim()}`.slice(0, 100) },
          { text: `/open ${r.task_id} 跟踪进度 · /read digest 阅读产物`, dim: true },
        ],
      }
    }
    if (name === "new" || name === "clear") {
      currentSessionId = null
      return { lines: [], clear: true, notice: "已新建会话（保留当前模式）" }
    }
    if (name === "open") {
      // 打开任务：走引擎 command_center，随后探测人工确认关口
      const target = (args || "").trim()
      if (!target) return { lines: [{ text: "用法：/open <任务ID|序号>", dim: true }] }
      const r = await bridge.call({ cmd: "command", text: `/open ${target}` }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      if (!r?.ok) return { lines: [{ text: `✗ /open：${r?.error || "执行失败"}`, dim: true }] }
      if (r.task_id) currentTaskId = r.task_id
      const taskId = r.task_id || target
      const rows = (typeof r.text === "string" ? r.text : "").split("\n").filter(Boolean).slice(0, 40).map((line) => ({ text: line }))
      try {
        const st = await bridge.call({ cmd: "poll", task_id: taskId }).catch(() => ({ ok: false }))
        if (st?.ok && st.status === "waiting_human") {
          return { lines: rows.length ? rows : [{ text: `已打开任务 ${taskId}`, dim: true }], openGateMenu: { taskId } }
        }
      } catch { /* 关口探测失败不阻塞打开 */ }
      return { lines: rows.length ? rows : [{ text: "✓ /open 已完成", dim: true }], taskId }
    }
    if (name === "session") {
      // 全屏阅读完整会话快照
      const id = (args || "").trim()
      if (!id) return { lines: [{ text: "用法：/session <会话ID>", dim: true }] }
      const r = await bridge.call({ cmd: "session_snapshot", session_id: id }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      if (!r?.ok) return { lines: [{ text: `✗ ${r?.error || "读取失败"}`, dim: true }] }
      currentSessionId = id
      return {
        lines: [{ text: `已打开全屏阅读器：会话 ${id}`, dim: true }],
        openReader: { title: `会话 ${id}`, lines: JSON.stringify(redactSettings(r.snapshot || {}), null, 2).split("\n") },
      }
    }
    if (name === "sessions") {
      const r = await bridge.call({ cmd: "sessions" }).catch(() => ({ ok: false }))
      if (!r?.ok) return { lines: [{ text: "✗ 无法读取会话列表", dim: true }] }
      const rows = (r.sessions || []).slice(0, 8).map((s) => ({ text: `${s.session_id} · ${s.status} · ${s.summary || s.request || ""}`.slice(0, 120) }))
      return { lines: rows.length ? rows : [{ text: "（暂无会话）", dim: true }] }
    }
    if (name === "steer") {
      // 回合中追加指导：先取当前回合，绑定 expected_turn_id 防错绑
      if (!currentSessionId) return { lines: [{ text: "✗ 当前没有进行中的会话", dim: true }] }
      if (!args) return { lines: [{ text: "用法：/steer <指导意见>", dim: true }] }
      const st = await bridge.call({ cmd: "steer_status", session_id: currentSessionId }).catch(() => ({ ok: false }))
      const turnId = st?.steering?.turn_id || ""
      const r = await bridge.call({
        cmd: "steer_add", session_id: currentSessionId, request: args,
        ...(turnId ? { expected_turn_id: turnId } : {}),
      }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      return { lines: [{ text: r?.ok
        ? `✓ 已追加指导${r?.ticket?.id ? `（工单 ${r.ticket.id}）` : ""}：${args}`.slice(0, 100)
        : `✗ ${r?.error || "追加失败"}`, dim: !r?.ok }] }
    }
    if (name === "rewind") {
      if (!currentSessionId) return { lines: [{ text: "✗ 当前没有进行中的会话", dim: true }] }
      const turns = Math.max(1, Math.min(10, Number(args) || 1))
      const r = await bridge.call({ cmd: "session_rewind", session_id: currentSessionId, turns }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      return { lines: [{ text: r?.ok ? `✓ 已回滚 ${turns} 个用户轮次` : `✗ ${r?.error || "回滚失败"}`, dim: !r?.ok }] }
    }
    if (name === "plan") {
      const arg = (args || "").trim()
      planMode = arg === "off" ? false : arg === "on" ? true : !planMode
      return { lines: [{ text: planMode ? "⏸ Plan 模式已开启：下一条消息只读规划，不执行副作用" : "▶ Plan 模式已关闭" }], planMode }
    }
    if (name === "init") {
      const r = await bridge.call({ cmd: "context_init" }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      return { lines: [{ text: r?.ok ? `✓ 已生成研究上下文：${r.path || "GEZHI.md"}` : `✗ ${r?.error || "生成失败"}`, dim: !r?.ok }] }
    }
    if (name === "remote") {
      const arg = (args || "").trim() || "status"
      if (arg === "start") {
        const r = await bridge.call({ cmd: "remote_start" }).catch((e) => ({ ok: false, error: String(e.message || e) }))
        return { lines: [{ text: r?.ok ? `✓ 远程控制已启动：${r.url || ""}` : `✗ ${r?.error || "启动失败"}`, dim: !r?.ok }] }
      }
      if (arg === "stop") {
        const r = await bridge.call({ cmd: "remote_stop" }).catch((e) => ({ ok: false, error: String(e.message || e) }))
        return { lines: [{ text: r?.ok ? "✓ 远程控制已停止" : `✗ ${r?.error || "停止失败"}`, dim: !r?.ok }] }
      }
      const r = await bridge.call({ cmd: "remote_status" }).catch(() => ({ ok: false }))
      const running = r?.ok && r?.running
      return { lines: [{ text: running ? `● 远程运行中：${r.url || ""}` : "○ 远程未启动（/remote start 开启）", dim: !running }] }
    }
    if (name === "copy") {
      // 复制最近一条助手消息（OSC52 由前端执行；这里返回文本）
      const r = await bridge.call({ cmd: "session_events", session_id: currentSessionId || "", limit: 40 }).catch(() => ({ ok: false }))
      const events = (r?.events || []).filter((e) => e.kind === "assistant_message" && e.payload?.text)
      const last = events[events.length - 1]?.payload?.text
      if (!last) return { lines: [{ text: "（没有可复制的助手消息）", dim: true }] }
      return { lines: [{ text: last, copy: true }] }
    }
    if (name === "read") {
      // 全屏阅读器：拉全文后交壳层开 pager
      const verb = (args || "").trim()
      const titles = {
        digest: "摘要稿", outline: "综述框架", cites: "参考文献",
        board: "科研看板", graph: "证据图谱", ledger: "研究时间账",
      }
      if (!titles[verb]) {
        return { lines: [{ text: "可阅读：digest / outline / cites / board / graph / ledger", dim: true }] }
      }
      const r = await bridge.call({
        cmd: "command",
        text: `/read ${verb}`,
        ...(currentTaskId ? { task_id: currentTaskId } : {}),
      }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      if (!r?.ok) return { lines: [{ text: `✗ ${r?.error || "读取失败"}`, dim: true }] }
      const text = String(r.text || "").trim()
      if (!text) return { lines: [{ text: `（${titles[verb]}暂无内容）`, dim: true }] }
      return {
        lines: [{ text: `已打开全屏阅读器：${titles[verb]}（↑↓ 滚动 · / 搜索 · n 跳转 · Esc/q 返回）`, dim: true }],
        openReader: { title: titles[verb], lines: text.split("\n") },
      }
    }
    if (name === "palette" && !(args || "").trim()) {
      // 命令面板：交壳层以空查询打开
      return { openPalette: true }
    }
    if (name === "reasoning") {
      // 思考强度：客户端直写 settings_save
      const value = (args || "").trim()
      if (!value) {
        const r = await bridge.call({ cmd: "settings_get" }).catch(() => ({ ok: false }))
        const s = r?.settings || {}
        return { lines: [
          { text: `推理强度 ${s.llm_reasoning_effort || "auto"} · 模式 ${s.llm_thinking_style || "auto"} · 思考预算 ${s.llm_thinking_budget ?? "自动"}` },
          { text: `/reasoning <${REASONING_EFFORTS.join("|")}> · 更改作用于默认连接；独立模型请用 /profile save。`, dim: true },
        ] }
      }
      if (!REASONING_EFFORTS.includes(value)) {
        return { lines: [{ text: `用法：/reasoning <${REASONING_EFFORTS.join("|")}>`, dim: true }] }
      }
      if (bridge.connection().mode === "remote") {
        return { lines: [{ text: "模型配置请在主机本机 TUI 或桌面操作；远程连接不发送配置或密钥。", dim: true }] }
      }
      const r = await bridge.call({ cmd: "settings_save", settings: { llm_reasoning_effort: value } })
        .catch((e) => ({ ok: false, error: String(e.message || e) }))
      return { lines: [{ text: r?.ok ? `✓ 推理强度已设置为 ${value}` : `✗ ${r?.error || "保存失败"}`, dim: !r?.ok }] }
    }
    if (name === "help" || name === "?") {
      // 帮助：精选常用命令（完整目录经 / 补全与 Ctrl+P 命令面板发现）；总量如实告知
      const essentials = ["mode", "help", "clear", "new", "session", "sessions", "read", "outputs", "skills", "models"]
      const rows = essentials.map((key) => COMMANDS.find((c) => c.name === key)).filter(Boolean)
        .map((c) => ({ text: `/${c.name}${c.args ? ` ${c.args}` : ""} — ${c.desc || c.description || ""}`, dim: true }))
      return { lines: [{ text: `共 ${COMMANDS.length} 条命令；常用如下，输入 / 触发补全查看全部。` }, ...rows] }
    }
    // 通用映射：命令名 → bridge 命令，参数透传；数组响应自动成列
    const mapped = BRIDGE_COMMAND_MAP[name]
    if (mapped) {
      const r = await bridge.call({ cmd: mapped, ...(args ? { args } : {}) }).catch((e) => ({ ok: false, error: String(e.message || e) }))
      if (!r?.ok) return { lines: [{ text: `✗ /${name} 失败：${r?.error || "未知错误"}`, dim: true }] }
      const list = r.rows || r.items || r.tasks || r.workflows || r.sessions || r.agents || r.skills || r.plugins || r.nodes || null
      if (Array.isArray(list)) {
        const rows = list.slice(0, 10).map((row) => ({ text: summarizeRow(row).slice(0, 100) }))
        return { lines: rows.length ? rows : [{ text: "（空）", dim: true }] }
      }
      const text = r.text || r.message || (r.result ? JSON.stringify(r.result).slice(0, 160) : "✓ 已完成")
      return { lines: [{ text }] }
    }
    // 兜底：Python command_center 斜杠分发处理科研流水线、会话与扩展命令。
    // 响应 text 按行渲染，session_id 自动接管。
    const commandText = raw || `/${name}${args ? ` ${args}` : ""}`
    const r = await bridge.call({ cmd: "command", text: commandText, ...(currentTaskId ? { task_id: currentTaskId } : {}) }).catch((e) => ({ ok: false, error: String(e.message || e) }))
    if (!r?.ok) return { lines: [{ text: `✗ /${name}：${r?.error || "执行失败"}`, dim: true }] }
    if (r.result?.session_id) currentSessionId = r.result.session_id
    else if (r.session_id) currentSessionId = r.session_id
    if (r.task_id) currentTaskId = r.task_id // /open 之后阅读/池类命令带当前任务
    const text = typeof r.text === "string" ? r.text : ""
    const rows = text.split("\n").filter(Boolean).slice(0, 40).map((line) => ({ text: line }))
    return { lines: rows.length ? rows : [{ text: `✓ /${name} 已完成`, dim: true }], sessionId: currentSessionId || undefined }
  }
  async function submit(text, { onUpdate, prefix } = {}) {
    const parsed = parseCommand(text)
    if (parsed) return runCommand(parsed, text)
    // 研究目标注入：目标常驻上下文，每轮提交自评达成度
    const goalPrefix = goalText
      ? `[研究目标] ${goalText}\n[完成标准] 完成后对照目标自评达成情况并指出差距`
      : ""
    const request = [prefix, goalPrefix].filter(Boolean).length
      ? `${[prefix, goalPrefix].filter(Boolean).join("\n")}\n[请求] ${text}`
      : text
    return launchAgent(request, { agent: DEFAULT_AGENT[workspaceMode] || "research", mode: planMode ? "plan" : "agent", onUpdate, withSession: true })
  }

  /** agent 会话发起 + 轮询 + 快照渲染（普通提交与 /agent|/spawn|/review 共用；withSession=继续当前会话） */
  async function launchAgent(request, { agent, mode = "agent", onUpdate, withSession = false } = {}) {
    const local = bridge.connection().mode === "local"
    const launchId = `l_${now().toString(36)}`
    const r = await bridge.call({
      cmd: local ? "agent_launch" : "agent_run",
      request,
      mode,
      agent,
      workspace_mode: workspaceMode,
      ...(local ? { launch_id: launchId } : {}),
      ...(withSession && currentSessionId ? { session_id: currentSessionId } : {}),
    }).catch((e) => ({ ok: false, error: String(e.message || e) }))
    if (!r?.ok) return { lines: [{ text: `✗ 请求未被接受：${r?.error || "未知错误"}`, dim: true }] }
    const result = r.result || {}
    if (result.session_id) currentSessionId = result.session_id
    if (!result.session_id) return { lines: [{ text: "（无会话 ID，疑似 headless 回执）", dim: true }] }
    lastLaunchPending = Boolean(result.launch_pending)
    // 增量流式：cursor 分页只取新事件，边到边渲染
    let cursor = null
    let streamed = 0
    let streamUnsupported = false // 引擎不支持 session_events（legacy 回退：只给最终回复）
    let finalAnswer = typeof result.answer === "string" ? result.answer : ""
    const drain = async () => {
      // 同一游标失败重试一次（临时故障不丢事件页，也不推进游标）
      for (let attempt = 0; attempt < 2; attempt++) {
        const page = await bridge.call({ cmd: "session_events", session_id: result.session_id, limit: 60, cursor })
          .catch((e) => ({ ok: false, error: String(e.message || e) }))
        if (page?.ok) {
          if (page.reset) cursor = null // rewind 后从头重取（游标失效）
          else if (page.cursor) cursor = page.cursor
          const rows = renderEvents(page.events || [], { expanded })
          if (rows.length && onUpdate) onUpdate(rows)
          return page.events?.length || 0
        }
        if (/未知命令|unknown command|unsupported|not supported/i.test(page?.error || "")) {
          streamUnsupported = true
          return 0
        }
      }
      return 0
    }
    for (let i = 0; i < POLL_MAX && !streamUnsupported; i++) {
      await sleep(POLL_INTERVAL_MS)
      streamed += await drain()
      const st = await bridge.call({ cmd: "agent_launch_status", session_id: result.session_id }).catch(() => ({ ok: false }))
      if (!st?.ok) break
      if (st.result?.answer) finalAnswer = st.result.answer
      const pending = Boolean(st.pending?.id || st.launch_pending)
      if (!pending) break
    }
    if (!streamUnsupported) await drain()
    const snap = await bridge.call({ cmd: "session_snapshot", session_id: result.session_id }).catch(() => ({ ok: false }))
    const lines = []
    if (streamUnsupported) {
      // legacy 引擎回退：如实说明无事件流，展示最终回复
      lines.push({ text: finalAnswer || snap?.snapshot?.answer || "（引擎未返回事件流或最终回复）" })
      lines.push({ text: "（当前引擎不支持实时事件流；已展示最终回复，事件回放请升级引擎）", dim: true })
      return { lines, sessionId: result.session_id }
    }
    if (snap?.ok) {
      const s = snap.snapshot || {}
      lines.push({ text: `会话 ${result.session_id} · ${s.status || "?"} · agent=${s.agent || "?"}`, dim: true })
      if (s.status === "waiting_approval" && s.pending) {
        lines.push({ text: `⏸ 等待审批：${s.pending.tool || s.pending.name || "工具调用"}（/approve 允许 · /deny 拒绝）`, dim: true })
      }
    }
    if (streamed === 0 && lines.length <= 1) lines.push({ text: "（暂无事件输出）", dim: true })
    return { lines, sessionId: result.session_id }
  }

  // 侧栏数据：技能 / MCP / 模型 / 当前会话（失败降级为空，不阻塞主界面）
  /** 插件命令清单（命令面板插件候选；失败降级为空数组） */
  async function pluginCommands() {
    const r = await bridge.call({ cmd: "plugin_commands" }).catch(() => ({ ok: false }))
    return r?.ok && Array.isArray(r.commands) ? r.commands : []
  }

  async function sidebar() {
    const [skills, mcp, llm, status] = await Promise.all([
      bridge.call({ cmd: "skills_list" }).catch(() => ({ ok: false })),
      bridge.call({ cmd: "mcp_list" }).catch(() => ({ ok: false })),
      bridge.call({ cmd: "llm_status" }).catch(() => ({ ok: false })),
      currentSessionId ? bridge.call({ cmd: "session_status", session_id: currentSessionId }).catch(() => ({ ok: false })) : Promise.resolve({ ok: false }),
    ])
    const sections = []
    if (skills?.ok && Array.isArray(skills.skills)) {
      sections.push({ title: "技能", lines: skills.skills.slice(0, 6).map((s) => ({ text: `${s.enabled === false ? "○" : "●"} ${s.name}` })) })
    }
    if (mcp?.ok && Array.isArray(mcp.servers)) {
      sections.push({ title: "MCP", lines: mcp.servers.slice(0, 6).map((s) => ({ text: `${s.connected === false ? "○" : "●"} ${s.name}` })) })
    }
    if (llm?.ok) {
      const model = llm.status?.model || llm.model || ""
      if (model) sections.push({ title: "模型", lines: [{ text: String(model) }] })
    }
    if (status?.ok && status.status) {
      const s = status.status
      if (s.execution_plan?.has_plan) sections.push({ title: "执行计划", lines: executionPlanLines(s.execution_plan, { compact: true }).map(text => ({ text })) });
      sections.push({ title: "会话", lines: [{ text: `${currentSessionId} · ${s.status || "?"}` }, { text: `上下文 ${s.context?.input_tokens ?? "?"}/${s.context?.limit_tokens ?? "?"}` }] })
    }
    return sections
  }

  // 审批：查询待决审批 + 决策（allow_once / allow_session / deny）
  async function pollApproval() {
    if (!currentSessionId) return null
    const sid = currentSessionId
    const snap = await bridge.call({ cmd: "session_snapshot", session_id: sid }).catch(() => ({ ok: false }))
    // 陈旧响应守卫：悬挂快照返回时若已切换会话，不得替换当前审批
    if (currentSessionId !== sid) return null
    const s = snap?.snapshot
    if (s?.status === "waiting_approval" && s.pending) {
      return {
        sessionId: sid,
        tool: s.pending.tool || s.pending.name || "工具调用",
        callId: s.pending.tool_call_id || s.pending.id || "",
        reason: s.pending.reason || "",
        action: s.pending.action || null,
      }
    }
    return null
  }

  async function decideApproval(decision, callId) {
    if (!currentSessionId || !callId) return { ok: false, error: "没有待审批的调用" }
    const cmd = decision === "deny" ? "agent_deny" : "agent_approve"
    const r = await bridge.call({
      cmd,
      session_id: currentSessionId,
      tool_call_id: callId,
      ...(decision === "allow_session" ? { scope: "session" } : {}),
    }).catch((e) => ({ ok: false, error: String(e.message || e) }))
    return { ok: Boolean(r?.ok), error: r?.error }
  }

  // Subagent：计划列表 + 详情
  async function subagents() {
    const r = await bridge.call({ cmd: "swarm_list", limit: 20 }).catch(() => ({ ok: false }))
    if (!r?.ok) return []
    return (r.swarms || []).map((s) => ({
      plan_id: s.plan_id || s.id,
      status: s.status,
      request: s.request,
      branches: Array.isArray(s.branches) ? s.branches : [],
    }))
  }

  async function subagentDetail(planId) {
    const r = await bridge.call({ cmd: "swarm_get", plan_id: planId }).catch(() => ({ ok: false }))
    if (!r?.ok) return null
    return r.swarm || r.plan || null
  }

  // 上下文分解：消息/工具schema/预留/上限
  async function contextBreakdown() {
    const sid = currentSessionId
    if (!sid) return null
    const r = await bridge.call({ cmd: "session_status", session_id: sid }).catch(() => ({ ok: false }))
    const c = r?.status?.context
    if (!r?.ok || !c || sid !== currentSessionId || (r.status.session_id && r.status.session_id !== sid)) return null
    const count = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null
    return {
      messages: count(c.tokens),
      tools: count(c.schema_tokens),
      input: count(c.input_tokens),
      reserve: count(c.output_reserve_tokens),
      limit: count(c.limit_tokens),
    }
  }

  // --continue：恢复最近 Agent 会话
  async function resumeLatest() {
    try {
      await bridge.whenReady?.()
      const r = await bridge.call({ cmd: "chat_sessions" }).catch(() => ({ ok: false }))
      const sid = r?.sessions?.[0]?.session_id
      if (!sid) return { sessionId: null, message: "没有可恢复的会话：直接输入研究方向即可开始。" }
      currentSessionId = sid
      const cont = await bridge.call({ cmd: "agent_continue", session_id: sid }).catch(() => ({ ok: false }))
      return { sessionId: sid, message: cont?.ok ? undefined : `会话 ${sid} 已恢复（继续输入即可对话）` }
    } catch (e) {
      return { sessionId: null, message: `恢复会话失败：${String(e?.message || e).slice(0, 60)}` }
    }
  }

  // shell 模式：项目根目录内执行，输出封顶 10 行
  async function shell(command) {
    const r = await bridge.call({ cmd: "shell_exec", command }).catch((e) => ({ ok: false, error: String(e?.message || e) }))
    const rows = String(r?.text || "").split(/\r?\n/).filter(Boolean)
    const lines = [{ text: `$ ${command}`.slice(0, 90), dim: true },
      ...rows.slice(0, 10).map((text) => ({ text, dim: true }))]
    if (!rows.length && r?.ok) lines.push({ text: "（无输出）", dim: true })
    if (rows.length > 10 || r?.truncated) lines.push({ text: "（输出已截断，以上为已捕获内容的预览）", dim: true })
    if (!r?.ok) lines.push({ text: `✗ ${r?.error || "执行失败"}`, dim: true })
    if (Number.isInteger(r?.code) && (r?.ok || r.code !== 0)) {
      lines.push({ text: `exit ${r.code}`, dim: true })
    } else if (r?.ok) {
      lines.push({ text: "退出码未知", dim: true })
    }
    return { lines }
  }

  // ---- 科研分析面板（数据来自既有 bridge 命令，Python 端零改动）----
  /** 假设排行榜 */
  async function hypotheses() {
    const r = await bridge.call({ cmd: "notebook_list", kind: "hypothesis" }).catch(() => ({ ok: false }))
    if (!r?.ok) return []
    return rankHypotheses(r.records || [])
  }

  /** 共识计量：支持/背景/反驳 三段统计 */
  async function consensus() {
    const r = await bridge.call({ cmd: "evidence_brief" }).catch(() => ({ ok: false }))
    if (!r?.ok) return null
    return consensusFromBrief(r.brief || r)
  }

  /** 证据矩阵：记录 × 关系计数 × 缺口 × 下一问 */
  async function matrix() {
    const r = await bridge.call({ cmd: "evidence_brief" }).catch(() => ({ ok: false }))
    const records = r?.brief?.records || r?.records || []
    return records.map((row) => ({
      id: row.id || "",
      title: row.title || "",
      supports: Number(row.counts?.supports ?? 0),
      challenges: Number(row.counts?.challenges ?? 0),
      context: Number(row.counts?.context ?? 0),
      gaps: Array.isArray(row.gaps) ? row.gaps : [],
      followup: row.followup_draft || "",
    }))
  }

  /** 实验树：related_ids 父子森林 */
  async function experimentTree() {
    const r = await bridge.call({ cmd: "notebook_list" }).catch(() => ({ ok: false }))
    if (!r?.ok) return []
    return buildExperimentTree(r.records || [])
  }

  /** 世界模型事实卡片：跨任务共享事实 */
  async function facts() {
    const r = await bridge.call({ cmd: "world_fact_list" }).catch(() => ({ ok: false }))
    if (!r?.ok) return []
    return (r.facts || []).map((f) => ({
      id: f.id,
      claim: f.claim || "",
      topic: f.topic || "",
      confidence: f.confidence || "medium",
      status: f.status || "asserted",
      supports: Number(f.supports ?? 0),
      challenges: Number(f.challenges ?? 0),
      source: f.source || {},
    }))
  }

  // ---- 配置面板（/config）：聚合 settings + llm_status，即切即存 ----
  async function appearancePrefs() {
    const response = await bridge.call({ cmd: "settings_get" })
    if (!response?.ok || !response.settings) throw new Error(response?.error || "外观设置暂不可用")
    return normalizeAppearance(response.settings)
  }

  async function configItems() {
    const [settings, llm] = await Promise.all([
      bridge.call({ cmd: "settings_get" }).catch(() => ({ ok: false })),
      bridge.call({ cmd: "llm_status" }).catch(() => ({ ok: false })),
    ])
    const s = settings?.settings || {}
    const appearance = normalizeAppearance(s)
    const l = llm?.status || llm || {}
    return {
      llm_model: l.model || s.llm_model || "",
      reasoning_effort: l.reasoning_effort || "medium",
      default_offline: Boolean(s.default_offline),
      ...appearance,
      appearance_dark_mode: appearance.theme_mode !== "light",
      appearance_density: appearance.density,
      workspace_mode: s.workspace_mode || "research",
      lit_threshold: s.lit_threshold ?? 0.18,
      memory_enabled: s.memory_enabled !== false,
      experimental_message_queue: Boolean(s.experimental_message_queue),
      experimental_evidence_reviews: Boolean(s.experimental_evidence_reviews),
      models: Array.isArray(l.models) ? l.models : [],
    }
  }

  /** 保存一项配置（settings_save）；返回 {ok, error} */
  async function configSet(key, value) {
    const r = await bridge.call({ cmd: "settings_save", settings: { [key]: value } }).catch((e) => ({ ok: false, error: String(e.message || e) }))
    return { ok: Boolean(r?.ok), error: r?.error }
  }

  /** 连接总览（/connections）：model_registry.describe() 归一（连接/模型/路由 + 各阶段生效配置） */
  async function connections() {
    const r = await bridge.call({ cmd: "model_registry" }).catch(() => ({ ok: false }))
    const reg = r?.registry || {}
    const list = Array.isArray(reg.connections) ? reg.connections : []
    const profiles = Array.isArray(reg.profiles) ? reg.profiles : []
    const resolvedRaw = reg.resolved && typeof reg.resolved === "object" ? reg.resolved : {}
    const usedConnections = new Set(Object.values(resolvedRaw).map((row) => row?.connection_id).filter(Boolean))
    const usedProfiles = new Set(Object.values(resolvedRaw).map((row) => row?.profile_id).filter(Boolean))
    return {
      connections: list.map((c) => ({
        id: c.id || "",
        label: c.label || c.id || "",
        protocol: c.protocol || "chat_completions",
        base_url: c.base_url || "",
        has_api_key: Boolean(c.has_api_key || c.api_key),
        active: usedConnections.has(c.id || ""),
      })),
      profiles: profiles.map((p) => ({
        id: p.id || "",
        label: p.label || p.id || "",
        connection_id: p.connection_id || "legacy",
        model: p.model || "",
        reasoning_effort: p.reasoning_effort || "auto",
        active: usedProfiles.has(p.id || ""),
      })),
      routes: reg.routes && typeof reg.routes === "object" ? reg.routes : {},
      resolved: Object.entries(resolvedRaw).map(([stage, row]) => ({
        stage,
        profile_id: row?.profile_id || "legacy",
        connection_id: row?.connection_id || "legacy",
        model: row?.model || "",
        protocol: row?.protocol || "",
      })),
    }
  }

  /** 科研时间线（/timeline）：goal + 假设/证据/事实/实验树/待办 的流水线一览（只读，零 Python 改动） */
  async function timeline() {
    const [hyps, cons, factRows, tree, todoRows] = await Promise.all([
      hypotheses().catch(() => []),
      consensus().catch(() => null),
      facts().catch(() => []),
      experimentTree().catch(() => []),
      todos().catch(() => []),
    ])
    const stage = (key, count, detail, meter) => ({
      key,
      count,
      status: count > 0 ? "done" : "pending",
      detail: count > 0 ? detail : "",
      meter: count > 0 ? (meter ?? Math.min(1, count / 8)) : 0,
    })
    const stages = [
      { key: "goal", count: goalText ? 1 : 0, status: goalText ? "done" : "pending", detail: goalText ? `目标：${goalText.slice(0, 24)}` : "", meter: goalText ? 1 : 0 },
      stage("hypotheses", (hyps || []).length, timelineDetail("hypotheses", (hyps || []).length)),
      stage("evidence", cons?.total || 0, cons?.total ? `支持 ${cons.yes} · 背景 ${cons.mixed} · 反驳 ${cons.no}` : "", cons?.ratio?.yes ?? 0),
      stage("facts", (factRows || []).length, timelineDetail("facts", (factRows || []).length)),
      stage("experiments", (tree || []).length, timelineDetail("experiments", (tree || []).length)),
      stage("todos", (todoRows || []).length, timelineDetail("todos", (todoRows || []).length)),
    ]
    return { goal: goalText, stages, counts: timelineCounts(stages) }
  }

  /** 研究待办面板（Ctrl+T） */
  async function todos() {
    const r = await bridge.call({ cmd: "todo_list" }).catch(() => ({ ok: false }))
    if (!r?.ok) return []
    return (r.todos || []).map((t) => ({
      id: t.id || "",
      title: t.title || t.text || "",
      status: t.status || "pending",
    }))
  }

  /** 事实投票（支持 +1；面板 Enter 一键支持后刷新） */
  async function factVote(id, vote = "support") {
    const cmd = vote === "challenge" ? "world_fact_challenge" : "world_fact_support"
    const r = await bridge.call({ cmd, id }).catch((e) => ({ ok: false, error: String(e.message || e) }))
    return { ok: Boolean(r?.ok), error: r?.error }
  }

  /** 新颖性检测：特化 prompt 走普通 agent 回合 */
  async function novelty(topic) {
    const text = String(topic || "").trim()
    if (!text) return { lines: [{ text: "用法：/novelty <课题或方向>", dim: true }] }
    const prompt = `请对以下课题做新颖性检测（ Owl 式四段式）：\n1. 是否已有人做过（最接近的 3 项工作）\n2. 与现有工作的关键差异点\n3. 潜在风险与重复劳动提示\n4. 结论：新颖 / 部分新颖 / 已充分研究\n课题：${text}`
    return submit(prompt)
  }

  /** 研究目标：设置后每次提交注入目标上下文 */
  function setGoal(text) {
    goalText = String(text || "").trim().slice(0, 500)
    return goalText
  }

  // ---- 模型选择器（/models|/model） ----
  let picker = null // {stage, focus, loading, saving, catalog, index, effort, changedEffort, error}
  let pickerSeq = 0
  const pickerListeners = new Set()
  const notifyPicker = () => pickerListeners.forEach((listener) => listener(picker))
  const pickerIdentity = () => JSON.stringify(bridge.connection())
  function readCatalog(response) {
    if (response?.ok !== true) throw new Error(response?.error || "模型目录读取失败")
    const catalog = response.catalog
    if (!catalog || typeof catalog.revision !== "string" || typeof catalog.stage !== "string"
      || !Array.isArray(catalog.stages) || !Array.isArray(catalog.items)) {
      throw new Error("引擎未提供可核验的模型目录，请在主机更新后重试")
    }
    return { ...catalog, stages: catalog.stages.filter((v) => typeof v === "string").slice(0, 32),
      items: catalog.items.filter((row) => row && typeof row.id === "string").slice(0, 100) }
  }
  function modelPickerOpen(stage = "planning") {
    const serial = ++pickerSeq
    const connection = pickerIdentity()
    const valid = () => pickerSeq === serial && connection === pickerIdentity()
    if (bridge.connection().mode !== "local") { picker = null; return { remote: true } }
    // 同步先进入 loading 态（Esc 可取消；迟到目录不得复活面板）
    picker = { stage, focus: "model", loading: true, catalog: null, index: 0, effort: "auto", error: null }
    void (async () => {
      try {
        const catalog = readCatalog(await bridge.call({ cmd: "model_control_catalog", stage }))
        if (!valid()) return
        const index = Math.max(0, catalog.items.findIndex((row) => row.id === catalog.active_id))
        picker = { stage: catalog.stage, focus: "model", catalog, index,
          effort: catalog.items[index]?.reasoning_effort || "auto", changedEffort: false, loading: false, saving: false, error: null }
        notifyPicker()
      } catch (error) {
        if (valid() && picker) { picker = { ...picker, loading: false, error: String(error.message || error) }; notifyPicker() }
      }
    })()
    return picker
  }
  function modelPickerMove(delta) {
    const value = picker
    if (!value || value.loading || value.saving || !value.catalog) return
    if (value.focus === "stage") {
      const options = value.catalog.stages
      const index = options.indexOf(value.stage)
      const stage = options[Math.max(0, Math.min(options.length - 1, index + delta))]
      if (stage && stage !== value.stage) return void modelPickerOpen(stage)
    } else if (value.focus === "effort") {
      const options = value.catalog.items[value.index]?.efforts || []
      const index = options.findIndex((row) => row.value === value.effort)
      const option = options[Math.max(0, Math.min(options.length - 1, index + delta))]
      if (option) picker = { ...value, effort: option.value, changedEffort: true,
        error: option.enabled ? null : option.reason || "此档位未通过适配器校验" }
    } else {
      const index = Math.max(0, Math.min(value.catalog.items.length - 1, value.index + delta))
      picker = { ...value, index, effort: value.catalog.items[index]?.reasoning_effort || "auto", changedEffort: false, error: null }
    }
  }
  function modelPickerTab() {
    if (!picker || picker.loading || picker.saving) return
    picker = { ...picker, focus: { model: "stage", stage: "effort", effort: "model" }[picker.focus] || "model" }
  }
  async function modelPickerSave() {
    const value = picker
    if (!value || value.loading || value.saving || !value.catalog) return { ok: false }
    if (bridge.connection().mode !== "local") { picker = null; return { ok: false, remote: true } }
    const target = value.catalog.items[value.index]
    if (!target) return { ok: false }
    if (value.changedEffort && !target.efforts?.some((row) => row.value === value.effort && row.enabled === true)) {
      picker = { ...value, error: "所选思考档位未通过适配器校验，原配置保留" }
      return { ok: false }
    }
    picker = { ...value, saving: true, error: null }
    try {
      const response = await bridge.call({ cmd: "model_control_apply", stage: value.stage, profile_id: target.id,
        expected_revision: value.catalog.revision, ...(value.changedEffort ? { effort: value.effort } : {}) })
      const catalog = readCatalog(response)
      picker = null
      return { ok: true, catalog, settings: response.settings || {} }
    } catch (error) {
      picker = { ...value, saving: false, error: String(error.message || error) }
      return { ok: false, error: picker.error }
    }
  }
  function modelPickerClose() { pickerSeq += 1; picker = null; notifyPicker() }
  function onPickerState(listener) { pickerListeners.add(listener); return () => pickerListeners.delete(listener) }
  return {
    readMode,
    modelPickerOpen,
    onPickerState,
    modelPickerMove,
    modelPickerTab,
    modelPickerSave,
    modelPickerClose,
    get modelPicker() { return picker },
    setMode,
    submit,
    sidebar,
    pluginCommands,
    shell,
    contextBreakdown,
    subagents,
    subagentDetail,
    pollApproval,
    decideApproval,
    hypotheses,
    consensus,
    matrix,
    experimentTree,
    facts,
    factVote,
    configItems,
    appearancePrefs,
    configSet,
    connections,
    timeline,
    todos,
    novelty,
    setGoal,
    clearGoal() { goalText = "" },
    setSession(id) { currentSessionId = id || null },
    resumeLatest,
    get goal() { return goalText },
    setExpanded(next) { expanded = Boolean(next) },
    get expanded() { return expanded },
    get planMode() { return planMode },
    get mode() { return workspaceMode },
    get sessionId() { return currentSessionId },
    get taskId() { return currentTaskId },
    get connectionMode() { return bridge.connection().mode },
    get launchPending() { return lastLaunchPending },
  }
}
