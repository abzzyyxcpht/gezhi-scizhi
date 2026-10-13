// Slash 补全 + 命令面板纯逻辑测试（无终端）
// 运行: node test/opentui-panels.js
import { fuzzyMatch, completionCandidates, completionWindow } from "../src/opentui/components/slash-suggest.js"
import { paletteCommands } from "../src/command-palette.js"
import { COMMANDS } from "../src/commands.js"
import { windowLines, clampScrollOffset, chromeRows } from "../src/opentui/components/transcript.js"

let failures = 0
const assert = (cond, name) => {
  if (!cond) {
    console.error(`FAIL ${name}`)
    failures++
  }
}

// 模糊匹配（有序子序列）
assert(fuzzyMatch("md", "mode"), "子序列匹配 md→mode")
assert(fuzzyMatch("mdx", "mode") === false, "非子序列不匹配")
assert(fuzzyMatch("", "mode"), "空查询全匹配")

// 补全候选：前缀优先
const c = completionCandidates("/mo", "research")
assert(c.length > 0 && c[0].name.startsWith("mo"), "前缀优先排序")
const c2 = completionCandidates("/", "general")
assert(c2.length <= 8, "候选上限 8")
assert(c2.every((x) => typeof x.desc === "string"), "候选带描述")
// 科研模式 relevance：科研专属命令应排在通用集之前
const researchFirst = completionCandidates("/s", "research")[0]?.name || ""
assert(researchFirst.length > 0, "科研模式候选非空")

// 滑窗
const many = Array.from({ length: 20 }, (_, i) => ({ name: `c${i}` }))
const w0 = completionWindow(many, 0, 6)
assert(w0.start === 0 && w0.rows.length === 6, "滑窗起点")
const w10 = completionWindow(many, 10, 6)
assert(w10.rows.some((r) => r.name === "c10"), "选中项在窗口内")
const w19 = completionWindow(many, 19, 6)
assert(w19.start === 14, "滑窗贴底")

// 命令面板：复用 Ink 版 paletteCommands（中文搜索）
const rows = paletteCommands(COMMANDS, [], "模式", "research")
assert(rows.length > 0 && rows.some((r) => r.name === "mode"), "中文搜索命中 /mode")
const rowsEmpty = paletteCommands(COMMANDS, [], "不存在的命令xyz", "research")
assert(rowsEmpty.length === 0, "无命中返回空")

// 会话区视口裁剪 + 滚轮
const many2 = Array.from({ length: 30 }, (_, i) => ({ text: `L${i}` }))
assert(windowLines(many2, 5).map((l) => l.text).join(",") === "L25,L26,L27,L28,L29", "吸底保留最后 5 行")
assert(windowLines(many2, 5, 3).map((l) => l.text).join(",") === "L22,L23,L24,L25,L26", "offset=3 回看 3 行")
assert(windowLines(many2, 5, 100).map((l) => l.text).join(",") === "L0,L1,L2,L3,L4", "offset 超限贴顶")
assert(windowLines([{ text: "only" }], 5).length === 1, "行数不足高度时全显")
assert(clampScrollOffset(-5, 30, 5) === 0, "滚轮下限钳制")
assert(clampScrollOffset(999, 30, 5) === 25, "滚轮上限钳制")
assert(chromeRows(0) === 9 && chromeRows(11) === 21, "chrome 行数核算")

if (failures === 0) {
  console.error("opentui-panels: ALL_PASS")
  process.exit(0)
}
console.error(`opentui-panels: FAILURES=${failures}`)
process.exit(1)
