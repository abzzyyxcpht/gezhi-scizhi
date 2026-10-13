// 科研创新功能帧存档（无 GUI 环境的真渲染证据）：OpenTuiApp + fake bridge 步进各面板状态
// 运行: node src/opentui/spike/capture-research-frames.js
import { mkdirSync, writeFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import React from "react"
import { testRender } from "@mbears/opentui-react/test-utils"
import { OpenTuiApp } from "../runtime.js"
import { createSessionController } from "../session-controller.js"

const here = dirname(fileURLToPath(import.meta.url))
const outDir = join(here, "..", "..", "..", "..", "design-previews", "2026-10-09-research-innovation")
mkdirSync(outDir, { recursive: true })

const HYP = [
  { id: "h1", title: "假设甲：高温提升产率", status: "completed", updated_at: "2026-10-01T00:00:00Z",
    evidence: [{ relation: "supports" }, { relation: "supports" }, { relation: "context" }] },
  { id: "h2", title: "假设乙：压强无关", status: "planned", updated_at: "2026-09-01T00:00:00Z", evidence: [] },
  { id: "h3", title: "假设丙：催化剂有效", status: "failed", updated_at: "2026-10-05T00:00:00Z",
    evidence: [{ relation: "challenges" }, { relation: "challenges" }] },
]
const EXPS = [
  { id: "e1", title: "基线实验：原始配置", status: "completed", kind: "experiment", related_ids: [] },
  { id: "e2", title: "分支A：调参扫描", status: "running", kind: "experiment", related_ids: ["e1"] },
  { id: "e3", title: "分支B：换数据集", status: "failed", kind: "experiment", related_ids: ["e1"] },
]
const FACTS = [
  { id: "wf_1", claim: "退火温度与缺陷检出率正相关", topic: "热轧钢", confidence: "high",
    status: "verified", supports: 2, challenges: 0, source: { id: "s1" } },
  { id: "wf_2", claim: "压强对检出率无显著影响", topic: "热轧钢", confidence: "low",
    status: "challenged", supports: 1, challenges: 3, source: { id: "s2" } },
]

function bridge() {
  return {
    calls: [],
    connection: () => ({ mode: "local" }),
    whenReady: async () => true,
    async call(req) {
      this.calls.push(req.cmd)
      switch (req.cmd) {
        case "workbench_mode": return req.mode ? { ok: true, mode: req.mode, revision: 2 } : { ok: true, mode: "research", revision: 1 }
        case "notebook_list": return req.kind === "hypothesis" ? { ok: true, records: HYP } : { ok: true, records: EXPS }
        case "evidence_brief":
          return { ok: true, brief: { records: [
            { id: "r1", title: "文献一：方法与应用", counts: { supports: 3, challenges: 1, context: 2 }, gaps: ["缺样本量"], followup_draft: "补充对照实验" },
            { id: "r2", title: "文献二：系统综述", counts: { supports: 1, challenges: 0, context: 1 }, gaps: [], followup_draft: "" },
          ] } }
        case "world_fact_list": return { ok: true, facts: FACTS }
        case "agent_launch": return { ok: true, result: { session_id: "s-shot" } }
        case "agent_launch_status": return { ok: true, pending: null }
        case "session_events": return { ok: true, events: [{ kind: "assistant_message", payload: { text: "检测完成" } }] }
        case "session_snapshot": return { ok: true, snapshot: { session_id: "s-shot", status: "done" } }
        default: return { ok: true }
      }
    },
  }
}

const b = bridge()
const controller = createSessionController({ bridge: b, sleep: () => Promise.resolve() })
const setup = await testRender(React.createElement(OpenTuiApp, { reducedMotion: true, controller }), {
  width: 110, height: 28, targetFps: 30,
})

async function stepUntil(pred, label, { maxSteps = 200, settleMs = 12 } = {}) {
  for (let i = 0; i < maxSteps; i++) {
    await React.act(async () => { await new Promise((r) => setTimeout(r, settleMs)) })
    await setup.renderOnce()
    const frame = setup.captureCharFrame()
    if (pred(frame)) return frame
  }
  console.error(`[capture] 超时未命中：${label}`)
  return setup.captureCharFrame()
}

async function run(text) {
  await React.act(async () => { await setup.mockInput.typeText(text) })
  await React.act(async () => { setup.mockInput.pressEnter() })
}

async function shot(name, pred, label) {
  const frame = await stepUntil(pred, label)
  writeFileSync(join(outDir, `${name}.txt`), frame, "utf-8")
  console.error(`[capture] ${name}.txt (${frame.split("\n").length} 行)`)
}

// 0) 科研模式主界面
await shot("00-home", (f) => f.includes("◎") && f.includes("就绪"), "主界面")

// 1) 假设排行榜
await run("/hypotheses")
await shot("01-hypotheses", (f) => f.includes("假设排行榜") && f.includes("假设甲"), "假设排行榜")

// 2) 共识计量条
await React.act(async () => { setup.mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })
await run("/consensus")
await shot("02-consensus", (f) => f.includes("共识计量") && f.includes("支持 4"), "共识计量")

// 3) 证据矩阵
await React.act(async () => { setup.mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })
await run("/matrix")
await shot("03-matrix", (f) => f.includes("证据矩阵") && f.includes("文献一"), "证据矩阵")

// 4) 实验树
await React.act(async () => { setup.mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })
await run("/experiments")
await shot("04-experiments", (f) => f.includes("实验树") && f.includes("基线实验"), "实验树")

// 5) 世界模型
await React.act(async () => { setup.mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })
await run("/facts")
await shot("05-facts", (f) => f.includes("世界模型") && f.includes("退火温度"), "世界模型")

// 6) goal 徽标
await React.act(async () => { setup.mockInput.pressEscape() })
await React.act(async () => { await new Promise((r) => setTimeout(r, 80)) })
await run("/goal 复现论文实验并复现基线结果")
await shot("06-goal", (f) => f.includes("◎ goal") || f.includes("目标已设置"), "goal 徽标")

// 7) novelty 发起态
await run("/novelty 图神经网络异常检测")
await shot("07-novelty", (f) => f.includes("新颖性检测"), "novelty 检测")

console.error(`[capture] 完成 → ${outDir}`)
setup.renderer.destroy()
process.exit(0)
