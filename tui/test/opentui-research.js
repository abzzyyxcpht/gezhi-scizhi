// 科研创新功能 headless 验证：分析面板纯函数 + controller 新方法 + runtime 集成
// 运行: node test/opentui-research.js
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import {
  rankHypotheses, consensusFromBrief, buildExperimentTree, flattenTree, insightPanelRows,
  HypothesesPanel, ConsensusMeter, EvidenceMatrixPanel, ExperimentTreePanel, FactsPanel,
} from "../src/opentui/components/research-panels.js"
import { renderEvents, createSessionController } from "../src/opentui/session-controller.js"
import { OpenTuiApp } from "../src/opentui/runtime.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}

// ---------- Part 1：纯函数 ----------
const hypRecords = [
  { id: "h1", title: "假设甲：高温提升产率", status: "completed", updated_at: "2026-10-01T00:00:00Z",
    evidence: [{ relation: "supports" }, { relation: "supports" }, { relation: "context" }] },
  { id: "h2", title: "假设乙：压强无关", status: "planned", updated_at: "2026-09-01T00:00:00Z", evidence: [] },
  { id: "h3", title: "假设丙：催化剂有效", status: "failed", updated_at: "2026-10-05T00:00:00Z",
    evidence: [{ relation: "challenges" }, { relation: "challenges" }] },
]
const ranked = rankHypotheses(hypRecords)
assert(ranked.length === 3, "排行榜返回全部假设")
assert(ranked[0].id === "h1" && ranked[1].id === "h2" && ranked[2].id === "h3", "按分数降序：支持×2+背景+状态加成")
assert(ranked[0].evidence.supports === 2 && ranked[2].evidence.challenges === 2, "证据关系计数")
assert(rankHypotheses([]).length === 0, "空记录返回空榜")

const consensus = consensusFromBrief({
  records: [
    { counts: { supports: 3, challenges: 1, context: 2 } },
    { counts: { supports: 1, challenges: 2, context: 0 } },
  ],
})
assert(consensus.yes === 4 && consensus.no === 3 && consensus.mixed === 2 && consensus.total === 9, "共识三段聚合")
assert(Math.abs(consensus.ratio.yes - 4 / 9) < 1e-9, "共识占比")
const emptyConsensus = consensusFromBrief({ records: [] })
assert(emptyConsensus.total === 0 && emptyConsensus.ratio === null, "无证据时占比为 null")
assert(consensusFromBrief(null).total === 0, "null brief 不崩")

const treeRecords = [
  { id: "e1", title: "基线实验", status: "completed", kind: "experiment", related_ids: [] },
  { id: "e2", title: "分支A：调参", status: "running", kind: "experiment", related_ids: ["e1"] },
  { id: "e3", title: "分支B：换数据", status: "failed", kind: "experiment", related_ids: ["e1", "e2"] },
  { id: "e4", title: "假设H", status: "planned", kind: "hypothesis", related_ids: [] },
  // 环：e5↔e6 互引，必须各渲染一次不死循环
  { id: "e5", title: "环1", status: "planned", kind: "experiment", related_ids: ["e6"] },
  { id: "e6", title: "环2", status: "planned", kind: "experiment", related_ids: ["e5"] },
]
const tree = buildExperimentTree(treeRecords)
const flat = flattenTree(tree)
assert(flat.some((r) => r.node.id === "e1"), "森林含根 e1")
assert(flat.some((r) => r.node.id === "e4"), "假设记录也入树")
assert(flat.filter((r) => r.node.id === "e5").length === 1 && flat.filter((r) => r.node.id === "e6").length === 1, "环防御：互引记录各渲染一次")
assert(flat.some((r) => r.text.includes("└")), "子节点带枝形符号")
assert(buildExperimentTree([]).length === 0, "空记录返回空树")
assert(insightPanelRows("matrix", [1, 2, 3]) === 12 && insightPanelRows("hypotheses", [1]) === 4, "面板行数预算")

// ---------- Part 2：controller（fake bridge） ----------
function researchBridge() {
  const calls = []
  let polls = 0
  return {
    calls,
    connection: () => ({ mode: "local" }),
    whenReady: async () => true,
    async call(req) {
      calls.push(req.cmd)
      switch (req.cmd) {
        case "notebook_list":
          return req.kind === "hypothesis"
            ? { ok: true, records: hypRecords }
            : { ok: true, records: treeRecords }
        case "workflow_run":
          return { ok: true, task_id: "repro_001", workflow_id: req.workflow_id || "reproduce" }
        case "peer_review":
          return { ok: true, record_id: req.id, kind: "hypothesis", status: "completed",
            dimensions: { structure: 5, evidence: 4, testability: 5, closure: 5, depth: 2 },
            overall: 4.2, verdict: "结构良好", suggestions: ["追加观察记录"], boundary: "启发式评分" }
        case "world_fact_list":
          return { ok: true, facts: [
            { id: "wf_1", claim: "温度提升产率", topic: "T", confidence: "high", status: "verified", supports: 2, challenges: 0, source: { id: "u1" } },
            { id: "wf_2", claim: "压强无关", topic: "T", confidence: "low", status: "challenged", supports: 1, challenges: 3, source: { id: "u2" } },
          ] }
        case "world_fact_support":
        case "world_fact_challenge":
          return { ok: true, fact: { id: req.id, supports: 3, challenges: 0, status: "verified" } }
        case "world_fact_save":
          return { ok: true, fact: { id: "wf_new" }, created: true }
        case "world_fact_delete":
          return { ok: true, deleted: true }
        case "evidence_brief":
          return { ok: true, brief: { records: [
            { id: "r1", title: "文献一", counts: { supports: 2, challenges: 1, context: 3 }, gaps: ["缺样本量"], followup_draft: "补充对照实验" },
            { id: "r2", title: "文献二", counts: { supports: 0, challenges: 0, context: 1 }, gaps: [], followup_draft: "" },
          ] } }
        case "agent_launch":
        case "agent_run":
          return { ok: true, result: { session_id: "s-77", agent: "research" } }
        case "agent_launch_status":
          polls += 1
          return { ok: true, pending: polls <= 1 ? { id: "l1" } : null }
        case "session_events":
          return { ok: true, events: [{ kind: "assistant_message", payload: { text: "检测完成" } }] }
        case "session_snapshot":
          return { ok: true, snapshot: { session_id: "s-77", status: "done" } }
        default:
          return { ok: true }
      }
    },
  }
}

const bridge = researchBridge()
const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() })

const hyps = await ctrl.hypotheses()
assert(hyps.length === 3 && hyps[0].id === "h1", "controller.hypotheses 拉取并排序")
assert(bridge.calls.includes("notebook_list"), "hypotheses 走 notebook_list")

const cons = await ctrl.consensus()
assert(cons && cons.total === 7, "controller.consensus 聚合（2+1+3+0+0+1）")

const mat = await ctrl.matrix()
assert(mat.length === 2 && mat[0].supports === 2 && mat[0].gaps[0] === "缺样本量" && mat[0].followup === "补充对照实验", "controller.matrix 字段映射")

const expTree = await ctrl.experimentTree()
assert(expTree.length >= 2, "controller.experimentTree 返回森林")
const factRows = await ctrl.facts()
assert(factRows.length === 2 && factRows[0].supports === 2 && factRows[1].status === "challenged", "controller.facts 拉取世界模型")
const vote = await ctrl.factVote("wf_1", "support")
assert(vote.ok, "controller.factVote 支持投票")
assert(bridge.calls.includes("world_fact_support"), "投票走 world_fact_support")
const repro = await ctrl.submit("/reproduce 热轧钢缺陷检测复现")
assert((repro.lines || []).some((l) => String(l.text).includes("repro_001")), "/reproduce 启动复现流水线")
assert(bridge.calls.includes("workflow_run"), "reproduce 走 workflow_run")
const noRepro = await ctrl.submit("/reproduce")
assert((noRepro.lines || []).some((l) => String(l.text).includes("用法")), "/reproduce 空参给用法")
const review = await ctrl.submit("/peer-review nb_1")
assert((review.lines || []).some((l) => String(l.text).includes("总评 4.2")), "/peer-review 五维评分渲染")
assert((review.lines || []).some((l) => String(l.text).includes("追加观察记录")), "/peer-review 建议渲染")
assert((review.lines || []).some((l) => String(l.text).includes("启发式评分")), "/peer-review 边界声明")

// 引用角标（Kosmos 强绑定对标）
const cited = renderEvents([
  { kind: "assistant_message", payload: { text: "结论：X 成立", citations: [
    { title: "文献A", doi: "10.1/a" }, { title: "文献B", url: "https://x.cn/b" },
  ] } },
])
assert(cited.some((l) => l.kind === "citation" && l.text.includes("文献A") && l.text.includes("10.1/a")), "引用角标渲染（题名+定位符）")
assert(cited.filter((l) => l.kind === "citation").length === 2, "两条引用两行角标")
assert(renderEvents([{ kind: "assistant_message", payload: { text: "无引用" } }]).every((l) => l.kind !== "citation"), "无 citations 不渲染角标")

// goal：注入与清除
ctrl.setGoal("复现论文实验")
assert(ctrl.goal === "复现论文实验", "setGoal 生效")
const r1 = await ctrl.submit("帮我跑一下")
const launchCall = bridge.calls.length > 0
assert(launchCall, "submit 走 bridge")
ctrl.clearGoal()
assert(ctrl.goal === "", "clearGoal 生效")

// novelty：走 agent 回合，request 含四段式提示与课题
bridge.calls.length = 0
const r2 = await ctrl.novelty("图神经网络异常检测")
assert(bridge.calls.includes("agent_launch"), "novelty 走 agent_launch")
assert((r2.lines || []).some((l) => String(l.text).includes("会话 s-77")), "novelty 返回会话摘要")
const noTopic = await ctrl.novelty("")
assert((noTopic.lines || []).some((l) => String(l.text).includes("用法")), "novelty 空课题给用法")

// 面板组件冒烟（React headless 各渲染一次不崩）
async function renderOnce(el, w = 90, h = 12) {
  const s = await testRender(el, { width: w, height: h, targetFps: 30 })
  await s.renderOnce()
  const f = s.captureCharFrame()
  s.renderer.destroy()
  return f
}
let f = await renderOnce(React.createElement(HypothesesPanel, { hypotheses: ranked, index: 0 }))
assert(f.includes("假设排行榜") && f.includes("假设甲"), "假设排行榜面板渲染")
f = await renderOnce(React.createElement(ConsensusMeter, { consensus }))
assert(f.includes("共识计量") && f.includes("支持 4"), "共识计量条渲染")
f = await renderOnce(React.createElement(EvidenceMatrixPanel, { matrix: mat }))
assert(f.includes("证据矩阵") && f.includes("文献一") && f.includes("待补"), "证据矩阵渲染")
f = await renderOnce(React.createElement(ExperimentTreePanel, { tree: buildExperimentTree(treeRecords.slice(0, 3)) }))
assert(f.includes("实验树") && f.includes("基线实验"), "实验树渲染")
f = await renderOnce(React.createElement(FactsPanel, { facts: factRows, index: 0 }))
assert(f.includes("世界模型") && f.includes("温度提升产率") && f.includes("已核验"), "世界模型面板渲染")
f = await renderOnce(React.createElement(FactsPanel, { facts: [], index: 0 }))
assert(f.includes("暂无事实卡片"), "世界模型空态")

// 退出前沉降：让 fake bridge 轮询链与 renderer 句柄自然关闭（Windows libuv 断言防护）
await new Promise((r) => setTimeout(r, 150))
if (failures === 0) {
  console.error("opentui-research: ALL_PASS")
  process.exit(0)
}
console.error(`opentui-research: FAILURES=${failures}`)
process.exit(1)
