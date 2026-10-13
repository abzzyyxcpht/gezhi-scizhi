// Phase 0 冒烟测试：验证 @mbears/opentui-core 原生渲染器在 Windows 真终端可用
// 运行: node src/opentui/spike/smoke-core.js   （需在真实 TTY 中运行）
import { createCliRenderer, TextRenderable, BoxRenderable } from "@mbears/opentui-core"

const log = (m) => console.error(`[spike] ${m}`)

let renderer
try {
  renderer = await createCliRenderer({
    width: 0, // 0 = 用终端实际尺寸
    height: 0,
    targetFps: 30,
    useMouse: true,
    enableMouseMovement: true,
    exitOnCtrlC: false,
  })
} catch (err) {
  log(`CREATE_FAIL ${err.message}`)
  process.exit(1)
}
log(`CREATE_OK`)

const box = new BoxRenderable(renderer, {
  width: "100%",
  height: "100%",
  flexDirection: "column",
  padding: 1,
  border: true,
  borderStyle: "rounded",
  title: " SciZhi Spike ",
  backgroundColor: "#0f1419",
})
renderer.root.add(box)

box.add(new TextRenderable(renderer, { content: "格致 SciZhi · OpenTUI 冒烟测试", fg: "#7dd3fc", bold: true }))
box.add(new TextRenderable(renderer, { content: "◇ 通用模式  ◎ 科研模式", fg: "#e6edf3" }))
box.add(new TextRenderable(renderer, { content: "truecolor / 鼠标 / 30fps 验证", fg: "#7d8590" }))

let frames = 0
let keys = 0
let mice = 0
renderer.on("frame", () => {
  frames++
  if (frames === 10) log(`FRAME_OK frames=${frames}`)
})
renderer.on("key", () => {
  keys++
  if (keys <= 3) log(`KEY_OK #${keys}`)
})
renderer.on("mouse", () => {
  mice++
  if (mice === 1 || mice % 20 === 0) log(`MOUSE_OK #${mice}`)
})

setTimeout(() => {
  const stats = renderer.stats ? `fps=${Math.round(renderer.stats.fps)}` : "stats=NA"
  log(`DONE totalFrames=${frames} keys=${keys} mice=${mice} ${stats}`)
  try { renderer.destroy() } catch { /* 退出时 sync 失败可忽略 */ }
  process.exit(frames >= 30 ? 0 : 1)
}, 15000)
