"""Behavioral checks for the shared desktop appearance contract."""
import json
from pathlib import Path

import flet as ft
import msgpack
import pytest
from flet.messaging.protocol import configure_encode_object_for_msgpack

from src.ui import brand, theme


@pytest.fixture(autouse=True)
def restore_theme():
    old_dark = theme.palette.dark
    theme.configure_typography({})
    yield
    theme.configure_typography({})
    theme.palette.set_dark(old_dark)


def test_semantic_sizes_change_only_text_and_code_sizes_are_independent():
    theme.configure_typography({'ui_font_size': 14, 'code_font_size': 17,
                                'terminal_font_size': 12})
    assert [theme.ui_size(role) for role in ('xl', 'lg', 'base', 'caption', 'sm', 'xs')] == [18, 16, 14, 13, 12, 10]
    body = ft.Text('研究工作台', size=theme.ui_size('lg'))
    code = ft.Text('file.read()', font_family='Consolas', size=11)
    terminal = theme.mark_typography(ft.Text('运行中', size=11), 'terminal')
    field = ft.TextField(width=320, height=40, text_size=14)
    tree = ft.Column([body, code, terminal, field], spacing=12)
    theme.style_inputs(tree)
    assert (body.size, code.size, terminal.size) == (16, 17, 12)
    theme.configure_typography({'ui_font_size': 18, 'code_font_size': 17,
                                'terminal_font_size': 12})
    theme.style_inputs(tree)
    theme.style_inputs(tree)
    assert (body.size, code.size, terminal.size, field.text_size) == (20, 17, 12, 18)
    assert (tree.spacing, field.width, field.height) == (12, 320, 40)
    theme.configure_typography({'ui_font_size': 14, 'code_font_size': 17,
                                'terminal_font_size': 12})
    theme.style_inputs(tree)
    assert body.size == 16


def test_dynamic_text_and_markdown_adopt_current_preferences():
    theme.configure_typography({'ui_font_size': 16, 'code_font_size': 19,
                                'code_dark_theme': 'atom-one-dark'})
    theme.palette.set_dark(True)
    message = ft.Markdown('正文\n\n```python\nprint(1)\n```')
    title = ft.Text('证据', size=18)
    field = ft.TextField(label='研究问题')
    theme.style_inputs(ft.Column([message, title, field]))
    assert title.size == 20
    assert field.text_size == 16
    assert message.md_style_sheet.p_text_style.size == 16
    assert message.md_style_sheet.code_text_style.size == 19
    assert message.code_style_sheet.code_text_style.size == 19
    assert message.code_theme == ft.MarkdownCodeTheme.ATOM_ONE_DARK
    theme.configure_typography({'ui_font_size': 14, 'code_font_size': 12,
                                'code_light_theme': 'atom-one-light'})
    theme.palette.set_dark(False)
    theme.repaint(message)
    assert message.md_style_sheet.p_text_style.size == 14
    assert message.md_style_sheet.code_text_style.size == 12
    assert message.code_theme == ft.MarkdownCodeTheme.ATOM_ONE_LIGHT


def test_shared_geometry_and_reduced_motion_are_used_by_real_themes():
    contract = json.loads((Path(__file__).parents[1] / 'assets/ui-design.json').read_text(encoding='utf-8'))
    theme.configure_typography({'ui_font_size': 16, 'ui_animations': True})
    built = theme.build_theme()
    assert theme.radius('control') == contract['radius']['control']
    assert theme.motion_duration('panel') == contract['motion']['panel_ms']
    assert theme.motion_duration('feedback') == contract['motion']['feedback_ms']
    assert built.outlined_button_theme.style.text_style.size == 16
    assert built.text_button_theme.style.text_style.size == 16
    assert built.dialog_theme.title_text_style.size == 20
    assert built.popup_menu_theme.label_text_style.size == 16
    assert built.outlined_button_theme.style.side[ft.ControlState.FOCUSED].color == theme.palette.ACCENT_TEXT
    theme.configure_typography({'ui_font_size': 16, 'ui_animations': False})
    assert theme.motion_duration('panel') == 0
    assert theme.motion_duration('feedback') == 0


def test_equal_color_values_keep_semantics_across_repeated_theme_switches():
    theme.palette.set_dark(False)
    controls = {role: ft.Container(bgcolor=theme.palette.value(role))
                for role in ('SURFACE', 'INPUT', 'CARD', 'MENU', 'POPOVER', 'PRIMARY')}
    text = ft.Text('执行', color=theme.palette.ON_PRIMARY)
    white_label = ft.Text('状态', color=theme.palette.ON_ACCENT)
    for dark in (True, True, False, False, True, False):
        theme.palette.set_dark(dark)
        theme.repaint(*controls.values(), text, white_label)
        for role, control in controls.items():
            assert control.bgcolor == theme.palette.value(role), role
        assert text.color == theme.palette.ON_PRIMARY
        assert white_label.color == theme.palette.ON_ACCENT
    assert controls['INPUT'].bgcolor == brand.INPUT


def test_marked_color_roles_survive_json_serialization_and_dark_start():
    theme.palette.set_dark(True)
    surface = ft.Container(bgcolor=theme.palette.SURFACE)
    card = ft.Container(bgcolor=theme.palette.CARD)
    assert json.loads(json.dumps({'color': card.bgcolor})) == {'color': theme.DARK['CARD']}
    card.content = ft.Text('代码', size=theme.ui_size('base'))
    encoded = msgpack.packb(card, default=configure_encode_object_for_msgpack(ft.Control))
    rendered = msgpack.unpackb(encoded)
    assert rendered['bgcolor'] == theme.DARK['CARD']
    assert rendered['content']['size'] == 14
    theme.repaint(surface, card)
    assert surface.bgcolor == theme.DARK['SURFACE']
    assert card.bgcolor == theme.DARK['CARD']
    theme.palette.set_dark(False)
    theme.repaint(surface, card)
    assert surface.bgcolor == brand.SURFACE
    assert card.bgcolor == brand.CARD


def test_navigation_indicators_and_nested_icons_follow_theme_round_trips():
    theme.palette.set_dark(False)
    icon = ft.Icon(ft.Icons.FOLDER, color=theme.palette.TEXT_2)
    drawer = ft.NavigationDrawer(
        controls=[ft.NavigationDrawerDestination(label='研究项目', icon=icon)],
        bgcolor=theme.palette.SURFACE, indicator_color=theme.palette.ACTIVE)
    rail = ft.NavigationRail(destinations=[], indicator_color=theme.palette.SELECTED)
    bar = ft.NavigationBar(destinations=[], indicator_color=theme.palette.ACTIVE,
                           overlay_color={ft.ControlState.HOVERED: theme.palette.HOVER})
    for dark in (True, False, True):
        theme.palette.set_dark(dark)
        theme.repaint(drawer, rail, bar)
        assert drawer.indicator_color == theme.palette.ACTIVE
        assert rail.indicator_color == theme.palette.SELECTED
        assert bar.indicator_color == theme.palette.ACTIVE
        assert bar.overlay_color[ft.ControlState.HOVERED] == theme.palette.HOVER
        assert icon.color == theme.palette.TEXT_2


def test_explicit_menu_surfaces_and_primary_styles_follow_theme():
    theme.palette.set_dark(False)
    menu = ft.SubmenuButton(
        content=ft.Text('导出'), menu_style=ft.MenuStyle(
            bgcolor={ft.ControlState.DEFAULT: theme.palette.MENU},
            side=ft.BorderSide(1, theme.palette.BORDER)),
        style=ft.ButtonStyle(bgcolor=theme.palette.PRIMARY, color=theme.palette.ON_PRIMARY))
    menubar = ft.MenuBar(controls=[menu], style=ft.MenuStyle(bgcolor=theme.palette.CARD))
    dropdown = ft.Dropdown(menu_style=ft.MenuStyle(bgcolor=theme.palette.MENU),
                           hover_color=theme.palette.HOVER)
    for dark in (True, False):
        theme.palette.set_dark(dark)
        theme.repaint(menubar, dropdown)
        assert menu.menu_style.bgcolor[ft.ControlState.DEFAULT] == theme.palette.MENU
        assert menu.menu_style.side.color == theme.palette.BORDER
        assert menu.style.bgcolor == theme.palette.PRIMARY
        assert menu.style.color == theme.palette.ON_PRIMARY
        assert menubar.style.bgcolor == theme.palette.CARD
        assert dropdown.menu_style.bgcolor == theme.palette.MENU
        assert dropdown.hover_color == theme.palette.HOVER
