import assert from 'node:assert/strict'
import React from 'react'
import { testRender } from '@mbears/opentui-react/test-utils'
import { OpenTuiApp } from '../src/opentui/runtime.js'
import { createSessionController } from '../src/opentui/session-controller.js'

let snapshot = { session_id: 's1', context: { tokens: 1500, schema_tokens: 500, input_tokens: 2000, output_reserve_tokens: 1000, limit_tokens: 10000 } }
let hold = false, finishRead
const bridge = {
  connection: () => ({ mode: 'local' }),
  whenReady: async () => true,
  call: async (request) => {
    if (request.cmd === 'settings_get') return { ok: true, settings: { ui_animations: false } }
    if (request.cmd === 'workbench_mode') return { ok: true, mode: 'general' }
    if (request.cmd === 'session_status') {
      const result = { ok: true, status: structuredClone(snapshot) }
      if (hold) return new Promise((resolve) => { finishRead = () => resolve(result) })
      return result
    }
    return { ok: true }
  },
}
const controller = createSessionController({ bridge })
let setup
await React.act(async () => { setup = await testRender(React.createElement(OpenTuiApp, { controller }), { width: 90, height: 24, exitOnCtrlC: false }) })
async function settle(ms = 60) {
  await React.act(async () => new Promise((resolve) => setTimeout(resolve, ms)))
  await setup.renderOnce()
  return setup.captureCharFrame()
}
try {
  assert.ok((await settle()).includes('ctx 暂无数据'), 'an empty session never displays sample context usage')
  controller.setSession('s1')
  assert.ok((await settle(2150)).includes('ctx 20% · 2K/10K'), 'footer reads the active session context')
  snapshot.context.input_tokens = null
  assert.ok((await settle(2150)).includes('ctx 暂无数据'), 'missing token counts stay unknown rather than becoming zero')
  hold = true
  const pending = controller.contextBreakdown()
  controller.setSession('s2')
  finishRead()
  assert.equal(await pending, null, 'a previous session response cannot replace current context')
  hold = false
  controller.setSession(null)
  assert.ok((await settle(2150)).includes('ctx 暂无数据'), 'leaving the session clears context usage')
} finally { await React.act(async () => setup.renderer.destroy()) }
console.log('opentui-context-telemetry: ALL_PASS')
process.exit(0)
