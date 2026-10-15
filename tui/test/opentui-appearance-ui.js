import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import React from 'react'
import { testRender } from '@mbears/opentui-react/test-utils'
import { OpenTuiApp } from '../src/opentui/runtime.js'
import { currentTheme } from '../src/opentui/theme/theme.js'
import { flattenConfig } from '../src/opentui/components/config-panel.js'

let prefs = { theme_mode: 'light', density: 'compact', ui_animations: false, sidebar_mode: 'docked', inspector_visible: false }
const saved = []
const controller = {
  readMode: async () => 'general',
  sidebar: async () => [{ title: '研究工作区', lines: [{ text: 'SIDEBAR-CONTENT' }] }],
  configItems: async () => ({ ...prefs }),
  configSet: async (key, value) => { saved.push({ [key]: value }); prefs = { ...prefs, [key]: value }; return { ok: true } },
  submit: async () => ({ lines: [{ text: '```python\nreturn 123\n```' }] }),
}
let setup
await React.act(async () => { setup = await testRender(React.createElement(OpenTuiApp, { controller }), { width: 78, height: 30, exitOnCtrlC: false }) })
async function settle() {
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 90)) })
  await setup.renderOnce()
  return setup.captureCharFrame()
}
async function key(name, options) {
  const names = { return: 'RETURN', escape: 'ESCAPE', down: 'ARROW_DOWN', up: 'ARROW_UP' }
  await React.act(async () => setup.mockInput.pressKey(names[name] || name, options)); return settle()
}
async function type(text) { await React.act(async () => setup.mockInput.typeText(text)); return settle() }
function capture(name) {
  const output = process.env.GEZHI_UI_CAPTURE_DIR
  if (!output) return
  fs.mkdirSync(output, { recursive: true })
  fs.writeFileSync(path.join(output, `${name}.txt`), setup.captureCharFrame(), 'utf8')
  const frame = setup.captureSpans()
  fs.writeFileSync(path.join(output, `${name}.json`), JSON.stringify({ ...frame,
    fixture: true, native_pty: false,
    lines: frame.lines.map((line) => ({ spans: line.spans.map((span) => ({ ...span, fg: span.fg.toInts(), bg: span.bg.toInts() })) })),
  }, null, 2), 'utf8')
}
try {
  await settle()
  assert.equal(currentTheme(), 'light', 'startup applies persisted theme before opening /config')
  assert.ok(!setup.captureCharFrame().includes('ctx '), 'inspector visibility is applied at startup')
  await type('keep-draft')
  let frame = await key('b', { ctrl: true })
  assert.ok(frame.includes('SIDEBAR-CONTENT'), 'Ctrl+B opens a real narrow-screen drawer')
  capture('narrow-sidebar-overlay')
  await type('SHOULD-NOT-LEAK')
  await key('return')
  frame = await key('escape')
  assert.ok(frame.includes('keep-draft'), 'closing drawer restores the unchanged draft')
  assert.ok(!frame.includes('SHOULD-NOT-LEAK'), 'drawer focus consumes text and submit keys')
  assert.ok(!frame.includes('SIDEBAR-CONTENT'), 'Esc closes overlay without clearing draft')
  await key('c', { ctrl: true })
  await type('/config')
  await key('return')
  const rows = flattenConfig()
  for (let i = 0; i < rows.findIndex((r) => r.key === 'theme_mode'); i++) await key('down')
  await key('return') // light -> dark
  assert.equal(currentTheme(), 'dark', 'theme changes live after saving')
  capture('appearance-config-dark')
  assert.deepEqual(saved[0], { theme_mode: 'dark' }, 'only changed preference is sent')
  await key('escape')
  prefs = { ...prefs, theme_mode: 'light', inspector_visible: true }
  await type('/config')
  await key('return')
  assert.equal(currentTheme(), 'light', 'opening config refreshes externally changed preferences')
  await key('escape')
  assert.ok((await settle()).includes('ctx '), 'refreshed inspector preference affects shell')
  await type('render-code')
  await key('return')
  const keywordColor = () => setup.captureSpans().lines.flatMap((line) => line.spans).find((span) => span.text.includes('return'))?.fg.toInts().slice(0, 3)
  prefs = { ...prefs, theme_mode: 'dark', code_dark_theme: 'github-dark' }
  await type('/config'); await key('return'); await key('escape')
  assert.deepEqual(keywordColor(), [255, 123, 114], 'default GitHub code palette reaches rendered spans')
  prefs = { ...prefs, code_dark_theme: 'atom-one-dark' }
  await type('/config'); await key('return'); await key('escape')
  assert.deepEqual(keywordColor(), [198, 120, 221], 'changing only code theme repaints with animations disabled')
  capture('code-theme-atom-dark')
  await React.act(async () => setup.renderer.emit('theme_mode', 'light'))
  await settle()
  assert.equal(currentTheme(), 'dark', 'explicit dark mode ignores terminal theme events')
  prefs = { ...prefs, theme_mode: 'system' }
  await type('/config'); await key('return'); await settle(); await settle()
  await React.act(async () => setup.renderer.emit('theme_mode', 'light'))
  await settle()
  assert.equal(currentTheme(), 'light', 'system mode follows subsequent terminal theme events')
  console.log('opentui-appearance-ui: ALL_PASS')
} finally { await React.act(async () => setup.renderer.destroy()) }
process.exit(0)
