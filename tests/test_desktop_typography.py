"""Native typography, state color and shortcut contracts."""
from __future__ import annotations

from unittest import mock

import flet as ft
import pytest

from src import settings
from src.ui import brand
from src.ui import theme
from src.ui.theme import DARK, build_theme, markdown_styles, palette, style_inputs
from src.ui.workspace_mode import WorkspaceModeControl


@pytest.fixture(autouse=True)
def reset_typography():
    original_dark = palette.dark
    if hasattr(theme, 'configure_typography'):
        theme.configure_typography({})
    yield
    if hasattr(theme, 'configure_typography'):
        theme.configure_typography({})
    palette.set_dark(original_dark)


def test_font_preferences_have_defaults_and_reject_unknown_values():
    assert settings._DEFAULTS['appearance_font_family'] == 'system'
    assert settings._DEFAULTS['appearance_code_font'] == 'cascadia'
    settings._validate({'appearance_font_family': 'yahei', 'appearance_code_font': 'consolas'})
    for patch in ({'appearance_font_family': 'comic'}, {'appearance_code_font': 'unknown'},
                  {'appearance_font_family': []}, {'appearance_code_font': {}}):
        with pytest.raises(ValueError):
            settings._validate(patch)


def test_selected_fonts_and_fallbacks_reach_theme_and_markdown():
    theme.configure_typography({'appearance_font_family': 'yahei', 'appearance_code_font': 'consolas'})
    app_theme = build_theme(dark=False)
    sheet = markdown_styles(dark=False)
    assert app_theme.font_family == 'Microsoft YaHei UI'
    assert app_theme.text_theme.body_medium.font_family_fallback == ['PingFang SC', 'Noto Sans CJK SC']
    assert sheet.p_text_style.font_family == 'Microsoft YaHei UI'
    assert sheet.code_text_style.font_family == 'Consolas'
    assert 'Microsoft YaHei UI' in sheet.code_text_style.font_family_fallback


def test_preview_font_choices_do_not_change_the_saved_typography():
    theme.configure_typography({'appearance_font_family': 'yahei', 'appearance_code_font': 'consolas'})
    assert theme.body_font_family('system') == 'Segoe UI'
    assert theme.code_font_family('cascadia') == 'Cascadia Code'
    assert theme.body_font_family([]) == 'Segoe UI'
    assert theme.code_font_family({}) == 'Cascadia Code'
    assert theme.body_font_family() == 'Microsoft YaHei UI'
    assert theme.code_font_family() == 'Consolas'


def test_incremental_tool_rows_use_current_code_font_and_scale():
    from src.ui.session_composer import SessionComposer
    from src.ui.terminal_panel import TerminalPanel
    theme.configure_typography({'appearance_code_font': 'cascadia', 'appearance_font_scale': 1.2})
    events = []
    page = mock.MagicMock()
    composer = SessionComposer(page, ft.TextField(), lambda: 'font-session', lambda: '',
                               lambda sid: None, lambda: None, events_provider=lambda sid: events)
    composer._rendered_sid = 'font-session'
    for number in range(2):
        events.append({'type': 'tool_call', 'payload': {'id': str(number), 'name': 'file.read',
                                                     'arguments': {'path': '研究记录.md'}}})
        composer.consume_events()
        for tile, title, _ in composer.tool_rows.values():
            assert title.font_family == 'Cascadia Code'
            assert title.size == pytest.approx(13.2)
            assert tile.controls[0].font_family == 'Cascadia Code'
            assert tile.controls[0].size == pytest.approx(13.2)
    terminal = TerminalPanel(page, lambda: None, lambda: '')
    line = terminal._emit_line('研究记录', palette.TEXT)
    assert line.font_family == 'Cascadia Code'
    assert line.size == pytest.approx(13.2)


def test_markdown_scale_is_reversible_and_unknown_values_fall_back():
    base = markdown_styles()
    assert base.p_text_style.size == 15
    assert base.p_text_style.height == 1.6
    theme.configure_typography({'appearance_font_scale': 1.2})
    assert markdown_styles().p_text_style.size == 18
    assert markdown_styles().h1_text_style.size == pytest.approx(base.h1_text_style.size * 1.2)
    theme.configure_typography({'appearance_font_scale': 1.2})
    assert markdown_styles().p_text_style.size == 18
    theme.configure_typography({'appearance_font_scale': 1.0, 'appearance_font_family': 'bogus',
                          'appearance_code_font': 'bogus'})
    restored = markdown_styles()
    assert restored.p_text_style.size == 15
    assert restored.p_text_style.font_family == 'Segoe UI'
    assert restored.code_text_style.font_family == 'Cascadia Code'
    theme.configure_typography({'appearance_font_family': [], 'appearance_code_font': {}})
    assert markdown_styles().p_text_style.font_family == 'Segoe UI'
    assert markdown_styles().code_text_style.font_family == 'Cascadia Code'


def test_dynamic_markdown_uses_current_scale_and_survives_theme_change():
    theme.configure_typography({'appearance_font_scale': 1.1, 'appearance_code_font': 'consolas'})
    reading = ft.Markdown('科研内容')
    palette.set_dark(True)
    style_inputs(reading)
    assert reading.md_style_sheet.p_text_style.size == 16.5
    assert reading.md_style_sheet.code_text_style.font_family == 'Consolas'
    assert reading.md_style_sheet.p_text_style.color == DARK['TEXT']
    palette.set_dark(False)
    from src.ui.theme import repaint
    repaint(reading)
    assert reading.md_style_sheet.p_text_style.size == 16.5
    assert reading.md_style_sheet.p_text_style.color == brand.TEXT


def test_switch_and_icon_button_have_theme_aware_interaction_states():
    for dark in (False, True):
        theme = build_theme(dark=dark)
        switch = theme.switch_theme
        icon = theme.icon_button_theme.style
        for style in (switch.overlay_color, icon.overlay_color):
            assert ft.ControlState.HOVERED in style
            assert ft.ControlState.FOCUSED in style
        assert switch.thumb_color[ft.ControlState.DISABLED] == (DARK['TEXT_3'] if dark else brand.TEXT_3)
        assert icon.icon_color[ft.ControlState.DISABLED] == (DARK['TEXT_3'] if dark else brand.TEXT_3)
        assert icon.bgcolor[ft.ControlState.HOVERED] == (DARK['HOVER'] if dark else brand.HOVER)


def test_workspace_mode_overlays_use_flet_argb_and_shortcuts_refresh():
    page = mock.Mock()
    with mock.patch.object(settings, 'load', return_value={'workspace_mode_revision': 0, 'keyboard_shortcuts': {}}):
        control = WorkspaceModeControl(page, api=mock.Mock())
    button = control.buttons['general']
    assert button.style.overlay_color == {
        ft.ControlState.HOVERED: '#12FFFFFF',
        ft.ControlState.PRESSED: '#18000000',
        ft.ControlState.FOCUSED: '#10FFFFFF',
    }
    with mock.patch.object(settings, 'load', return_value={
            'workspace_mode_revision': 0, 'keyboard_shortcuts': {'workspace_mode': 'Alt+G'}}):
        control.refresh_shortcuts()
    assert 'Alt+G' in button.tooltip
    assert 'Alt+G' in control.notice.value
    control.notice.value = '模式未切换：保存失败'
    control.refresh_shortcuts()
    assert control.notice.value == '模式未切换：保存失败'
