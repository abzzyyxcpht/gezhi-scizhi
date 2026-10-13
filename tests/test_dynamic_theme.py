"""Controls created after dark mode selection retain readable semantic colors."""
import flet as ft
from types import SimpleNamespace
from unittest import mock

from src.ui.theme import DARK, palette, style_inputs
from src.ui import brand
from src.ui.theme import repaint
from src.ui.settings_center import SettingsCenter


def test_new_input_uses_the_current_theme():
    before = palette.dark
    try:
        palette.set_dark(True)
        field = ft.TextField(label="新建研究连接")
        style_inputs(field)
        assert field.fill_color == DARK["INPUT"]
        assert field.color == DARK["TEXT"]
        assert field.label_style.color == DARK["TEXT_2"]
        assert field.hint_style.color == DARK["TEXT_3"]
        assert field.cursor_color == DARK["ACCENT"]
        assert field.selection_color == DARK["ACTIVE"]
    finally:
        palette.set_dark(before)


def test_explicit_button_disabled_colors_follow_current_theme():
    before = palette.dark
    try:
        palette.set_dark(True)
        button = ft.FilledButton("执行", style=ft.ButtonStyle())
        style_inputs(button)
        assert button.style.bgcolor[ft.ControlState.DISABLED] == DARK["SURFACE_3"]
        assert button.style.color[ft.ControlState.DISABLED] == DARK["TEXT_3"]
    finally:
        palette.set_dark(before)


def test_new_section_labels_and_markdown_remain_readable():
    before = palette.dark
    try:
        palette.set_dark(True)
        title = ft.Text("研究设置", color=brand.TEXT)
        reading = ft.Markdown("研究材料")
        style_inputs(ft.Column([title, reading]))
        assert title.color == DARK["TEXT"]
        assert reading.md_style_sheet.p_text_style.color == DARK["TEXT"]
    finally:
        palette.set_dark(before)


def test_button_state_colors_and_primary_contrast_survive_theme_switch():
    before = palette.dark
    try:
        palette.set_dark(False)
        button = ft.FilledButton("执行", style=ft.ButtonStyle(bgcolor=brand.ACCENT, color=brand.ON_ACCENT))
        style_inputs(button)
        palette.set_dark(True)
        repaint(button)
        assert button.style.bgcolor[ft.ControlState.DEFAULT] == DARK["ACCENT"]
        assert button.style.color[ft.ControlState.DEFAULT] == brand.ON_ACCENT
        assert button.style.bgcolor[ft.ControlState.DISABLED] == DARK["SURFACE_3"]
        assert button.style.color[ft.ControlState.DISABLED] == DARK["TEXT_3"]
        palette.set_dark(False)
        repaint(button)
        assert button.style.bgcolor[ft.ControlState.DEFAULT] == brand.ACCENT
        assert button.style.bgcolor[ft.ControlState.DISABLED] == brand.SURFACE_3
    finally:
        palette.set_dark(before)


def test_settings_selection_and_hover_follow_the_dark_palette():
    before = palette.dark
    try:
        palette.set_dark(True)
        with mock.patch("src.ui.settings_center.settings.load", return_value={}), \
             mock.patch.object(ft.Control, "update"):
            ui = SettingsCenter(mock.Mock(), sections={"general": [], "appearance": []})
            ui.select("appearance", update=False)
            active, inactive = ui.nav_buttons["appearance"], ui.nav_buttons["general"]
            assert active.style.bgcolor == DARK["ACTIVE"]
            assert active.content.color == DARK["ACCENT_TEXT"]
            assert inactive.content.color == DARK["TEXT_2"]
            ui._paint_nav_button(SimpleNamespace(data="true"), "general")
            assert inactive.style.bgcolor == DARK["SURFACE_3"]
            assert inactive.content.color == DARK["TEXT"]
    finally:
        palette.set_dark(before)
