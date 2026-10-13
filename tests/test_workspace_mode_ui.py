"""Exercise mode switching callbacks without suppliers or real workspace data."""
from types import SimpleNamespace
from unittest import mock

import pytest

from src.ui.workspace_mode import WorkspaceModeControl


def control():
    page = mock.Mock()
    page.run_thread.side_effect = lambda work: work()
    changed = mock.Mock()
    api = mock.Mock(side_effect=lambda req: {'ok': True, 'mode': req['mode']})
    ui = WorkspaceModeControl(page, mode='research', on_changed=changed, api=api)
    return ui, page, api, changed


def test_button_saves_mode_before_changing_visible_selection():
    ui, page, api, changed = control()
    pending = []
    page.run_thread.side_effect = pending.append
    ui.buttons['general'].on_click(None)
    assert ui.mode == 'research'
    assert ui.saving
    assert all(button.disabled for button in ui.buttons.values())
    changed.assert_not_called()
    pending.pop()()
    assert api.call_args.args[0] == {'cmd': 'workbench_mode', 'mode': 'general', 'expected_revision': ui.revision}
    assert ui.mode == 'general'
    changed.assert_called_once_with('general')
    assert not ui.saving


def test_failure_and_repeated_click_do_not_change_mode():
    ui, _, api, changed = control()
    ui.buttons['research'].on_click(None)
    api.assert_not_called()
    api.return_value = {'ok': False, 'error': '磁盘不可写'}
    api.side_effect = None
    ui.toggle()
    assert ui.mode == 'research'
    assert '磁盘不可写' in ui.notice.value
    changed.assert_not_called()
    assert not ui.saving


def test_mode_buttons_expose_native_pointer_and_keyboard_states():
    import flet as ft

    ui, _, _, _ = control()
    # wrap=True 搭配 expand=True 按钮是非法组合：Flutter Wrap 不容纳 Expanded，
    # 该子树布局中止会拖垮同列后续控件（2026-10-10 桌面侧栏卡点根因）。
    # 窄宽由 resize() 的 50px 图标模式覆盖，模式行不换行。
    assert not ui.mode_row.wrap
    assert all(button.expand is True for button in ui.buttons.values())
    for key, button in ui.buttons.items():
        style = button.style
        assert style.animation_duration == ui.TRANSITION_MS
        for state in (ft.ControlState.DEFAULT, ft.ControlState.HOVERED,
                      ft.ControlState.PRESSED, ft.ControlState.FOCUSED,
                      ft.ControlState.DISABLED):
            assert state in style.color, key
            assert state in style.icon_color, key
            assert state in style.bgcolor, key
            assert state in style.side, key
        assert ft.ControlState.HOVERED in style.overlay_color
        assert ft.ControlState.PRESSED in style.overlay_color
        assert ft.ControlState.FOCUSED in style.overlay_color


def test_mode_switch_compacts_to_icons_then_restores_labels():
    ui, _, _, _ = control()
    ui.resize(140)
    assert ui.compact
    assert all(button.content == '' and button.width == 50 and button.expand is None
               for button in ui.buttons.values())
    assert all('Ctrl+G' in str(button.tooltip) for button in ui.buttons.values())
    ui.resize(180)
    assert not ui.compact
    assert all(button.content == {'general': '通用', 'research': '科研'}[key]
               and button.expand is True and button.width is None
               for key, button in ui.buttons.items())


def test_stale_mode_save_preserves_selection_and_retry_uses_new_revision():
    ui, _, api, changed = control()
    api.side_effect = [
        {'ok': False, 'code': 'stale_revision', 'error': '工作台模式已被其他窗口修改，请刷新后重试', 'revision': 8},
        {'ok': True, 'mode': 'general', 'revision': 9},
    ]
    ui.toggle()
    assert ui.mode == 'research'
    assert ui.revision == 8
    assert '重试' in ui.notice.value
    changed.assert_not_called()
    ui.toggle()
    assert api.call_args.args[0]['expected_revision'] == 8
    assert ui.mode == 'general'
    assert ui.revision == 9


@pytest.fixture
def desktop_ui(tmp_path):
    from tests import test_desktop_navigation
    from src import settings
    with mock.patch.object(settings, '_PATH', tmp_path / 'settings.json'):
        harness = test_desktop_navigation.DesktopNavigationTest('runTest')
        harness.setUp()
        try:
            yield harness.ui, harness.page
        finally:
            harness.doCleanups()


def test_desktop_switch_preserves_draft_and_opens_general_workspace(desktop_ui):
    ui, _ = desktop_ui
    ui.agent_request.value = '保留尚未提交的文字'
    ui.agent_request.on_change(SimpleNamespace())
    ui.workspace_mode_control.buttons['general'].on_click(None)
    assert ui.agent_request.value == '保留尚未提交的文字'
    assert ui.agent_pick.value == 'general'
    assert ui.nav_index['i'] == 4
    assert '代码' in ui.agent_request.hint_text


def test_desktop_mode_chrome_callback_can_defer_its_repaint(desktop_ui):
    ui, page = desktop_ui
    page.update.reset_mock()
    ui.workspace_mode_changed('general', navigate=False, set_draft_agent=False, update=False)
    # The switch's worker owns the final frame; chrome mutation alone must not
    # force a second redraw when a caller opts into the deferred path.
    page.update.assert_not_called()


def test_keyboard_switch_and_saved_session_role_remain_separate(desktop_ui):
    ui, page = desktop_ui
    ui.agent_state.update(session_id='saved-research', status='done', workspace_agent='research')
    ui.agent_pick.value = 'research'
    page.on_keyboard_event(SimpleNamespace(key='G', ctrl=True, alt=False, shift=False))
    assert ui.workspace_mode_control.mode == 'general'
    assert ui.agent_state['session_id'] == 'saved-research'
    assert ui.agent_pick.value == 'research'
    assert '科研' in ui.workspace_session_note.value
    assert '新建会话' in ui.workspace_session_note.value


def test_mode_command_syncs_sidebar_and_custom_shortcut_is_respected(desktop_ui):
    from src import settings
    ui, page = desktop_ui
    ui.tools_panel.command.value = '/mode general'
    ui.tools_panel.run_command()
    assert ui.workspace_mode_control.mode == 'general'
    assert ui.agent_pick.value == 'general'
    settings.save({'keyboard_shortcuts': {'workspace_mode': 'Alt+G'}})
    page.on_keyboard_event(SimpleNamespace(key='G', ctrl=True, alt=False, shift=False))
    assert ui.workspace_mode_control.mode == 'general'
    page.on_keyboard_event(SimpleNamespace(key='G', ctrl=False, alt=True, shift=False))
    assert ui.workspace_mode_control.mode == 'research'


def test_example_keeps_existing_input_and_general_hides_research_onboarding(desktop_ui):
    ui, _ = desktop_ui
    ui.workspace_mode_control.buttons['general'].on_click(None)
    assert not ui.onboarding.content.visible
    assert not ui.nav_buttons[2].visible
    assert ui.nav_buttons[7].visible
    ui.agent_request.value = '未提交内容'
    ui.workspace_examples.controls[0].on_click(SimpleNamespace())
    assert ui.agent_request.value == '未提交内容'


def test_mode_is_discoverable_by_its_chinese_label(desktop_ui):
    import flet as ft
    ui, _ = desktop_ui
    ui.tools_panel.command.value = '模式'
    ui.tools_panel.suggest()
    matches = [button.content.value for button in ui.tools_panel.suggestions.controls
               if isinstance(button, ft.TextButton)]
    assert any('/mode ' in label for label in matches)


def test_new_project_draft_and_fallback_use_the_general_role(desktop_ui):
    ui, _ = desktop_ui
    ui.workspace_mode_control.buttons['general'].on_click(None)
    ui.project_changed({'id': 'new-general-project', 'name': '新项目', 'view': 'chat'})
    assert ui.agent_pick.value == 'general'
    ui.state['project_id'] = 'fallback-general-project'
    ui.open_project_draft()
    assert ui.agent_pick.value == 'general'


def test_startup_refresh_keeps_an_existing_specialist_draft(desktop_ui):
    ui, _ = desktop_ui
    ui.agent_pick.value = 'stats'
    ui.agent_request.value = '尚未发送的数据分析请求'
    ui.drafts.capture()
    ui.drafts.flush()
    ui.workspace_mode_changed('research', set_draft_agent=False)
    assert ui.agent_pick.value == 'stats'
    assert ui.agent_request.value == '尚未发送的数据分析请求'


def test_saved_workspace_purpose_controls_specialist_session_chrome(desktop_ui):
    ui, _ = desktop_ui
    ui.agent_state.update(session_id='general-explore', status='done',
                          workspace_agent='explore', workspace_mode='general')
    ui.agent_pick.value = 'explore'
    ui.refresh_workspace_context()
    assert '通用' in ui.workspace_title.value
    assert '当前会话：通用' in ui.workspace_session_note.value
    assert not ui.evidence_button.visible
    assert not ui.onboarding.content.visible


def test_selecting_specialist_draft_reads_the_saved_purpose_from_the_journal(desktop_ui):
    ui, _ = desktop_ui
    store = ui.get_runtime().sessions
    store.append('saved-general-specialist', 'session_created',
                 {'agent': 'explore', 'workspace_mode': 'general'})
    ui.select_agent_session('saved-general-specialist')
    assert ui.agent_state['workspace_mode'] == 'general'
    assert ui.agent_pick.value == 'explore'
    assert '当前会话：通用' in ui.workspace_session_note.value


def test_selected_draft_keeps_its_saved_purpose_and_role_when_default_changes(desktop_ui):
    ui, _ = desktop_ui
    ui.workspace_mode_control.buttons['general'].on_click(None)
    ui.agent_state.update(session_id='saved-general-draft', status='draft',
                          workspace_agent='explore', workspace_mode='general')
    ui.agent_pick.value = 'explore'
    ui.agent_request.value = '未提交的项目问题'
    ui.workspace_mode_control.buttons['research'].on_click(None)
    assert ui.agent_pick.value == 'explore'
    assert ui.agent_state['workspace_mode'] == 'general'
    assert '当前会话：通用' in ui.workspace_session_note.value
    assert '新建会话使用科研' in ui.workspace_session_note.value
    assert ui.agent_request.value == '未提交的项目问题'
