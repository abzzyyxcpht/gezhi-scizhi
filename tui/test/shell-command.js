import assert from "node:assert/strict"
import test from "node:test"
import { createSessionController } from "../src/opentui/session-controller.js"

async function render(response, { command = "fixture", reject = false } = {}) {
  const requests = []
  const controller = createSessionController({ bridge: { call: async (request) => {
    requests.push(request)
    if (reject) throw response
    return response
  } } })
  const { lines } = await controller.shell(command)
  assert.deepEqual(requests, [{ cmd: "shell_exec", command }])
  return lines.map((line) => line.text)
}

test("shell displays short successful output and its exit status", async () => {
  assert.deepEqual(await render({ ok: true, text: "first\nsecond\n", code: 0 }),
    ["$ fixture", "first", "second", "exit 0"])
})

test("shell preserves nonzero exit status and stderr diagnostics", async () => {
  const lines = await render({ ok: true, text: "failure detail\n", code: 7 })
  assert.ok(lines.includes("failure detail"))
  assert.ok(lines.includes("exit 7"))
  assert.ok(!lines.includes("exit 0"))
})

test("shell timeout retains partial output without a fabricated success", async () => {
  const lines = await render({ ok: false, error: "命令超时（10s）", timed_out: true,
    text: "diagnostic before timeout\n", code: null })
  assert.ok(lines.includes("diagnostic before timeout"))
  assert.ok(lines.includes("✗ 命令超时（10s）"))
  assert.ok(!lines.includes("exit 0"))
})

test("shell signals truncation after the ten visible output lines", async () => {
  const text = Array.from({ length: 20 }, (_, index) => `line ${index + 1}`).join("\n")
  const lines = await render({ ok: true, text, code: 0, truncated: false })
  assert.ok(lines.includes("line 10"))
  assert.ok(!lines.includes("line 11"))
  assert.match(lines.slice(11).join("\n"), /输出已截断/)
})

test("shell honors backend truncation even for a short preview", async () => {
  const lines = await render({ ok: true, text: "retained prefix", code: 0, truncated: true })
  assert.ok(lines.includes("retained prefix"))
  assert.match(lines.join("\n"), /输出已截断/)
})

test("shell also keeps captured diagnostics on an execution error", async () => {
  const lines = await render({ ok: false, error: "输出管道读取失败", text: "partial diagnostics", code: 0 })
  assert.ok(lines.includes("partial diagnostics"))
  assert.match(lines.join("\n"), /输出管道读取失败/)
  assert.ok(!lines.includes("exit 0"))
})

test("shell never defaults an unknown exit code to success", async () => {
  const lines = await render({ ok: true, text: "output", code: null })
  assert.ok(!lines.includes("exit 0"))
  assert.match(lines.join("\n"), /退出码未知/)
})

test("shell reports a bridge rejection without an exit code", async () => {
  const lines = await render(new Error("bridge disconnected"), { reject: true })
  assert.match(lines.join("\n"), /bridge disconnected/)
  assert.ok(!lines.some((line) => line.startsWith("exit ")))
})
