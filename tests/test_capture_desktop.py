"""Native screenshot helpers close through the application's shutdown contract."""
import asyncio
from types import SimpleNamespace
from unittest import mock

import flet as ft
import pytest

from scripts import capture_desktop


def test_regular_capture_invokes_the_registered_native_close_callback():
    page = mock.Mock()
    page.window.on_event = mock.AsyncMock()
    page.window.destroy = mock.AsyncMock()
    asyncio.run(capture_desktop.close_capture_page(page, {}))
    page.window.on_event.assert_awaited_once()
    assert page.window.on_event.call_args.args[0].type == ft.WindowEventType.CLOSE
    page.window.destroy.assert_not_awaited()


def test_draft_capture_preserves_its_explicit_force_close_cleanup():
    page = mock.Mock()
    close = mock.AsyncMock()
    asyncio.run(capture_desktop.close_capture_page(page, {"finish_native_close": close}))
    close.assert_awaited_once_with(force=True)
    page.window.on_event.assert_not_called()


def test_capture_scrolls_activity_without_moving_the_fixed_request():
    request = ft.TextField(label="交给智能体的任务")
    activity = ft.Column([ft.ExpansionTile(title=ft.Text("执行选项"))], scroll=ft.ScrollMode.AUTO)
    fixed = ft.Column([request, activity])
    page = SimpleNamespace(controls=[fixed])
    assert capture_desktop.agent_scroller(page) is activity


def test_mode_capture_refuses_missing_native_controls():
    isolated = capture_desktop.ROOT / '_testtmp' / 'capture-safety-check'
    with pytest.raises(RuntimeError, match='helper-captured desktop controls'):
        asyncio.run(capture_desktop.prepare_workspace_mode_state(
            mock.Mock(), 'general-workspace', isolated, {}))


def test_mode_capture_refuses_settings_outside_its_isolated_directory():
    from src import settings
    sandbox = capture_desktop.ROOT / '_testtmp' / 'capture-safety-check'
    controls = {name: mock.Mock() for name in
        ('workspace_mode_control', 'agent_request', 'agent_pick', 'tools_panel')}
    with mock.patch.object(settings, '_PATH', sandbox / 'user-settings.json'):
        with pytest.raises(RuntimeError, match='inside the isolated data directory'):
            asyncio.run(capture_desktop.prepare_workspace_mode_state(
                mock.Mock(), 'general-workspace', sandbox / 'isolated', controls))
    assert not controls['workspace_mode_control'].mock_calls
