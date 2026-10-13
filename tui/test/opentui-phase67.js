// Phase 6-7 测试：bridge 命令子集 + 增量流式 + 宽度缓存性能基准
// 运行: node test/opentui-phase67.js
import { createSessionController } from "../src/opentui/session-controller.js"
import { clipCached } from "../src/opentui/components/transcript.js"
import { renderEvents } from "../src/opentui/session-controller.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}

function fakeBridge() {
  const calls = []
  return {
    calls,
    connection: () => ({ mode: "local" }),
    whenReady: async () => true,
    async call(req) {
      calls.push(req.cmd)
      switch (req.cmd) {
        case "workbench_mode": return { ok: true, mode: req.mode || "research", revision: 2 }
        case "context_status": return { ok: true, status: { used: 13600, total: 68000 } }
        case "session_compact": return { ok: true, result: { changed: true, before_tokens: 1200, after_tokens: 700, removed_messages: 6, model_calls: 0, message: "已保留目标和近期对话" } }
        case "agent_cancel": return { ok: true, result: { cancelled: true } }
        case "agent_continue": return { ok: true, result: { continued: true } }
        case "skills_list": return { ok: true, skills: [{ name: "文献综述", desc: "检索并总结" }, { name: "实验评审", desc: "检查设计", enabled: false }] }
        case "plugins_summary": return { ok: true, plugins: [{ name: "bibtex" }, { name: "term_freq", enabled: false }] }
        case "usage": return { ok: true, usage: { today: 12000 } }
        case "workflows": return { ok: true, workflows: [{ id: "literature_scan", title: "文献扫描" }] }
        case "list": return { ok: true, tasks: [{ id: "t1", status: "done", title: "课题A" }] }
        case "agents": return { ok: true, agents: [{ name: "general", desc: "通用助手" }] }
        case "agent_launch": return { ok: true, result: { session_id: "s9" } }
        case "agent_launch_status": return { ok: true, pending: null }
        case "session_snapshot": return { ok: true, snapshot: { session_id: "s9", status: "done", agent: "research" } }
        case "session_events": {
          if (!req.cursor) return { ok: true, cursor: "k1", events: [{ kind: "assistant_message", payload: { text: "流式增量消息" } }] }
          return { ok: true, cursor: req.cursor, events: [] }
        }
        default: return { ok: true }
      }
    },
  }
}

// ---- 命令子集 ----
const bridge = fakeBridge()
const controller = createSessionController({ bridge, sleep: () => Promise.resolve() })

let r = await controller.submit("/status")
assert(r.lines[0].text.includes("20%") && r.lines[0].text.includes("13600/68000"), "/status 上下文占用")

// /compact 需要会话：先发起一次 agent 拿到 session
r = await controller.submit("跑一个课题")
assert(r.lines.some((l) => l.text.includes("会话 s9")), "agent 提交拿会话")
r = await controller.submit("/compact")
const compactText = r.lines.map((l) => l.text).join("\n")
assert(compactText.includes("上下文已压缩") && compactText.includes("1200 → 700"), "/compact")
assert(bridge.calls.includes("session_compact"), "compact 桥调用")

r = await controller.submit("/cancel")
assert(r.lines[0].text.includes("已取消"), "/cancel")
r = await controller.submit("/continue")
assert(r.lines[0].text.includes("已继续"), "/continue")

r = await controller.submit("/skills")
const skillText = r.lines.map((l) => l.text).join("\n")
assert(skillText.includes("文献综述") && skillText.includes("实验评审") && skillText.includes("正文按需读取"), "/skills 列表（含禁用态与渐进加载说明）")
r = await controller.submit("/plugins")
assert(r.lines.length === 2 && r.lines[0].text.includes("bibtex"), "/plugins 列表")
r = await controller.submit("/usage")
assert(r.lines[0].text.includes("today"), "/usage")
r = await controller.submit("/workflows")
assert(r.lines[0].text.includes("literature_scan"), "/workflows")
r = await controller.submit("/tasks")
assert(r.lines[0].text.includes("t1"), "/tasks")
r = await controller.submit("/agents")
assert(r.lines[0].text.includes("general"), "/agents")

// ---- 增量流式 ----
const bridge2 = fakeBridge()
const streamed = []
const controller2 = createSessionController({ bridge: bridge2, sleep: () => Promise.resolve() })
await controller2.submit("第二个课题", { onUpdate: (rows) => streamed.push(...rows) })
assert(streamed.some((l) => l.text.includes("流式增量消息")), "onUpdate 增量流式回调")
const eventsCalls = bridge2.calls.filter((c) => c === "session_events").length
assert(eventsCalls >= 2, `cursor 分页多次 drain（${eventsCalls} 次）`)

// ---- 宽度缓存性能 ----
const long = "格".repeat(2000)
const t0 = performance.now()
for (let i = 0; i < 2000; i++) clipCached(`${i % 50}-${long.slice(0, 40)}`, 80)
const firstPass = performance.now() - t0
const t1 = performance.now()
for (let i = 0; i < 2000; i++) clipCached(`${i % 50}-${long.slice(0, 40)}`, 80)
const secondPass = performance.now() - t1
assert(secondPass < firstPass * 0.5 + 1, `宽度缓存命中加速（${firstPass.toFixed(1)}ms → ${secondPass.toFixed(1)}ms）`)
assert(clipCached("短文本", 80) === "短文本", "短文本不裁剪")
const clipped = clipCached("x".repeat(200), 20)
assert([...clipped].length <= 20, "长文本裁剪到宽度")

if (failures === 0) {
  console.error("opentui-phase67: ALL_PASS")
  process.exit(0)
}
console.error(`opentui-phase67: FAILURES=${failures}`)
process.exit(1)
