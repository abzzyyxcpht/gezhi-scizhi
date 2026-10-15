"""Renderer-independent UI contract; reading old settings never writes to disk."""
from __future__ import annotations

import json
from pathlib import Path

DESIGN = json.loads((Path(__file__).resolve().parents[1] / 'assets' / 'ui-design.json').read_text(encoding='utf-8'))
SCHEMA = DESIGN['settings']
DEFAULTS = {name: spec['default'] for name, spec in SCHEMA.items()}
ALIASES = {'density': 'appearance_density', 'inspector_visible': 'desktop_inspector'}


def _valid(value, spec):
    if 'options' in spec:
        return isinstance(value, str) and value in spec['options']
    if 'min' in spec:
        return type(value) is int and spec['min'] <= value <= spec['max']
    return type(value) is type(spec['default'])


def validate(values: dict) -> None:
    for name, spec in SCHEMA.items():
        if name in values and not _valid(values[name], spec):
            detail = (' / '.join(spec['options']) if 'options' in spec else
                      f"{spec['min']}–{spec['max']} 的整数" if 'min' in spec else '布尔值')
            raise ValueError(f'{name} 须为 {detail}')


def migrated_patch(values: dict) -> dict:
    """Make an old-client patch authoritative only for fields it actually changes."""
    result = dict(values)
    for name, legacy in ALIASES.items():
        if name not in result and legacy in result:
            result[name] = result[legacy]
    if 'theme_mode' not in result and type(result.get('appearance_dark_mode')) is bool:
        result['theme_mode'] = 'dark' if result['appearance_dark_mode'] else 'light'
    if 'ui_font_size' not in result and 'appearance_font_scale' in result:
        scale = result['appearance_font_scale']
        if type(scale) in (int, float) and scale in (.9, 1., 1.1, 1.2):
            result['ui_font_size'] = round(14 * scale)
    return result


def normalize(options: dict | None = None) -> dict:
    raw = options if isinstance(options, dict) else {}
    migrated = migrated_patch(raw)
    result = dict(raw)
    for name, spec in SCHEMA.items():
        value = migrated.get(name, spec['default'])
        result[name] = value if _valid(value, spec) else spec['default']
    for name, legacy in ALIASES.items():
        result[legacy] = result[name]
    if result['theme_mode'] != 'system':
        result['appearance_dark_mode'] = result['theme_mode'] == 'dark'
    else:
        result['appearance_dark_mode'] = raw.get('appearance_dark_mode', True) is not False
    return result


def resolve_dark(options: dict | None = None, system_dark: bool = True) -> bool:
    mode = normalize(options)['theme_mode']
    return bool(system_dark) if mode == 'system' else mode == 'dark'
