// 科研分析面板组（创新层）
// 假设排行榜（Elo 式竞争排序）· 共识计量条 · 证据矩阵 · 实验树
// 数据全部来自既有 bridge 命令（notebook_list / evidence_brief），Python 端零改动；
// 纯函数导出供无终端单测，组件遵循"组件不写字面量颜色"规范（docs/tui-design-spec.md）
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"

const h = React.createElement

const STATUS_BONUS = { completed: 3, running: 1, planned: 0, failed: -1 }
const STATUS_LABEL = { completed: "已完成", running: "进行中", planned: "计划", failed: "失败" }

/** 关系计数：supports→支持 challenges→反驳 context→背景 */
function evidenceCounts(record) {
  const counts = { supports: 0, challenges: 0, context: 0 }
  for (const item of record.evidence || []) {
    if (item.relation in counts) counts[item.relation] += 1
  }
  return counts
}

/**
 * 假设排行榜：证据加权 + 状态加成 + 新近度
 * 分数 = 支持×2 + 背景×0.5 − 反驳×1.5 + 状态加成 + 新近度(0–1)
 * @param records notebook_list(kind=hypothesis) 返回的 records 数组
 * @returns 按分数降序的 [{id,title,score,status,evidence,updated_at}]
 */
export function rankHypotheses(records = []) {
  const now = Date.now()
  return records
    .map((r) => {
      const counts = evidenceCounts(r)
      const updated = Date.parse(r.updated_at || r.created_at || 0) || 0
      const ageDays = Math.max(0, (now - updated) / 86400000)
      const recency = updated ? Math.max(0, 1 - ageDays / 90) : 0
      const score = counts.supports * 2 + counts.context * 0.5 - counts.challenges * 1.5 +
        (STATUS_BONUS[r.status] ?? 0) + recency
      return {
        id: r.id,
        title: r.title || "(未命名假设)",
        score: Math.round(score * 100) / 100,
        status: r.status || "planned",
        evidence: counts,
        updated_at: r.updated_at || "",
      }
    })
    .sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
}

/**
 * 共识计量：支持→Yes / 背景→Mixed / 反驳→No
 * @param brief evidence_brief 响应（{records:[{counts:{supports,challenges,context}}]}）
 * @returns {{yes, mixed, no, total, ratio}} 计数与占比（total=0 时 ratio 为 null）
 */
export function consensusFromBrief(brief = null) {
  const tally = { yes: 0, mixed: 0, no: 0 }
  for (const row of brief?.records || []) {
    const c = row.counts || {}
    tally.yes += Number(c.supports || 0)
    tally.mixed += Number(c.context || 0)
    tally.no += Number(c.challenges || 0)
  }
  const total = tally.yes + tally.mixed + tally.no
  const ratio = total
    ? { yes: tally.yes / total, mixed: tally.mixed / total, no: tally.no / total }
    : null
  return { ...tally, total, ratio }
}

/**
 * 实验树：related_ids 构成父子森林
 * @param records notebook_list 返回的 records 数组
 * @returns 森林 [{id,title,status,depth,children}]（环路防御：DFS 访问集）
 */
export function buildExperimentTree(records = []) {
  const byId = new Map(records.map((r) => [r.id, r]))
  const childMap = new Map()
  const roots = []
  for (const r of records) {
    const parents = (r.related_ids || []).filter((pid) => byId.has(pid) && pid !== r.id)
    if (parents.length) {
      for (const pid of parents) {
        if (!childMap.has(pid)) childMap.set(pid, [])
        childMap.get(pid).push(r.id)
      }
    } else {
      roots.push(r.id)
    }
  }
  const seen = new Set()
  const build = (id, depth) => {
    if (seen.has(id)) return null // 环防御：重复引用只渲染一次
    seen.add(id)
    const r = byId.get(id)
    return {
      id,
      title: r.title || "(未命名)",
      status: r.status || "planned",
      kind: r.kind || "experiment",
      depth,
      children: (childMap.get(id) || []).map((cid) => build(cid, depth + 1)).filter(Boolean),
    }
  }
  const forest = roots.map((id) => build(id, 0)).filter(Boolean)
  // 兜底：互引环组（彼此为父，无根）在 DFS 后仍未访问的，按记录序追加为根
  for (const r of records) {
    if (!seen.has(r.id)) {
      const node = build(r.id, 0)
      if (node) forest.push(node)
    }
  }
  return forest
}

/** 树扁平化为可视行（缩进 + 枝形符号） */
export function flattenTree(nodes = [], out = []) {
  for (const n of nodes) {
    const indent = "  ".repeat(n.depth)
    const branch = n.depth === 0 ? "" : "└ "
    out.push({ text: `${indent}${branch}${n.title}`, node: n })
    flattenTree(n.children, out)
  }
  return out
}

/** 假设排行榜面板（↑↓ 选择，Enter 下钻由 runtime 决定，这里只展示） */
export function HypothesesPanel({ hypotheses = [], index = 0, width = 74 }) {
  if (!hypotheses.length) {
    return h(
      "box",
      { border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
      h("text", { content: " 假设排行榜：科研记录中暂无假设（/notebook 录入）", fg: TOKENS.textMuted })
    )
  }
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.modeResearch, width: Math.min(width, 78) },
    h("text", { content: " 假设排行榜 · 锦标赛式竞争排序（证据×2 · 反驳−1.5 · 状态加成 · 新近度）", fg: TOKENS.modeResearch }),
    ...hypotheses.slice(0, 8).map((row, i) =>
      h(
        "box",
        { key: row.id, flexDirection: "row", onMouseOver: () => undefined },
        h("text", { content: i === index ? `${SYMBOLS.pointer} ` : "  ", fg: i === index ? TOKENS.modeResearch : TOKENS.textMuted }),
        h("text", { content: `${String(i + 1).padStart(2)}. `, fg: TOKENS.textDim }),
        h("text", { content: row.title.slice(0, 34), fg: i === index ? TOKENS.textStrong : TOKENS.text }),
        h("text", { content: ` ${row.score.toFixed(1)}`, fg: row.score >= 3 ? TOKENS.success : row.score <= 0 ? TOKENS.error : TOKENS.warning }),
        h("text", { content: ` 支${row.evidence.supports} 反${row.evidence.challenges} 背${row.evidence.context}`, fg: TOKENS.textMuted }),
        h("text", { content: ` ${STATUS_LABEL[row.status] || row.status}`, fg: TOKENS.textDim })
      )
    ),
    h("text", { content: ` ↑↓ 选择 · Esc 关闭 · 依据科研记录（未验证科学真伪）`, fg: TOKENS.textDim })
  )
}

/** 共识计量条：Yes/Mixed/No 三段比例条 */
export function ConsensusMeter({ consensus = null, width = 74 }) {
  if (!consensus || !consensus.total) {
    return h(
      "box",
      { border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
      h("text", { content: " 共识计量：当前范围暂无支持/反驳材料（/evidence 录入）", fg: TOKENS.textMuted })
    )
  }
  const { yes, mixed, no, total, ratio } = consensus
  const barWidth = Math.max(10, Math.min(width, 60) - 4)
  const yesW = Math.round(ratio.yes * barWidth)
  const mixedW = Math.round(ratio.mixed * barWidth)
  const noW = Math.max(0, barWidth - yesW - mixedW)
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.modeResearch, width: Math.min(width, 64) },
    h("text", { content: ` 共识计量 · ${total} 条证据关系`, fg: TOKENS.modeResearch }),
    h(
      "box",
      { flexDirection: "row" },
      h("text", { content: " ", fg: TOKENS.textMuted }),
      yesW ? h("text", { content: "█".repeat(yesW), fg: TOKENS.success }) : null,
      mixedW ? h("text", { content: "█".repeat(mixedW), fg: TOKENS.warning }) : null,
      noW ? h("text", { content: "█".repeat(noW), fg: TOKENS.error }) : null
    ),
    h("text", { content: ` 支持 ${yes} · 背景 ${mixed} · 反驳 ${no}`, fg: TOKENS.textMuted }),
    h("text", { content: ` ${SYMBOLS.bullet} 共识来自录入证据的关系统计，未验证来源真实性`, fg: TOKENS.textDim })
  )
}

/** 证据矩阵面板：记录 × 支持/反驳/背景/缺口/下一问 */
export function EvidenceMatrixPanel({ matrix = [], width = 74 }) {
  if (!matrix.length) {
    return h(
      "box",
      { border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
      h("text", { content: " 证据矩阵：当前范围暂无研究记录（/evidence 录入）", fg: TOKENS.textMuted })
    )
  }
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.modeResearch, width: Math.min(width, 80) },
    h("text", { content: " 证据矩阵 · 记录 × 材料关系 × 缺口 × 下一问", fg: TOKENS.modeResearch }),
    ...matrix.slice(0, 8).map((row) =>
      h(
        "box",
        { key: row.id, flexDirection: "column" },
        h(
          "box",
          { flexDirection: "row" },
          h("text", { content: ` ${row.title || row.id}`.slice(0, 46), fg: TOKENS.textStrong }),
          h("text", { content: `  支${row.supports} 反${row.challenges} 背${row.context}`, fg: TOKENS.textMuted })
        ),
        row.gaps?.length
          ? h("text", { content: `   待补：${row.gaps.join("；")}`.slice(0, 70), fg: TOKENS.warning })
          : null,
        row.followup
          ? h("text", { content: `   下一问：${row.followup}`.slice(0, 70), fg: TOKENS.textDim })
          : null
      )
    ),
    h("text", { content: ` ${SYMBOLS.bullet} 关系为录入者判断；合成样例不能作为真实实证证据`, fg: TOKENS.textDim })
  )
}

/** 实验树面板：分支探索/晋升可视化 */
export function ExperimentTreePanel({ tree = [], width = 74 }) {
  const rows = flattenTree(tree)
  if (!rows.length) {
    return h(
      "box",
      { border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
      h("text", { content: " 实验树：科研记录中暂无实验（/notebook 录入 kind=experiment）", fg: TOKENS.textMuted })
    )
  }
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.modeResearch, width: Math.min(width, 80) },
    h("text", { content: " 实验树 · 分支探索 / 剪枝 / 晋升", fg: TOKENS.modeResearch }),
    ...rows.slice(0, 10).map((row, i) =>
      h(
        "box",
        { key: `${row.node.id}-${i}`, flexDirection: "row" },
        h("text", { content: row.text.slice(0, 52), fg: row.node.status === "failed" ? TOKENS.textDim : TOKENS.text }),
        h("text", { content: ` ${STATUS_LABEL[row.node.status] || row.node.status}`, fg: row.node.status === "completed" ? TOKENS.success : row.node.status === "failed" ? TOKENS.error : TOKENS.textDim })
      )
    ),
    h("text", { content: ` ${SYMBOLS.bullet} 共 ${rows.length} 个节点；related_ids 构成父子分支`, fg: TOKENS.textDim })
  )
}

/** 世界模型事实卡片面板：论断 + 置信度 + 状态 + 票数 */
const FACT_STATUS_COLOR = { verified: TOKENS.success, challenged: TOKENS.error, asserted: TOKENS.info, synthetic: TOKENS.textDim }
const FACT_STATUS_LABEL = { verified: "已核验", challenged: "受反驳", asserted: "已录入", synthetic: "模拟" }
const CONFIDENCE_LABEL = { high: "高", medium: "中", low: "低" }

export function FactsPanel({ facts = [], index = 0, width = 74 }) {
  if (!facts.length) {
    return h(
      "box",
      { border: true, borderStyle: "single", borderColor: TOKENS.borderFocus },
      h("text", { content: " 世界模型：暂无事实卡片（/fact add <论断> 录入）", fg: TOKENS.textMuted })
    )
  }
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.modeResearch, width: Math.min(width, 82) },
    h("text", { content: " 世界模型 · 跨任务共享事实（论断强绑定来源）", fg: TOKENS.modeResearch }),
    ...facts.slice(0, 8).map((f, i) =>
      h(
        "box",
        { key: f.id, flexDirection: "row" },
        h("text", { content: i === index ? `${SYMBOLS.pointer} ` : "  ", fg: i === index ? TOKENS.modeResearch : TOKENS.textMuted }),
        h("text", { content: (f.claim || "").slice(0, 44), fg: i === index ? TOKENS.textStrong : TOKENS.text }),
        h("text", { content: ` ${CONFIDENCE_LABEL[f.confidence] || f.confidence}信心`, fg: f.confidence === "high" ? TOKENS.success : f.confidence === "low" ? TOKENS.warning : TOKENS.textMuted }),
        h("text", { content: ` ${FACT_STATUS_LABEL[f.status] || f.status}`, fg: FACT_STATUS_COLOR[f.status] || TOKENS.textDim }),
        h("text", { content: ` 支${f.supports ?? 0} 反${f.challenges ?? 0}`, fg: TOKENS.textMuted })
      )
    ),
    h("text", { content: ` ↑↓ 选择 · Enter 一键支持并刷新 · Esc 关闭`, fg: TOKENS.textDim })
  )
}

/** 面板分发器：runtime 只认 kind，路由到具体面板 */
export function ResearchInsightPanel({ kind, data, index = 0, width = 74 }) {
  if (kind === "hypotheses") return h(HypothesesPanel, { hypotheses: data, index, width })
  if (kind === "consensus") return h(ConsensusMeter, { consensus: data, width })
  if (kind === "matrix") return h(EvidenceMatrixPanel, { matrix: data, width })
  if (kind === "experiments") return h(ExperimentTreePanel, { tree: data, width })
  if (kind === "facts") return h(FactsPanel, { facts: data, index, width })
  return h("box", {}, h("text", { content: "（未知面板）", fg: TOKENS.textMuted }))
}

/** 面板行数预算（transcript chromeRows 用）：取内容行数 + 边框/提示 */
export function insightPanelRows(kind, data) {
  if (kind === "hypotheses") return Math.min(8, (data || []).length) + 3
  if (kind === "matrix") return Math.min(8, (data || []).length) * 3 + 3
  if (kind === "experiments") return Math.min(10, flattenTree(data || []).length) + 3
  if (kind === "facts") return Math.min(8, (data || []).length) + 3
  return 5
}
