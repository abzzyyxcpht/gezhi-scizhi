import assert from 'node:assert/strict'
import React from 'react'
import { testRender } from '@mbears/opentui-react/test-utils'
import { OpenTuiApp } from '../src/opentui/runtime.js'
import { currentTheme } from '../src/opentui/theme/theme.js'

let preferences = { theme_mode: 'dark', ui_animations: false, density: 'comfortable' }
let holdRead = false, resolveRead, reads = 0
let failSave = false
const controller = {
  readMode: async () => 'general',
  configItems: async () => ({ ...preferences }),
  appearancePrefs: async () => {
    reads++
    const snapshot = { ...preferences }
    if (holdRead) return new Promise((resolve) => { resolveRead = () => resolve(snapshot) })
    return snapshot
  },
  configSet: async (key, value) => {
    if (failSave) return { ok: false, error: 'fixture save failed' }
    preferences = { ...preferences, [key]: value }; return { ok: true }
  },
}
let setup
await React.act(async () => { setup = await testRender(React.createElement(OpenTuiApp, { controller }), { width: 90, height: 24, exitOnCtrlC: false }) })
async function wait(ms = 60) { await React.act(async () => new Promise((resolve) => setTimeout(resolve, ms))); await setup.renderOnce() }
async function type(text) { await React.act(async () => setup.mockInput.typeText(text)); await wait() }
async function key(name, mods) { await React.act(async () => setup.mockInput.pressKey(name, mods)); await wait() }
try {
  await wait()
  await type('keep-this-draft')
  preferences = { ...preferences, theme_mode: 'light', density: 'compact' }
  await wait(2150)
  assert.equal(currentTheme(), 'light', 'desktop changes appear without opening /config')
  assert.ok(setup.captureCharFrame().includes('keep-this-draft'), 'background preference updates preserve composer')
  holdRead = true
  await wait(2100)
  assert.equal(typeof resolveRead, 'function', 'one periodic read is now pending')
  const pendingCount = reads
  await wait(2100)
  assert.equal(reads, pendingCount, 'a pending poll prevents overlapping reads')
  await key('c', { ctrl: true })
  await type('/theme dark'); await key('RETURN')
  await type('new-draft')
  await React.act(async () => resolveRead())
  await wait()
  assert.equal(currentTheme(), 'dark', 'a stale read cannot overwrite a newer saved preference')
  assert.ok(setup.captureCharFrame().includes('new-draft'))
  await key('c', { ctrl: true })
  failSave = true
  await type('/theme light'); await key('RETURN')
  assert.equal(currentTheme(), 'dark', 'failed save leaves the applied preference unchanged')
  assert.ok(setup.captureCharFrame().includes('fixture save failed'), 'failed save is visible')
  failSave = false
  await type('/theme light'); await key('RETURN')
  assert.equal(currentTheme(), 'light', 'save can be retried after failure')
  const beforeDestroy = reads
  await React.act(async () => setup.renderer.destroy())
  setup = null
  await new Promise((resolve) => setTimeout(resolve, 2100))
  assert.equal(reads, beforeDestroy, 'unmount clears the preference polling timer')
} finally { if (setup) await React.act(async () => setup.renderer.destroy()) }

// A startup settings read can be slow because it also waits for llm_status.
// A newer background read must win even if neither client has saved locally.
let finishStartup
const startupDark = new Promise((resolve) => { finishStartup = resolve })
const delayedController = {
  readMode: async () => 'general',
  configItems: () => startupDark,
  appearancePrefs: async () => ({ theme_mode: 'light', ui_animations: false }),
}
await React.act(async () => { setup = await testRender(React.createElement(OpenTuiApp, { controller: delayedController }), { width: 90, height: 24, exitOnCtrlC: false }) })
try {
  await wait(2150)
  assert.equal(currentTheme(), 'light', 'newer poll applies while startup read is pending')
  await type('startup-draft')
  await React.act(async () => finishStartup({ theme_mode: 'dark', ui_animations: false }))
  await wait()
  assert.equal(currentTheme(), 'light', 'older startup response cannot revert the newer poll')
  assert.ok(setup.captureCharFrame().includes('startup-draft'))
} finally { await React.act(async () => setup.renderer.destroy()) }
console.log('opentui-appearance-sync: ALL_PASS')
process.exit(0)
