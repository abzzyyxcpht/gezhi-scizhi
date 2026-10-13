// 格致 SciZhi 路演 PPT（15 页精简版）生成器
// 设计取向（去 AI 感）：白底 + 单一强调色 + 细描线；版式由文字与留白组织，
// 不用卡片网格、不用彩色标签、不用大面积色块；技术内容用浅灰代码块（等宽）承载。
// 版式刻度：页边距 0.75"，正文 12.5pt / 1.35 行距，页标题 25pt，页脚 8.5pt。
const path = require("path")
const fs = require("fs")
let pptxgen
try {
  pptxgen = require("pptxgenjs")
} catch {
  const globalRoot = require("child_process").execSync("npm root -g").toString().trim()
  pptxgen = require(path.join(globalRoot, "pptxgenjs"))
}

const FONT = "Microsoft YaHei"
const MONO = "Consolas"
const ROOT = path.join(__dirname, "..")
const ASSET = path.join(ROOT, "assets")
const DEMO_ROOT = path.join(__dirname, "真实演示截图")
const GITHUB_URL = "https://github.com/abzzyyxcpht/gezhi-scizhi"
const C = {
  ink: "1A1D21",
  ink2: "3C4450",
  muted: "6B7480",
  faint: "98A1AC",
  hair: "E4E7EB",
  hairStrong: "CFD5DB",
  page: "FFFFFF",
  code: "F7F8FA",
  accent: "0B57D0",
  ok: "157347",
}
const W = 13.333
const H = 7.5
const M = 0.75
const CW = W - M * 2

let pres

function latestDemoFile(name) {
  if (!fs.existsSync(DEMO_ROOT)) return null
  const dirs = fs.readdirSync(DEMO_ROOT, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
  if (!dirs.length) return null
  const candidate = path.join(DEMO_ROOT, dirs[dirs.length - 1], name)
  return fs.existsSync(candidate) ? candidate : null
}

const DEMO_HOME = latestDemoFile("1440x900-home.png")
const DEMO_AGENT = latestDemoFile("1440x900-agent.png")
const DEMO_MONITOR = latestDemoFile("1440x900-monitor.png")
const DEMO_RESULTS = latestDemoFile("1440x900-results.png")

function imageFrame(s, { path: imagePath, x, y, w, h, label, caption, dark = false }) {
  if (!imagePath || !fs.existsSync(imagePath)) return false
  s.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h,
    fill: { color: dark ? "151719" : "F7F8FA" },
    line: { color: dark ? "2C3138" : C.hairStrong, width: 0.8 },
  })
  s.addImage({ path: imagePath, x: x + 0.06, y: y + 0.06, w: w - 0.12, h: h - 0.12 })
  if (label) s.addText(label, { x: x + 0.14, y: y + 0.12, w: w - 0.28, h: 0.22, fontFace: FONT, fontSize: 8.5, color: dark ? "FFFFFF" : C.ink, bold: true, margin: 0 })
  if (caption) s.addText(caption, { x, y: y + h + 0.08, w, h: 0.25, fontFace: FONT, fontSize: 8.5, color: C.muted, margin: 0 })
  return true
}

function statCard(s, { x, y, w, h = 0.98, value, label, accent = C.accent }) {
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: C.code }, line: { color: C.hair, width: 0.8 } })
  s.addText(value, { x: x + 0.18, y: y + 0.12, w: w - 0.36, h: 0.38, fontFace: FONT, fontSize: 24, bold: true, color: accent, margin: 0 })
  s.addText(label, { x: x + 0.18, y: y + 0.56, w: w - 0.36, h: 0.24, fontFace: FONT, fontSize: 9.5, color: C.muted, margin: 0 })
}

function arrow(s, { x, y, w, color = C.hairStrong }) {
  s.addShape(pres.shapes.LINE, { x, y, w, h: 0, line: { color, width: 1.2, beginArrowType: "none", endArrowType: "triangle" } })
}

function archBox(s, { x, y, w, h, title, body, accent = C.accent, fill = C.page }) {
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { color: C.hairStrong, width: 0.8 } })
  s.addShape(pres.shapes.RECTANGLE, { x, y, w: 0.08, h, fill: { color: accent }, line: { color: accent, width: 0 } })
  s.addText(title, { x: x + 0.22, y: y + 0.14, w: w - 0.35, h: 0.25, fontFace: FONT, fontSize: 12.5, bold: true, color: C.ink, margin: 0 })
  s.addText(body, { x: x + 0.22, y: y + 0.47, w: w - 0.35, h: h - 0.58, fontFace: FONT, fontSize: 9.5, color: C.ink2, margin: 0, valign: "top", lineSpacingMultiple: 1.18 })
}

function newDeck() {
  pres = new pptxgen()
  pres.layout = "LAYOUT_WIDE"
  pres.author = "格致 SciZhi 项目组"
  pres.title = "格致 SciZhi · 科研智能体工作台"
  pres.subject = "中国国际大学生创新大赛（2026）产业赛道路演"
}

/** 页头：kicker + 标题 + 细描线；返回 { s, top } */
function page(title, { kicker, page: pageNo } = {}) {
  const s = pres.addSlide()
  s.background = { color: C.page }
  let y = 0.5
  if (kicker) {
    s.addText(kicker, { x: M, y, w: CW, h: 0.26, fontFace: FONT, fontSize: 10.5, color: C.accent, charSpacing: 1.2, margin: 0 })
    y += 0.28
  }
  s.addText(title, { x: M, y, w: CW - 1.2, h: 0.62, fontFace: FONT, fontSize: 25, bold: true, color: C.ink, margin: 0 })
  s.addShape(pres.shapes.LINE, { x: M, y: y + 0.72, w: CW, h: 0, line: { color: C.hairStrong, width: 1 } })
  if (pageNo) {
    s.addText(String(pageNo), { x: W - M - 0.8, y: 0.54, w: 0.8, h: 0.3, fontFace: MONO, fontSize: 10.5, color: C.faint, align: "right", margin: 0 })
  }
  return { s, top: y + 0.98 }
}

/** 页脚（内容页统一） */
function footer(s, left = "格致 SciZhi · 科研智能体工作台") {
  s.addShape(pres.shapes.LINE, { x: M, y: H - 0.62, w: CW, h: 0, line: { color: C.hair, width: 0.75 } })
  s.addText(left, { x: M, y: H - 0.56, w: CW - 1, h: 0.28, fontFace: FONT, fontSize: 8.5, color: C.faint, margin: 0 })
}

/** 段落块：label（加粗）+ 正文（字符串或行数组）；返回下一段 y */
function block(s, { x, y, w, label, body, size = 12.5, labelColor = C.ink, bodyColor = C.ink2, gap = 0.16 }) {
  let cy = y
  if (label) {
    s.addText(label, { x, y: cy, w, h: 0.3, fontFace: FONT, fontSize: size + 1, bold: true, color: labelColor, margin: 0 })
    cy += 0.32
  }
  const lines = Array.isArray(body) ? body : [body]
  // CJK 视宽 ≈ 1 字宽；每行可容纳 width / (size/72) 个字符；据此估算折行数
  const charW = size / 72
  const wrapped = lines.reduce((n, t) => n + Math.max(1, Math.ceil(String(t).length / Math.max(4, w / charW))), 0)
  const h = Math.max(0.3, wrapped * charW * 1.45)
  s.addText(
    lines.map((t, i) => ({ text: t, options: { breakLine: i < lines.length - 1 } })),
    { x, y: cy, w, h, fontFace: FONT, fontSize: size, color: bodyColor, margin: 0, lineSpacingMultiple: 1.35, valign: "top" }
  )
  return cy + h + gap
}

/** 浅灰代码块（真实命令与输出；等宽小字） */
function code(s, { x, y, w, lines, size = 9.5, label }) {
  const h = lines.length * ((size * 1.55) / 72) + 0.28
  if (label) s.addText(label, { x, y: y - 0.28, w, h: 0.24, fontFace: FONT, fontSize: 9, color: C.muted, margin: 0 })
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: C.code }, line: { color: C.hair, width: 0.75 } })
  s.addText(
    lines.map((l, i) => ({ text: typeof l === "string" ? l : l.text, options: { breakLine: i < lines.length - 1, color: typeof l === "string" ? C.ink2 : (l.color || C.ink2) } })),
    { x: x + 0.16, y: y + 0.12, w: w - 0.32, h: h - 0.24, fontFace: MONO, fontSize: size, color: C.ink2, margin: 0, lineSpacingMultiple: 1.3, valign: "top" }
  )
  return y + h
}

/** 表：细线 + 浅灰表头，无斑马纹、无阴影 */
function table(s, { x, y, w, rows, colW, size = 11, rowH = 0.42, highlightLast = false }) {
  const data = rows.map((r, ri) => r.map((cell, ci) => {
    const emphasized = highlightLast && ci === r.length - 1
    return {
    text: cell,
    options: {
      fontFace: FONT, fontSize: size,
      bold: ri === 0 || emphasized,
      color: ri === 0 ? (emphasized ? "FFFFFF" : C.ink) : (emphasized ? "286557" : C.ink2),
      fill: { color: ri === 0 ? (emphasized ? "286557" : C.code) : (emphasized ? "F4FAF7" : C.page) },
      valign: "middle", margin: 0.06,
    },
  }}))
  s.addTable(data, { x, y, w, colW, rowH, border: { pt: 0.5, color: C.hair }, valign: "middle", align: "left" })
  return y + rows.length * rowH
}

/** 小标题：句首方点 + 加粗（替代彩色标签） */
function sub(s, { x, y, w, text, size = 13.5 }) {
  s.addShape(pres.shapes.RECTANGLE, { x, y: y + 0.07, w: 0.09, h: 0.16, fill: { color: C.accent }, line: { color: C.accent, width: 0 } })
  s.addText(text, { x: x + 0.2, y, w: w - 0.2, h: 0.32, fontFace: FONT, fontSize: size, bold: true, color: C.ink, margin: 0 })
  return y + 0.4
}

// ============================================================ P1 封面
function cover() {
  const s = pres.addSlide()
  s.background = { color: C.page }
  s.addImage({ path: path.join(ASSET, "gezhi.png"), x: W - M - 0.72, y: 0.52, w: 0.52, h: 0.52 })
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 1.3, w: 0.1, h: 1.6, fill: { color: C.accent }, line: { color: C.accent, width: 0 } })
  s.addText("格致 SciZhi", { x: M + 0.35, y: 1.28, w: 9, h: 1.0, fontFace: FONT, fontSize: 44, bold: true, color: C.ink, margin: 0 })
  s.addText("面向科研过程的 AI 智能体工作台", { x: M + 0.35, y: 2.28, w: 9.6, h: 0.5, fontFace: FONT, fontSize: 18, color: C.ink2, margin: 0 })
  s.addShape(pres.shapes.LINE, { x: M + 0.35, y: 3.05, w: 6.4, h: 0, line: { color: C.hairStrong, width: 1 } })
  s.addText("把检索、抽取、综合、审查、归档连成一条可追溯、可干预、可复现的流水线。", { x: M + 0.35, y: 3.22, w: 7.6, h: 0.5, fontFace: FONT, fontSize: 13, color: C.muted, margin: 0 })
  s.addText("科研智能体 · 研究者可控的证据流水线", { x: M, y: 5.78, w: 7.6, h: 0.35, fontFace: FONT, fontSize: 12.5, color: C.accent, bold: true, margin: 0 })
  s.addText("0.9.0 本地候选 · Apache-2.0 开源许可 · Windows 安装包已构建", { x: M, y: 6.2, w: 9.6, h: 0.34, fontFace: FONT, fontSize: 10.5, color: C.muted, margin: 0 })
  if (fs.existsSync(path.join(ASSET, "research-study.svg"))) {
    s.addImage({ path: path.join(ASSET, "research-study.svg"), x: W - M - 3.55, y: 2.0, w: 3.2, h: 1.88, transparency: 2 })
  }
  s.addText("答辩演示：终端版 npm start，全程离线可复现", { x: W - M - 4.8, y: 5.9, w: 4.8, h: 0.4, fontFace: FONT, fontSize: 10, color: C.faint, align: "right", margin: 0 })
  return s
}

// ============================================================ P2 项目概览
function overview() {
  const { s, top } = page("项目概览", { kicker: "壹 · 是什么", page: 2 })
  let y = block(s, { x: M, y: top, w: 6.75, label: "一句话", body: "格致把科研中最耗时间的几步——找资料、读证据、写综述、做复核、留档——串成一条可以暂停、可以追问、可以复跑的工作流。", size: 13.5 })
  y = block(s, { x: M, y: y + 0.12, w: 6.75, label: "谁会用", body: [
    "· 研究生与课题组：把一次综述从“反复翻找”变成“按站点推进”",
    "· 企业研发与情报团队：数据在本机，过程有权限，结果可交接",
  ], size: 11.5 })
  block(s, { x: M, y: y + 0.08, w: 6.75, label: "共同底座", body: "Python 智能体引擎 + JSON Lines 桥协议；终端、桌面和手机远控共享同一份研究档案。", size: 11.5 })
  statCard(s, { x: M, y: 5.18, w: 2.08, value: "40+ h", label: "传统综述事务性耗时（样例）" })
  statCard(s, { x: M + 2.28, y: 5.18, w: 2.08, value: "7 站", label: "从立项到归档的科研流水线", accent: "286557" })
  statCard(s, { x: M + 4.56, y: 5.18, w: 2.08, value: "1 份", label: "可带走的研究包与证据账本" })
  imageFrame(s, { path: DEMO_HOME, x: 8.0, y: top - 0.02, w: 4.58, h: 3.05, label: "真机 · 新任务", caption: "原生桌面端：离线模式、研究方向、最近研究" , dark: true })
  table(s, { x: 8.0, y: 4.4, w: 4.58, rows: [
    ["形态", "入口", "适合场景"],
    ["终端工作台", "npm start", "快速推进"],
    ["原生桌面", "安装包 / 源码", "长时间研究"],
    ["手机远控", "局域网 Web", "看进度 / 审批"],
  ], colW: [1.35, 1.25, 1.98], size: 9.5, rowH: 0.42 })
  footer(s)
  return s
}

// ============================================================ P3 痛点
function pain() {
  const { s, top } = page("痛点：科研工作流断在四个地方", { kicker: "壹 · 为什么做", page: 3 })
  const items = [
    ["检索碎裂", "OpenAlex、CrossRef、PDF、网页分别翻找，去重与引用追踪全靠手工。一次综述平均 40+ 小时，其中约七成是事务性劳动；检索式不留档，下一次从零开始。"],
    ["证据失锚", "结论散落在不同 PDF 页码与笔记里，引用与正文对不上。抽查一遍就露馅，投稿与答辩被追问「这条出自哪」时，只能回头人工核对。"],
    ["过程不可复现", "三个月后复跑同一课题：检索式没存、轮次没记、池子没有版本。答不出「上次为什么筛掉那篇」，人一毕业即断代，经验无法沉淀。"],
    ["合规成本高", "AI 生成内容没有标识，校/企合规红线过不去；模型密钥散落在各人的配置文件里；外部工具越权写入，出事查不到责任人。"],
  ]
  let y = top + 0.1
  items.forEach(([t, d], i) => {
    y = sub(s, { x: M, y, w: CW, text: t })
    y = block(s, { x: M + 0.2, y, w: CW - 0.2, body: d, size: 12.5, gap: 0.3 })
    if (i < items.length - 1) s.addShape(pres.shapes.LINE, { x: M, y: y - 0.14, w: CW, h: 0, line: { color: C.hair, width: 0.75 } })
  })
  s.addText("缺口：缺少一个把五步串起来、且每一步都可追溯、可干预的科研工作台。", { x: M, y: H - 1.28, w: CW, h: 0.4, fontFace: FONT, fontSize: 13, bold: true, color: C.accent, margin: 0 })
  footer(s)
  return s
}

// ============================================================ P4 形态与架构
function architecture() {
  const { s, top } = page("产品形态与技术架构", { kicker: "贰 · 怎么做", page: 4 })
  const y = top + 0.1
  const gap = 0.22
  const boxW = (CW - gap * 3) / 4
  archBox(s, { x: M, y, w: boxW, h: 2.18, title: "界面层", body: "终端 TUI\n桌面 Flet\n手机远控 Web\n\n研究者看到的是同一套命令与状态。", accent: C.accent, fill: "F7F9FC" })
  arrow(s, { x: M + boxW + 0.03, y: y + 1.09, w: gap - 0.05, color: C.accent })
  archBox(s, { x: M + boxW + gap, y, w: boxW, h: 2.18, title: "桥接层", body: "src/bridge.py\nJSON Lines\n命令合约 + revision\n\n界面不直接碰业务逻辑。", accent: "286557", fill: "F4FAF7" })
  arrow(s, { x: M + (boxW + gap) * 2 - 0.02, y: y + 1.09, w: gap - 0.05, color: "286557" })
  archBox(s, { x: M + (boxW + gap) * 2, y, w: boxW, h: 2.18, title: "智能体引擎", body: "Plan / Agent / Review\nCheckpoint + HITL\n检索、抽取、综述、审查\n\n可无头运行、可单测。", accent: C.accent, fill: "F7F9FC" })
  arrow(s, { x: M + (boxW + gap) * 3 - 0.02, y: y + 1.09, w: gap - 0.05, color: C.accent })
  archBox(s, { x: M + (boxW + gap) * 3, y, w: boxW, h: 2.18, title: "数据与生态", body: "插件 / Skill / GEZHI.md\nMCP（stdio + HTTP）\ngit 研究档案 + 用量日志\n\n产物带 manifest 与校验和。", accent: "286557", fill: "F4FAF7" })

  s.addShape(pres.shapes.RECTANGLE, { x: M, y: top + 2.72, w: CW, h: 1.18, fill: { color: C.code }, line: { color: C.hair, width: 0.8 } })
  s.addText("一条边界原则", { x: M + 0.22, y: top + 2.9, w: 1.6, h: 0.24, fontFace: FONT, fontSize: 12, bold: true, color: C.accent, margin: 0 })
  s.addText("界面只发命令，引擎只产事件；每个事件都有权限、输入、输出和 revision。这样终端、桌面、手机可以换，证据链不丢。", { x: M + 1.75, y: top + 2.9, w: CW - 1.98, h: 0.5, fontFace: FONT, fontSize: 11.5, color: C.ink2, margin: 0, lineSpacingMultiple: 1.3 })
  s.addText("隔离验证：GEZHI_DATA_DIR 指向临时目录，回归测试不触碰真实研究数据。", { x: M + 0.22, y: top + 3.53, w: CW - 0.44, h: 0.24, fontFace: FONT, fontSize: 10.5, color: C.muted, margin: 0 })
  code(s, { x: M, y: top + 4.28, w: CW, lines: [
    "UI → bridge.py → agent runtime → research package",
    "          JSONL events · permission · checkpoint · manifest · sha256",
  ], size: 10, label: "从点击到归档：一条可观测的事件链" })
  footer(s)
  return s
}

// ============================================================ P5 科研流水线
function pipeline() {
  const { s, top } = page("科研流水线：七站，每一站都可回看", { kicker: "贰 · 怎么做", page: 5 })
  const stages = [
    ["立项", "研究方向与关键词，目标常驻上下文"],
    ["假设", "可证伪假设进假设台，证据加权排序"],
    ["检索", "双源并发召回 + 引用滚雪球递归"],
    ["提取", "结构化抽取，结论绑定文献出处"],
    ["综合", "综述框架 plan→act→reflect，缺证据自动补检"],
    ["审查", "AI 审稿人五维评分 + 后台证据复核"],
    ["归档", "研究包：manifest + 产物 + 数据 SHA256"],
  ]
  const y0 = top + 0.38
  const inset = 0.62
  const span = 5.25
  const gap = span / (stages.length - 1)
  s.addShape(pres.shapes.LINE, { x: M + inset, y: y0, w: span, h: 0, line: { color: C.hairStrong, width: 1.25 } })
  stages.forEach(([name, d], i) => {
    const x = M + inset + i * gap
    s.addShape(pres.shapes.OVAL, { x: x - 0.055, y: y0 - 0.055, w: 0.11, h: 0.11, fill: { color: i < 5 ? C.accent : C.hairStrong }, line: { color: i < 5 ? C.accent : C.hairStrong, width: 0 } })
    s.addText(name, { x: x - 0.5, y: y0 + 0.18, w: 1.0, h: 0.3, fontFace: FONT, fontSize: 12, bold: true, color: C.ink, align: "center", margin: 0 })
    s.addText(d, { x: x - 0.44, y: y0 + 0.5, w: 0.88, h: 1.15, fontFace: FONT, fontSize: 8.5, color: C.muted, align: "center", valign: "top", margin: 0, lineSpacingMultiple: 1.15 })
  })
  imageFrame(s, { path: DEMO_MONITOR, x: 7.25, y: top + 0.06, w: 5.33, h: 2.85, label: "真机 · 研究进度", caption: "任务完成度、五站状态和研究详情同屏可见", dark: true })
  code(s, {
    x: M, y: top + 2.72, w: 6.55, lines: [
      "❯ /timeline                         ❯ /status",
      "  ● 立项目标  目标：大模型智能体综述       task=llm-agents-review",
      "  ● 证据池    15 条 · 支持 7 · 背景 6        节点 4/5 · 80%",
      "  ○ 世界事实  用 /fact add <论断> 录入       ⏸ 已挂起 · ▶ /resume",
    ], size: 9.5, label: "终端里看到的两条实时视图：阶段灯轨 + 任务监控" })
  block(s, { x: 7.25, y: top + 3.28, w: 5.33, size: 10.8, bodyColor: C.muted,
    body: "Checkpoint 不是“保存一下”：暂停、续跑、取消、重跑、删除都有明确语义；人工关口用编号菜单完成决策，并把选择写回事件流。" })
  footer(s)
  return s
}

// ============================================================ P6 F1
function f1() {
  const { s, top } = page("文献猎手：双源检索 + 三级漏斗", { kicker: "叁 · 核心功能", page: 6 })
  block(s, {
    x: M, y: top, w: 6.2, label: "召回：两个源并行，一条链递归", body: [
      "· OpenAlex 与 CrossRef 并发检索，DOI 去重，单源失败自动降级",
      "· 引用滚雪球：正向被引 + 反向参考文献双侧扩展",
      "· 收敛判断：新增低于阈值即停止加深，不做无限递归",
      "· 去重键：DOI 优先，缺失时回退「标题 + 年份」模糊匹配",
      "· 轮次级断点存档，中断后从 round-N 续跑",
    ], size: 11.2,
  })
  block(s, {
    x: 7.05, y: top, w: 5.53, label: "排序：两级粗排 + LLM 终审", body: [
      "· 默认 0.6×TF-IDF 余弦 + 0.4×饱和 BM25（纯 Python Okapi）",
      "· 配置 Embedding 端点后升级为混合评分，失败自动降级",
      "· 离线行为逐比特不变，评测脚本可对照两种模式",
      "· LLM 语义终审保留否决权；否决后不足 5 篇则放弃否决，防空池",
      "· 每级通过数写入产物 funnel 字段，可展示、可回放",
    ], size: 11.2,
  })
  s.addText("结果视图 · 证据图谱", { x: M + 0.15, y: top + 2.12, w: 2.6, h: 0.22, fontFace: FONT, fontSize: 9.5, color: C.muted, bold: true, margin: 0 })
  imageFrame(s, { path: path.join(ROOT, "data", "artifacts", "graph", "evidence_c41ff9.png"), x: M, y: top + 2.35, w: 6.25, h: 2.55, caption: "节点大小与颜色表达相关度；右侧列表保留出处" })
  s.addShape(pres.shapes.RECTANGLE, { x: 7.05, y: top + 2.35, w: 5.53, h: 2.55, fill: { color: C.code }, line: { color: C.hair, width: 0.8 } })
  s.addText("一次离线样例的漏斗", { x: 7.32, y: top + 2.6, w: 3.2, h: 0.26, fontFace: FONT, fontSize: 12, bold: true, color: C.ink, margin: 0 })
  const funnel = [["召回", "218", 1.0], ["去重", "176", 0.81], ["粗排", "64", 0.29], ["精排", "42", 0.19], ["入池", "42", 0.19]]
  funnel.forEach(([name, value, ratio], i) => {
    const yy = top + 3.1 + i * 0.36
    s.addText(name, { x: 7.32, y: yy, w: 0.7, h: 0.22, fontFace: FONT, fontSize: 9.5, color: C.muted, margin: 0 })
    s.addShape(pres.shapes.RECTANGLE, { x: 8.03, y: yy + 0.04, w: 2.7 * ratio, h: 0.16, fill: { color: i < 2 ? C.accent : "7CA6D8" }, line: { color: i < 2 ? C.accent : "7CA6D8", width: 0 } })
    s.addText(value, { x: 10.95, y: yy - 0.01, w: 0.55, h: 0.22, fontFace: MONO, fontSize: 10, bold: true, color: C.ink, margin: 0 })
  })
  s.addText("每一级通过数写入 funnel 字段，检索结束后还能回放“为什么留下这 42 篇”。", { x: 7.32, y: top + 4.65, w: 4.86, h: 0.42, fontFace: FONT, fontSize: 10, color: C.muted, margin: 0, lineSpacingMultiple: 1.15 })
  footer(s)
  return s
}

// ============================================================ P7 F2-F5
function f2345() {
  const { s, top } = page("综述搭子、数据速览、格式管家、暂停键", { kicker: "叁 · 核心功能", page: 7 })
  const items = [
    ["综述搭子", "先搭框架，再补证据；引用逐条回池核对，缺证据章节自动触发一次补检。"],
    ["数据速览", "/stats 一次给出描述统计、IQR 异常值、相关性和图表建议，结果跟着研究包走。"],
    ["格式管家", "GB/T 7714—2015 与 APA 7 一键切换；元数据缺失就留空，不用模型“猜”。"],
    ["暂停键", "暂停、续跑、取消、重跑、删除五种语义写进事件流，人工确认后才继续。"],
  ]
  const gap = 0.26
  const cardW = (CW - gap) / 2
  items.forEach(([t, d], i) => {
    const col = i % 2
    const row = Math.floor(i / 2)
    const x = M + col * (cardW + gap)
    const y = top + 0.12 + row * 1.42
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: cardW, h: 1.18, fill: { color: i % 2 ? "F4FAF7" : C.code }, line: { color: C.hair, width: 0.8 } })
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: 0.08, h: 1.18, fill: { color: i % 2 ? "286557" : C.accent }, line: { color: i % 2 ? "286557" : C.accent, width: 0 } })
    s.addText(t, { x: x + 0.22, y: y + 0.18, w: cardW - 0.35, h: 0.26, fontFace: FONT, fontSize: 13, bold: true, color: C.ink, margin: 0 })
    s.addText(d, { x: x + 0.22, y: y + 0.5, w: cardW - 0.4, h: 0.5, fontFace: FONT, fontSize: 10.5, color: C.ink2, margin: 0, lineSpacingMultiple: 1.2, valign: "top" })
  })
  imageFrame(s, { path: DEMO_RESULTS, x: M, y: top + 2.85, w: 7.08, h: 2.05, label: "真机 · 文献与成果", caption: "文献、摘要、综述框架、证据图谱在同一研究包里汇总", dark: true })
  block(s, { x: 8.1, y: top + 2.9, w: 4.48, label: "一条可见的闭环", body: [
    "① 先给问题和边界",
    "② 再由流水线召回与抽取",
    "③ 最后把结论、引用和干预记录一起导出",
  ], size: 11.5 })
  s.addText("AI 负责加速，研究者负责决定什么能写进最终版本。", { x: 8.1, y: top + 4.8, w: 4.3, h: 0.44, fontFace: FONT, fontSize: 11.5, bold: true, color: C.accent, margin: 0, lineSpacingMultiple: 1.2 })
  footer(s)
  return s
}

// ============================================================ P8 自研模块
function modules() {
  const { s, top } = page("自研模块：观测研究过程本身", { kicker: "叁 · 核心功能", page: 8 })
  s.addText("不是把功能堆在一起，而是把研究对象连成一张图。", { x: M, y: top, w: 6.4, h: 0.35, fontFace: FONT, fontSize: 13, color: C.ink2, margin: 0 })
  const nodes = [
    ["假设台", "先问：这个想法值不值得做？", M, top + 0.62, "F7F9FC"],
    ["证据矩阵", "再看：哪些文献支持、挑战或缺失？", M + 3.78, top + 0.62, "F4FAF7"],
    ["实验树", "把失败也留下，方便剪枝和复盘。", M, top + 2.18, "F4FAF7"],
    ["AI 审稿人", "投稿前按结构、证据、可检验性自审。", M + 3.78, top + 2.18, "F7F9FC"],
  ]
  nodes.forEach(([title, body, x, y, fill], i) => archBox(s, { x, y, w: 3.35, h: 1.08, title, body, accent: i % 2 ? "286557" : C.accent, fill }))
  arrow(s, { x: M + 3.38, y: top + 1.16, w: 0.34, color: C.hairStrong })
  arrow(s, { x: M + 2.05, y: top + 1.78, w: 0, color: C.hairStrong })
  arrow(s, { x: M + 5.42, y: top + 1.78, w: 0, color: C.hairStrong })
  s.addShape(pres.shapes.LINE, { x: M + 2.05, y: top + 1.7, w: 3.38, h: 0, line: { color: C.hairStrong, width: 1, dash: "dash" } })
  s.addText("同一份证据账本", { x: M + 2.15, y: top + 1.78, w: 3.15, h: 0.26, fontFace: FONT, fontSize: 10.5, color: C.accent, bold: true, align: "center", margin: 0 })
  imageFrame(s, { path: path.join(ROOT, "data", "artifacts", "graph", "evidence_c41ff9.png"), x: 8.0, y: top + 0.12, w: 4.58, h: 3.58, label: "证据图谱 · 可视化回看", caption: "每个节点都能回到文献、轮次与引用池" })
  block(s, { x: M, y: top + 4.03, w: 6.95, size: 11.2, bodyColor: C.muted, body: [
    "· 看板：池健康度、锚定率、孤儿文献、缺证据章节",
    "· 新颖性检测：选题前先查差异点与风险，避免从已有答案开始",
    "· 论文复现：白名单工具 + 研究包，让复跑有边界、有记录",
  ] })
  footer(s)
  return s
}

// ============================================================ P9 Harness
function harness() {
  const { s, top } = page("智能体运行时：权限、审批与韧性", { kicker: "肆 · 智能体内核", page: 9 })
  const left = [
    ["权限三档 × 三域", "只读 / 工作区写入 / 完全访问，作用于引用 / 数据 / 代码；只读默认放行，写入与网络默认询问，策略可持久化（支持 research.* 通配符）。"],
    ["审批卡的三个问题", "目标是什么、影响面多大、是否可逆——审批时把三个答案摆在眼前；支持「批准并本会话记住」，重启后从事件记录恢复，已处理不可重放。"],
  ]
  const right = [
    ["子智能体与扇出", "spawn 进程内派发，fanout 并行文献子任务：每路真实双源检索，子会话 JSONL 审计对父会话可见，单路失败降级。"],
    ["韧性四件套", "上下文压缩（软阈值先摘要折叠）· 工具失败自愈（错误回填，2 轮上限）· 单步并行只读工具（≤4 并发保序）· 中断对账恢复（崩溃后按日志续跑）。"],
  ]
  let y = top
  for (let i = 0; i < 2; i++) {
    let ly = sub(s, { x: M, y, w: 5.9, text: left[i][0] })
    ly = block(s, { x: M + 0.2, y: ly, w: 5.7, body: left[i][1], size: 12, gap: 0.3 })
    let ry = sub(s, { x: 7.1, y, w: 5.48, text: right[i][0] })
    ry = block(s, { x: 7.3, y: ry, w: 5.28, body: right[i][1], size: 12, gap: 0.3 })
    y = Math.max(ly, ry) + 0.4
  }
  code(s, {
    x: M, y: y + 0.02, w: 6.35, lines: [
      "❯ /plan 研究智能体        → 只读规划：拆解任务，不执行副作用",
      "❯ /agent 研究智能体       → 执行模式：写入 / 网络工具触发审批卡",
      "❯ /approve                → 编号处理：1 放行 / 2 拒绝 / 3 看参数",
    ], size: 9.7, label: "一条命令看清边界：规划不写盘，执行必过审批",
  })
  s.addShape(pres.shapes.RECTANGLE, { x: 7.35, y: y + 0.02, w: 5.23, h: 1.34, fill: { color: C.code }, line: { color: C.hair, width: 0.8 } })
  s.addText("一次审批的闭环", { x: 7.62, y: y + 0.22, w: 2.2, h: 0.24, fontFace: FONT, fontSize: 12, bold: true, color: C.ink, margin: 0 })
  const flow = [["目标", "要做什么"], ["影响", "会改哪里"], ["可逆", "能否撤回"]]
  flow.forEach(([title, body], i) => {
    const x = 7.62 + i * 1.55
    s.addShape(pres.shapes.OVAL, { x, y: y + 0.6, w: 0.38, h: 0.38, fill: { color: i === 1 ? "286557" : C.accent }, line: { color: i === 1 ? "286557" : C.accent, width: 0 } })
    s.addText(String(i + 1), { x, y: y + 0.68, w: 0.38, h: 0.18, fontFace: MONO, fontSize: 9, color: "FFFFFF", bold: true, align: "center", margin: 0 })
    s.addText(title, { x: x + 0.48, y: y + 0.58, w: 0.86, h: 0.22, fontFace: FONT, fontSize: 10.5, bold: true, color: C.ink, margin: 0 })
    s.addText(body, { x: x + 0.48, y: y + 0.83, w: 0.86, h: 0.2, fontFace: FONT, fontSize: 8.5, color: C.muted, margin: 0 })
    if (i < 2) arrow(s, { x: x + 0.98, y: y + 0.79, w: 0.44, color: C.hairStrong })
  })
  footer(s)
  return s
}

// ============================================================ P10 双端体验与界面
function experience() {
  const { s, top } = page("双端体验与界面设计", { kicker: "伍 · 体验与质量", page: 10 })
  imageFrame(s, { path: DEMO_HOME, x: M, y: top + 0.12, w: 3.9, h: 2.48, label: "新任务", caption: "离线研究入口", dark: true })
  imageFrame(s, { path: DEMO_AGENT, x: M + 4.08, y: top + 0.12, w: 3.9, h: 2.48, label: "智能体", caption: "计划、工具与人工确认", dark: true })
  imageFrame(s, { path: DEMO_MONITOR, x: M + 8.16, y: top + 0.12, w: 4.42, h: 2.48, label: "研究进度", caption: "五站状态、详情和恢复入口", dark: true })
  code(s, {
    x: M, y: top + 3.05, w: 6.95, lines: [
      "❯ /config       模型 / 主题 / 科研 / 界面（Enter 即切即存）",
      "❯ @src/ranking.py      文件提及补全 → 自动进入上下文",
      "❯ !pytest -q           shell 模式（项目根目录执行）",
      "❯ /timeline            七站灯轨 + 空洞提示",
      "❯ /read board          全屏阅读器：/ 搜索 · n 跳转 · q 返回",
    ], size: 9.5, label: "终端：93 条命令 + 三层补全，全键盘流",
  })
  block(s, { x: 8.15, y: top + 3.06, w: 4.43, label: "为什么认真做界面", body: [
    "· 科研会话常常持续数小时：信息密度优先，少滚动",
    "· 面板吃掉终端高度：光标可见 > 上下文 > 装饰",
    "· 终端、桌面、手机共享同一套状态和命令，不需要重新学习",
  ], size: 11 })
  footer(s)
  return s
}

// ============================================================ P11 安全合规复现
function safety() {
  const { s, top } = page("安全、合规与可复现", { kicker: "伍 · 体验与质量", page: 11 })
  block(s, {
    x: M, y: top, w: 6.0, label: "密钥与权限", body: [
      "· 密钥仅显示「已配置」，原文不出本机；敏感命令不进输入历史、回显脱敏",
      "· 工具权限三档 + 每智能体白名单；按会话文件锁，多进程审批互斥",
      "· 研究数据可用 GEZHI_DATA_DIR 完全隔离；远程模式危险操作交回主机",
    ], size: 11.5,
  })
  block(s, {
    x: 7.1, y: top, w: 5.48, label: "合规标识（依据《人工智能生成合成内容标识办法》）", body: [
      "· 摘要与框架正文标注：生成服务、生成方式、时间、任务标识、提示词版本",
      "· 导出目录自动生成 manifest.md，逐文件标注 AI 参与度",
      "· 确定性排版产物明确标注「非 AI 生成」",
    ], size: 11.5,
  })
  code(s, {
    x: M, y: top + 2.62, w: 7.1, lines: [
      "研究包（一次导出即可带走全部过程）",
      "  manifest.md      产物清单 + AI 参与度 + 生成参数",
      "  *.json / *.csv   断点、漏斗、文献池、事件流",
      "  data.sha256      数据与产物校验和；研究档案库 git 逐轮提交（署名），过程有 diff 可回滚",
    ], size: 10, label: "可复现：把「怎么做的」和「结果是什么」一起打包",
  })
  imageFrame(s, { path: DEMO_RESULTS, x: 7.45, y: top + 2.5, w: 5.13, h: 2.45, label: "真机 · 研究包入口", dark: true })
  s.addText("安全性有证据，开源有连接。", { x: M, y: top + 4.25, w: CW, h: 0.3, fontFace: FONT, fontSize: 12, bold: true, color: C.accent, margin: 0 })
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: top + 4.62, w: 7.05, h: 0.36, fill: { color: "F4FAF7" }, line: { color: "BFD9CE", width: 0.8 } })
  s.addText("持续更新仓库  ·  github.com/abzzyyxcpht/gezhi-scizhi  ·  Apache-2.0  ·  README / CHANGELOG / CI", { x: M + 0.16, y: top + 4.71, w: 6.75, h: 0.16, fontFace: FONT, fontSize: 9.1, color: "286557", bold: true, margin: 0 })
  footer(s)
  return s
}

// ============================================================ P12 工程质量
function quality() {
  const { s, top } = page("工程质量与验证基线", { kicker: "伍 · 体验与质量", page: 12 })
  const nums = [["1691", "项 Python 断言全绿（2 跳过，160 个测试文件）"], ["82", "个 TUI 测试文件；单链 62 项通过，含无头真帧断言"], ["93", "条 REPL 命令，每条有描述与可用性合约"]]
  const colW = 2.7
  const colGap = 0.12
  nums.forEach(([v, d], i) => {
    const x = M + i * (colW + colGap)
    s.addText(v, { x, y: top, w: colW - 0.18, h: 0.62, fontFace: FONT, fontSize: 30, bold: true, color: C.accent, margin: 0 })
    s.addText(d, { x, y: top + 0.64, w: colW - 0.18, h: 0.72, fontFace: FONT, fontSize: 10.2, color: C.muted, margin: 0, lineSpacingMultiple: 1.25 })
    if (i < 2) s.addShape(pres.shapes.LINE, { x: x + colW - 0.25, y: top, w: 0, h: 1.35, line: { color: C.hair, width: 0.75 } })
  })
  imageFrame(s, { path: DEMO_AGENT, x: 9.55, y: top + 0.05, w: 3.03, h: 1.55, label: "真机 · 工具与审批", caption: "真帧与人工关口进入回归口径", dark: true })
  let y = top + 2.0
  y = sub(s, { x: M, y, w: CW, text: "测试怎么写的" })
  y = block(s, {
    x: M + 0.2, y, w: CW - 0.2, size: 11.5, gap: 0.35,
    body: [
      "· 纯函数不变量优先：几何 / 预算 / 滚动 / 光标、漏斗口径、引用格式、共识计量都有单测",
      "· 真帧集成：无头渲染抓帧断言（面板分页、窄屏 54×18、人工关口菜单、夹具失败清理）",
      "· 隔离与并发：临时数据目录、按会话文件锁、僵尸进程清理回归；Ruff 与 UI 静态校验进 CI 口径",
    ],
  })
  y = sub(s, { x: M, y, w: 7.0, text: "开箱可用" })
  block(s, {
    x: M + 0.2, y, w: 7.0, size: 11.0,
    body: [
      "· /doctor 九项自检：数据目录、依赖、文献源连通、模型端点与清单、提示词、MCP、git、缓存、断点存档、向量检索",
      "· 离线可完整演示（内置公开语义样例，逐比特可复现）；Windows 安装包含 SHA256 校验",
    ],
  })
  footer(s)
  return s
}

// ============================================================ P13 优势
function advantage() {
  const { s, top } = page("优势总结", { kicker: "伍 · 体验与质量", page: 13 })
  const yEnd = table(s, {
    x: M, y: top, w: CW, rows: [
      ["维度", "Codex", "Claude Code", "OpenClaw（小龙虾）", "格致 SciZhi"],
      ["默认定位", "编码交付", "终端编码", "多渠道通用助手", "科研过程工作台"],
      ["双源文献检索", "需工作流", "需工作流", "可扩展", "内置：OpenAlex + CrossRef"],
      ["证据链追溯", "需外接", "需外接", "需外接", "内置：账本 + 图谱 + manifest"],
      ["断点 / 人工关口", "有审批模式", "有权限控制", "取决于配置", "Checkpoint + HITL"],
      ["科研模块", "通用工具", "通用工具", "通用工具", "假设台 / 实验树 / 审稿人"],
      ["移动 / 远控", "以代码环境为主", "以终端为主", "多渠道原生", "局域网 Web 远控"],
      ["本页口径", "公开定位", "公开定位", "公开定位", "本项目实测流程"],
    ], colW: [2.25, 2.0, 2.0, 2.55, 3.0], size: 9.3, rowH: 0.47, highlightLast: true,
  })
  const highlights = [
    ["科研专用链路", "OpenAlex + CrossRef → 证据账本 / 图谱 → manifest"],
    ["可控可审计", "三档 × 三域权限 + Checkpoint / HITL + 事件流"],
    ["可交接可复跑", "研究包 + data.sha256 + git diff / 回滚 + 离线复现"],
  ]
  const hg = 0.18
  const hw = (CW - hg * 2) / 3
  highlights.forEach(([title, body], i) => {
    const x = M + i * (hw + hg)
    const y = yEnd + 0.2
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: hw, h: 0.7, fill: { color: i === 1 ? "F4FAF7" : C.code }, line: { color: i === 1 ? "BFD9CE" : C.hair, width: 0.8 } })
    s.addText(title, { x: x + 0.14, y: y + 0.12, w: hw - 0.24, h: 0.2, fontFace: FONT, fontSize: 11.2, bold: true, color: i === 1 ? "286557" : C.accent, margin: 0 })
    s.addText(body, { x: x + 0.14, y: y + 0.38, w: hw - 0.24, h: 0.2, fontFace: FONT, fontSize: 8.8, color: C.ink2, margin: 0 })
  })
  s.addText("注：比较的是产品默认定位与本项目演示口径，不构成基准性能排名。", { x: M, y: yEnd + 1.02, w: CW, h: 0.2, fontFace: FONT, fontSize: 9.3, color: C.muted, margin: 0 })
  footer(s)
  return s
}

// ============================================================ P14 路线图
function roadmap() {
  const { s, top } = page("进度与下一步", { kicker: "伍 · 体验与质量", page: 14 })
  const done = [["M1", "检索骨架"], ["M2", "综述闭环"], ["M3", "双端体验"], ["M4", "对标收尾"]]
  const next = [["插件生态", "开放市场 + 沙箱 + 权限清单"], ["团队协作", "共享证据池 + 审批流 + 角色"], ["跨平台验收", "macOS / Linux · IME · 辅助功能"], ["真实评测集", "跨学科对照 + 供应商盲测"]]
  const y0 = top + 0.65
  s.addText("已完成（2026-08 → 2026-10）", { x: M, y: top + 0.05, w: CW, h: 0.3, fontFace: FONT, fontSize: 12, bold: true, color: C.ink, margin: 0 })
  const span1 = CW - 1.7
  const gap = span1 / 3
  s.addShape(pres.shapes.LINE, { x: M + 0.85, y: y0, w: span1, h: 0, line: { color: C.hairStrong, width: 1.25 } })
  done.forEach(([tag, t], i) => {
    const x = M + 0.85 + i * gap
    s.addShape(pres.shapes.OVAL, { x: x - 0.05, y: y0 - 0.05, w: 0.1, h: 0.1, fill: { color: C.accent }, line: { color: C.accent, width: 0 } })
    s.addText(`${tag} ${t}`, { x: x - 0.85, y: y0 + 0.16, w: 1.7, h: 0.3, fontFace: FONT, fontSize: 11, color: C.ink, align: "center", margin: 0 })
  })
  s.addText("检索 / 去重 / 漏斗 / 断点 · 抽取 / 框架 / 防幻觉 / 补检 · 双端与手机远控 · 单引擎 / 权限三档 / 研究包", { x: M, y: y0 + 0.55, w: CW, h: 0.5, fontFace: FONT, fontSize: 10.5, color: C.muted, margin: 0, lineSpacingMultiple: 1.3 })
  const y1 = top + 2.5
  s.addText("下一步（1.0 版）", { x: M, y: y1, w: CW, h: 0.3, fontFace: FONT, fontSize: 12, bold: true, color: C.ink, margin: 0 })
  const span2 = CW - 1.9
  const gap2 = span2 / 3
  s.addShape(pres.shapes.LINE, { x: M + 0.95, y: y1 + 0.6, w: span2, h: 0, line: { color: C.hair, width: 1 } })
  next.forEach(([t, d], i) => {
    const x = M + 0.95 + i * gap2
    s.addShape(pres.shapes.OVAL, { x: x - 0.05, y: y1 + 0.55, w: 0.1, h: 0.1, fill: { color: C.page }, line: { color: C.hairStrong, width: 1 } })
    s.addText(t, { x: x - 0.85, y: y1 + 0.76, w: 1.8, h: 0.3, fontFace: FONT, fontSize: 11, bold: true, color: C.ink, align: "center", margin: 0 })
    s.addText(d, { x: x - 0.95, y: y1 + 1.06, w: 2.0, h: 0.8, fontFace: FONT, fontSize: 9, color: C.muted, align: "center", valign: "top", margin: 0, lineSpacingMultiple: 1.2 })
  })
  s.addText("目标：把「可追溯科研」从个人工具做成课题组基础设施——每个结论都能回到文献、命令与轮次。", { x: M, y: H - 1.5, w: CW, h: 0.5, fontFace: FONT, fontSize: 12, bold: true, color: C.accent, margin: 0 })
  footer(s)
  return s
}

// ============================================================ P15 分工
function team() {
  const { s, top } = page("团队分工", { kicker: "陆 · 团队", page: 15 })
  const yEnd = table(s, {
    x: M, y: top, w: CW, rows: [
      ["姓名", "角色", "负责内容"],
      ["（填写姓名）", "组长 · 主责", "系统架构与核心引擎：智能体运行时（规划 / 执行 / 审查）、编排器与断点 / 人工关口、双源检索与排序、双端联调；路演主讲与技术方案定稿"],
      ["（填写姓名）", "成员 · 文献与证据", "文献检索与证据链：OpenAlex / CrossRef 接入、去重与漏斗指标、引用滚雪球与证据账本字段"],
      ["（填写姓名）", "成员 · 桌面端界面", "桌面端界面与设置中心：Flet 页面、主题与字体、审批卡与任务进程面板"],
      ["（填写姓名）", "成员 · 文档与演示", "文档与演示材料：README 与使用指南、路演 PPT、演示脚本与录屏、答辩问答准备"],
      ["（填写姓名）", "成员 · 测试与合规", "测试与合规：pytest 用例维护、隔离验证脚本、回归清单与系统自检项核对；参考文献格式（GB/T 7714 / APA）校对、AI 内容标识文案、赛题材料整理与现场后勤"],
    ], colW: [1.9, 2.35, 7.58], size: 10.5, rowH: 0.58,
  })
  block(s, {
    x: M, y: yEnd + 0.28, w: CW, size: 11,
    body: [
      "分工按模块边界划分：核心引擎与系统集成由组长主责，并统筹进度与联调；其余成员各自独立负责一个功能模块，可并行推进、互不阻塞。",
      "协作方式：研究档案库 git 逐轮提交留痕；每周一次接口对齐（引擎 / 界面 / 文档 / 测试）；验收以测试基线与演示链路为准。",
    ],
  })
  footer(s)
  return s
}

// ============================================================ 组装
function build() {
  newDeck()
  cover()
  overview()
  pain()
  architecture()
  pipeline()
  f1()
  f2345()
  modules()
  harness()
  experience()
  safety()
  quality()
  advantage()
  roadmap()
  team()
}

build()
const out = process.argv[2] || "项目路演/格致SciZhi-路演-15页.pptx"
pres.writeFile({ fileName: out }).then(() => console.log("WROTE " + out + " :: " + pres.slides.length + " slides"))
