// Phase 0 冒烟测试 B：headless test renderer（无 TTY，CI 可用）
// 运行: node src/opentui/spike/smoke-test-renderer.js
import { createTestRenderer } from "@mbears/opentui-core/testing"
import { TextRenderable, BoxRenderable } from "@mbears/opentui-core"

const log = (m) => console.error(`[spike] ${m}`)

try {
  const setup = await createTestRenderer({ width: 50, height: 10, targetFps: 30 })
  const { renderer } = setup
  log("TEST_RENDERER_OK")

  const box = new BoxRenderable(renderer, {
    width: "100%",
    height: "100%",
    flexDirection: "column",
    padding: 1,
    border: true,
    borderStyle: "rounded",
    backgroundColor: "#0f1419",
  })
  renderer.root.add(box)
  box.add(new TextRenderable(renderer, { content: "格致 SciZhi", fg: "#7dd3fc", bold: true }))
  box.add(new TextRenderable(renderer, { content: "◇ 通用 ◎ 科研", fg: "#e6edf3" }))

  await setup.renderOnce()
  const frame = setup.captureCharFrame()
  console.log(frame)
  if (!frame.includes("SciZhi")) {
    log("GRID_ASSERT_FAIL: 未渲染出品牌文字")
    process.exit(1)
  }
  log("GRID_ASSERT_OK (含 CJK + truecolor 样式渲染)")

  // 模拟键盘输入：验证 mockInput 可用（后续 UI 测试的基础）
  setup.mockInput.typeText("h")
  await setup.renderOnce()
  log("MOCK_INPUT_OK")

  // 模拟鼠标：验证 mockMouse 可用
  setup.mockMouse.moveTo(5, 3)
  await setup.renderOnce()
  log("MOCK_MOUSE_OK")

  renderer.destroy()
  log("SMOKE_B_PASS")
  process.exit(0)
} catch (err) {
  log(`SMOKE_B_FAIL ${err.message}`)
  process.exit(1)
}
