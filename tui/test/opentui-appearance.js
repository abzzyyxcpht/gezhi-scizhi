import assert from 'node:assert/strict'
import React from 'react'
import { TOKENS, paletteFor } from '../src/opentui/theme/tokens.js'
import { applyTheme } from '../src/opentui/theme/theme.js'
import { flattenConfig } from '../src/opentui/components/config-panel.js'
import { createSessionController } from '../src/opentui/session-controller.js'
import { StartupScreen } from '../src/opentui/components/startup-screen.js'
import { AppShell } from '../src/opentui/app-shell.js'
import { normalizeAppearance } from '../src/opentui/theme/preferences.js'

// A user must be able to switch back after visiting the light appearance page.
const originalDark = paletteFor('dark')
assert.equal(normalizeAppearance({ appearance_font_scale: 1.2 }).ui_font_size, 17, 'legacy font scale migrates like Python')
assert.equal(normalizeAppearance({ desktop_inspector: false }).inspector_visible, false, 'legacy inspector preference migrates')
assert.equal(normalizeAppearance({ desktop_inspector: false, inspector_visible: true }).inspector_visible, true, 'canonical preference wins over alias')
assert.equal(normalizeAppearance({ ui_font_size: '18' }).ui_font_size, 14, 'numeric strings are not accepted as desktop font sizes')
assert.equal(normalizeAppearance({ ui_font_size: 40 }).ui_font_size, 14, 'invalid sizes use shared defaults instead of diverging through clamping')
assert.equal(normalizeAppearance({ appearance_dark_mode: 'false' }).theme_mode, 'dark', 'string booleans do not enter legacy theme migration')
assert.equal(normalizeAppearance({ ui_animations: 'false' }).ui_animations, true, 'invalid boolean input uses schema default, like Python')
applyTheme('light')
applyTheme('dark')
assert.equal(TOKENS.background, originalDark.background, 'light → dark restores the original palette')
function luminance(hex) {
  const channels = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255)
    .map((n) => n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4)
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
}
for (const mode of ['dark', 'light']) {
  const palette = paletteFor(mode)
  for (const key of ['text', 'textMuted', 'textDim', 'accent', 'success', 'warning', 'error']) {
    for (const surface of ['background', 'panel']) {
      const a = luminance(palette[key]), b = luminance(palette[surface])
      assert.ok((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) >= 4.5, `${mode}.${key} remains legible on ${surface}`)
    }
  }
}

const settings = {
  theme_mode: 'system', density: 'compact', ui_animations: false,
  sidebar_mode: 'overlay', inspector_visible: false,
  code_show_line_numbers: false, code_wrap_long_lines: false,
  code_dark_theme: 'atom-one-dark', ui_font_size: 18, code_font_size: 16,
}
const calls = []
const ctrl = createSessionController({ bridge: {
  connection: () => ({ mode: 'local' }),
  call: async (request) => {
    calls.push(request)
    if (request.cmd === 'settings_get') return { ok: true, settings }
    return { ok: true }
  },
} })
const values = await ctrl.configItems()
for (const [key, value] of Object.entries(settings)) {
  assert.equal(values[key], value, `${key} survives a settings read`)
  assert.ok(flattenConfig().some((item) => item.key === key), `${key} is editable in /config`)
}
const beforeRead = calls.length
const refreshed = await ctrl.appearancePrefs()
assert.deepEqual(calls.slice(beforeRead).map((request) => request.cmd), ['settings_get'], 'background appearance reads do not query model status')
assert.equal(refreshed.theme_mode, settings.theme_mode)
await ctrl.configSet('density', 'comfortable')
assert.deepEqual(calls.find((r) => r.cmd === 'settings_save').settings,
  { density: 'comfortable' }, 'saving one preference cannot replace unrelated settings')

function textSnapshot(node) {
  if (node == null || typeof node === 'boolean') return ''
  if (Array.isArray(node)) return node.map(textSnapshot).join('')
  if (typeof node !== 'object') return String(node)
  if (typeof node.type === 'function') return node.type.name
  return `${node.props.content || ''}|${node.props.fg || ''}|${textSnapshot(node.props.children)}`
}
assert.equal(textSnapshot(StartupScreen({ reducedMotion: true, spinnerFrame: 0, shimmerFrame: 0 })),
  textSnapshot(StartupScreen({ reducedMotion: true, spinnerFrame: 17, shimmerFrame: 13 })),
  'reduced motion freezes both characters and shimmer colours')
const shellState = { lines: [{ text: 'draft test' }], reducedMotion: true, spinner: { active: true, frame: 0 } }
assert.equal(textSnapshot(AppShell({ state: shellState })),
  textSnapshot(AppShell({ state: { ...shellState, spinner: { active: true, frame: 13 } } })),
  'busy indicator remains static when animations are off')
assert.ok(textSnapshot(AppShell({ state: shellState })).includes('处理中'), 'static busy state retains its meaning')
console.log('opentui-appearance: ALL_PASS')
