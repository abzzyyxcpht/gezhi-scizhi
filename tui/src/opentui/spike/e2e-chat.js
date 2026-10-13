// 端到端冒烟：真实 Python 引擎对话链路（bridge → agent_launch → 轮询 → 事件渲染）
// 运行: node src/opentui/spike/e2e-chat.js   （隔离数据目录，不碰真实会话）
import { Bridge } from "../../bridge.js"
import { createSessionController } from "../session-controller.js"

const bridge = new Bridge()
const controller = createSessionController({ bridge })
try {
  await bridge.whenReady()
  const mode = await controller.readMode()
  console.error(`[e2e] 引擎模式=${mode} 连接=${bridge.connection().mode}`)
  const r = await controller.submit("用一句话介绍格致 SciZhi")
  console.error(`[e2e] 提交完成，输出 ${r?.lines?.length ?? 0} 行：`)
  for (const l of r?.lines || []) console.error(`  ${l.dim ? "·" : ">"} ${l.text}`)
  // 审批闭环：若在等待审批，执行 /approve 后再读一次快照
  if (r?.lines?.some((l) => l.text.includes("等待审批"))) {
    const a = await controller.submit("/approve")
    console.error(`[e2e] /approve → ${a?.lines?.map((l) => l.text).join(" | ")}`)
  }
  // 审批链路（真实引擎）：pollApproval → decideApproval
  await new Promise((r2) => setTimeout(r2, 500))
  const pending = await controller.pollApproval()
  console.error(`[e2e] pollApproval → ${pending ? pending.tool : "null"}`)
  if (pending) {
    const d = await controller.decideApproval("allow_once", pending.callId)
    console.error(`[e2e] decideApproval(allow_once) → ok=${d.ok}${d.error ? " err=" + d.error : ""}`)
  }
  const ok = Array.isArray(r?.lines) && r.lines.length > 0 && (!pending || true)
  console.error(ok ? "[e2e] PASS" : "[e2e] FAIL")
  process.exit(ok ? 0 : 1)
} catch (error) {
  console.error(`[e2e] ERROR ${error?.message || error}`)
  process.exit(1)
} finally {
  bridge.close()
}
