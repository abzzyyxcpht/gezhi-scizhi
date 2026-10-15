import asyncio
from types import SimpleNamespace
from unittest import mock

import flet as ft
import pytest

from src import settings
from src.ui import theme
from tests import test_desktop_navigation as navigation


@pytest.fixture
def desk():
    fixture = navigation.DesktopNavigationTest('runTest')
    fixture.setUp()
    yield fixture.ui, fixture.page
    fixture.doCleanups()


def test_system_theme_responds_without_replacing_draft(desk):
    ui, page = desk
    ui.agent_request.value = '保留我尚未发出的研究问题'
    saved = {**settings.load(), 'theme_mode': 'system', 'ui_font_size': 18}
    with mock.patch.object(settings, 'load', return_value=saved):
        page.platform_brightness = ft.Brightness.LIGHT
        ui.workspace_settings_saved()
        assert page.theme_mode == ft.ThemeMode.SYSTEM
        assert theme.palette.dark is False
        page.platform_brightness = ft.Brightness.DARK
        page.on_platform_brightness_change(None)
        assert theme.palette.dark is True
    assert ui.agent_request.value == '保留我尚未发出的研究问题'


def test_overlay_navigation_reachable_and_close_keeps_input(desk):
    ui, page = desk
    ui.agent_request.value = 'pending research'
    saved = {**settings.load(), 'sidebar_mode': 'overlay'}
    with mock.patch.object(settings, 'load', return_value=saved):
        ui.workspace_settings_saved()
        assert not ui.sidebar.visible
        assert ui.navigation_toggle.visible
        page.show_drawer = mock.AsyncMock()
        page.close_drawer = mock.AsyncMock()
        asyncio.run(ui.open_navigation())
        assert ui.layout['navigation_open']
        asyncio.run(ui.drawer_select(SimpleNamespace(control=SimpleNamespace(selected_index=5))))
        assert not ui.layout['navigation_open']
    assert ui.agent_request.value == 'pending research'


def test_external_preferences_apply_once_without_rebuilding_session(desk):
    ui, page = desk
    ui.agent_request.value = 'unsent'
    ui.settings_center.select('appearance')
    saved = {**settings.load(), 'ui_font_size': 16, 'inspector_visible': False}
    with mock.patch.object(settings, 'load', return_value=saved):
        assert ui.refresh_appearance_preferences()
        assert not ui.refresh_appearance_preferences()
    assert ui.agent_request.value == 'unsent'
    assert ui.settings_center.forms['appearance']['ui_font_size'].value == '16'
