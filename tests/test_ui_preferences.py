"""Shared appearance settings keep desktop and terminal saves compatible."""
import json

import pytest

from src import settings


@pytest.fixture
def preferences_file(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    monkeypatch.setattr(settings, '_PATH', path)
    return path


def test_legacy_file_migrates_without_writing(preferences_file):
    raw = {'appearance_dark_mode': False, 'appearance_font_scale': 1.2,
           'appearance_density': 'compact', 'desktop_inspector': False}
    preferences_file.write_text(json.dumps(raw), encoding='utf-8')
    value = settings.load()
    assert value['theme_mode'] == 'light'
    assert value['ui_font_size'] == 17
    assert value['density'] == 'compact'
    assert value['inspector_visible'] is False
    assert json.loads(preferences_file.read_text()) == raw


def test_partial_canonical_and_legacy_saves_preserve_other_choices(preferences_file):
    settings.save({'theme_mode': 'system', 'ui_font_size': 18,
                   'code_font_size': 12, 'density': 'compact', 'llm_model': 'keep'})
    settings.save({'code_wrap_long_lines': False})
    saved = settings.load()
    assert saved['theme_mode'] == 'system'
    assert saved['ui_font_size'] == 18
    assert saved['code_font_size'] == 12
    assert saved['appearance_density'] == 'compact'
    assert saved['llm_model'] == 'keep'
    settings.save({'appearance_dark_mode': True, 'desktop_inspector': False})
    assert settings.load()['theme_mode'] == 'dark'
    assert settings.load()['inspector_visible'] is False
    assert settings.load()['code_font_size'] == 12


@pytest.mark.parametrize('patch', [
    {'ui_font_size': True}, {'ui_font_size': 21}, {'code_font_size': 11},
    {'terminal_font_size': '14'}, {'theme_mode': 'auto'}, {'theme_mode': []},
    {'density': 'huge'}, {'sidebar_mode': 'invalid'},
    {'code_show_line_numbers': 1}, {'code_dark_theme': 'unavailable'},
])
def test_invalid_preference_never_writes(preferences_file, patch):
    with pytest.raises(ValueError):
        settings.save(patch)
    assert not preferences_file.exists()


def test_system_theme_and_bad_disk_values_are_safe():
    from src.ui_preferences import normalize, resolve_dark
    assert resolve_dark({'theme_mode': 'system'}, system_dark=False) is False
    assert resolve_dark({'theme_mode': 'dark'}, system_dark=False) is True
    assert normalize({'ui_font_size': [], 'theme_mode': {}})['ui_font_size'] == 14
