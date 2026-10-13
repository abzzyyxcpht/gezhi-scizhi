// 科研基本步骤（创新项：科研模式的工作流配置层）
// 数据源：workflows/*.yaml 的名称与节点序列（静态兜底 + bridge workflows 动态增强）
import React from "react"
import { TOKENS } from "../theme/tokens.js"
import { SYMBOLS } from "../theme/symbols.js"

const h = React.createElement

/** 科研基本步骤：与 workflows/ 目录一一对应 */
export const RESEARCH_STEPS = [
  {
    id: "literature_scan",
    name: "文献扫描",
    desc: "检索与筛选文献，生成文献池和规范参考文献清单",
    request: (input) => `按文献扫描步骤处理：${input || "当前课题"}`,
    nodes: ["lit_filter", "citation"],
  },
  {
    id: "evidence_review",
    name: "证据评审",
    desc: "人工审核文献池后抽取方法、结果与局限，形成可追溯证据摘要",
    request: (input) => `按证据评审步骤处理：${input || "当前课题"}`,
    nodes: ["lit_filter", "human_review", "extract", "citation"],
  },
  {
    id: "research_pipeline",
    name: "完整流水线",
    desc: "科研辅助全流程自动化（含人工检查点与节点级断点续跑）",
    request: (input) => `按完整科研流水线处理：${input || "当前课题"}`,
    nodes: ["lit_filter", "human_review", "extract", "citation", "report"],
  },
]

/** 步骤选择面板（selection 层，与命令面板同构） */
export function ResearchStepsPanel({ index = 0, onHover, onSelect, width = 74 }) {
  return h(
    "box",
    { flexDirection: "column", border: true, borderStyle: "single", borderColor: TOKENS.modeResearch, width: Math.min(width, 74) },
    h("text", { content: " 科研步骤 · 选择后发起结构化请求", fg: TOKENS.modeResearch }),
    ...RESEARCH_STEPS.map((step, i) =>
      h(
        "box",
        {
          key: step.id,
          flexDirection: "row",
          onMouseOver: () => onHover?.(i),
          onMouseDown: () => onSelect?.(i),
        },
        h("text", { content: i === index ? `${SYMBOLS.pointer} ` : "  ", fg: i === index ? TOKENS.modeResearch : TOKENS.textMuted }),
        h("text", { content: `${i + 1}. ${step.name}`, fg: i === index ? TOKENS.textStrong : TOKENS.text }),
        h("text", { content: `  ${step.desc}`.slice(0, 42), fg: TOKENS.textMuted })
      )
    ),
    h("text", { content: ` ↑↓ navigate · Enter 发起 · Esc 取消 · 节点：${RESEARCH_STEPS.map((s) => s.nodes.length).join("/")}`, fg: TOKENS.textDim })
  )
}
