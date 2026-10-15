"""Approval previews retain source settings and accurately describe the pending edit."""
from unittest import mock

import flet as ft
import pytest

from src import settings
from src.ui.code_preview import CodePreview
from src.ui.settings_center import apply_appearance
from tests import test_desktop_navigation as navigation


@pytest.fixture
def approval_ui(tmp_path):
    fixture = navigation.DesktopNavigationTest('runTest')
    fixture.setUp()
    with mock.patch('src.tools_workspace._resolve_root', return_value=tmp_path):
        yield fixture.ui, tmp_path
    fixture.doCleanups()


def controls(node):
    yield node
    for attr in ('content', 'controls'):
        child = getattr(node, attr, None)
        for item in child if isinstance(child, list) else [child]:
            if isinstance(item, ft.Control):
                yield from controls(item)


def test_approval_diff_obeys_code_settings_and_live_changes_without_writing(approval_ui):
    ui, root = approval_ui
    path = root / 'study.py'
    path.write_text('result = 1\n', encoding='utf-8')
    saved = {**settings.load(), 'code_font_size': 18,
             'code_show_line_numbers': False, 'code_wrap_long_lines': False}
    with mock.patch.object(settings, 'load', return_value=saved):
        block = ui._approval_diff_block('session-a', 'file.write',
                                        {'path': path.name, 'content': 'result = 2\n'})
        preview = next((node for node in controls(block) if isinstance(node, CodePreview)), None)
        assert preview is not None
        assert preview.code_lines[0].size == 18
        assert not preview.rows[0].controls[0].visible
        assert preview.code_lines[0].no_wrap
        assert '+result = 2' in preview.source
        saved.update(code_font_size=16, code_show_line_numbers=True, code_wrap_long_lines=True)
        apply_appearance(block)
        assert preview.code_lines[0].size == 16
        assert preview.rows[0].controls[0].visible
        assert not preview.code_lines[0].no_wrap
    assert path.read_text(encoding='utf-8') == 'result = 1\n'


def test_open_approval_dialog_updates_from_external_preferences(approval_ui):
    ui, root = approval_ui
    path = root / 'study.py'
    path.write_text('result = 1\n', encoding='utf-8')
    ui.render_approval({'id': 'approval-appearance', 'tool': 'file.write',
                       'capability': 'write', 'arguments': {'path': path.name, 'content': 'result = 2\n'}},
                       'session-a')
    preview = next(node for node in controls(ui.agent_approval_card) if isinstance(node, CodePreview))
    before = preview.bgcolor
    content = ui.agent_approval_card.content
    saved = {**settings.load(), 'theme_mode': 'dark', 'code_font_size': 18,
             'code_show_line_numbers': False, 'code_wrap_long_lines': False}
    with mock.patch.object(settings, 'load', return_value=saved):
        assert ui.refresh_appearance_preferences()
    assert ui.agent_approval_card.content is content
    assert ui.agent_state['approval_open_id'] == 'approval-appearance'
    assert ui.agent_state['approval_session'] == 'session-a'
    assert preview.code_lines[0].size == 18
    assert preview.code_lines[0].no_wrap
    assert not preview.rows[0].controls[0].visible
    assert preview.bgcolor != before
    assert path.read_text(encoding='utf-8') == 'result = 1\n'


@pytest.mark.parametrize('tool, arguments, reason', [
    ('file.edit', {'start_line': 0, 'end_line': 1, 'new_text': 'changed'}, '行区间'),
    ('file.str_replace', {'old_str': 'repeat', 'new_str': 'changed'}, '2 次'),
    ('file.str_replace', {'old_str': '', 'new_str': 'changed'}, '不能为空'),
])
def test_invalid_edit_does_not_display_an_operation_that_cannot_execute(approval_ui, tool, arguments, reason):
    ui, root = approval_ui
    (root / 'study.txt').write_text('repeat\nrepeat\n', encoding='utf-8')
    block = ui._approval_diff_block('session-a', tool, {'path': 'study.txt', **arguments})
    assert isinstance(block, ft.Text)
    assert reason in block.value


def test_long_approval_diff_discloses_omitted_changes(approval_ui):
    ui, _ = approval_ui
    block = ui._approval_diff_block('session-a', 'file.write',
                                    {'path': 'new.txt', 'content': '\n'.join(map(str, range(300)))})
    preview = next((node for node in controls(block) if isinstance(node, CodePreview)), None)
    assert preview is not None
    assert len(preview.source.splitlines()) == 260
    assert any('仅展示前 260 行' in str(getattr(node, 'value', '')) for node in controls(block))
