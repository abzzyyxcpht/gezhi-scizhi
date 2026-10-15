// Desktop and terminal share the persisted contract; physical terminal fonts
// remain the responsibility of the terminal host, not the character renderer.
import { readFileSync } from 'node:fs'

export const UI_DESIGN = JSON.parse(readFileSync(new URL('../../../../assets/ui-design.json', import.meta.url), 'utf8'))
export const UI_DEFAULTS = Object.freeze(Object.fromEntries(
  Object.entries(UI_DESIGN.settings).map(([key, rule]) => [key, rule.default])
))

export function normalizeAppearance(source = {}) {
  const raw = source && typeof source === 'object' && !Array.isArray(source) ? { ...source } : {}
  if (raw.theme_mode === undefined && typeof raw.appearance_dark_mode === 'boolean') {
    raw.theme_mode = raw.appearance_dark_mode ? 'dark' : 'light'
  }
  if (raw.density === undefined) raw.density = raw.appearance_density
  if (raw.inspector_visible === undefined) raw.inspector_visible = raw.desktop_inspector
  if (raw.ui_font_size === undefined && [0.9, 1, 1.1, 1.2].includes(raw.appearance_font_scale)) {
    raw.ui_font_size = Math.round(UI_DESIGN.typography.base * raw.appearance_font_scale)
  }
  const result = {}
  for (const [key, rule] of Object.entries(UI_DESIGN.settings)) {
    let value = raw[key] ?? rule.default
    if (rule.options && !rule.options.includes(value)) value = rule.default
    if (typeof rule.default === 'boolean' && typeof value !== 'boolean') value = rule.default
    if (typeof rule.default === 'number') {
      if (!Number.isInteger(value) || value < rule.min || value > rule.max) value = rule.default
    }
    result[key] = value
  }
  return result
}

export function terminalGeometry(density = UI_DEFAULTS.density) {
  return density === 'compact'
    ? { contentPadding: 0, sidebarPadding: 0, panelGap: 0 }
    : { contentPadding: 1, sidebarPadding: 1, panelGap: 1 }
}
