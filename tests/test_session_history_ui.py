from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft
import pytest
from src.ui.session_history import SessionHistoryDialog


def fixture():
    work, selected = [], {'sid': 'one'}
    page = SimpleNamespace(update=Mock(), run_thread=work.append, show_dialog=Mock(), pop_dialog=Mock())
    def api(request):
        query = request.get('query', '')
        return {'ok': True, 'history': {'session_id': 'one', 'query': query, 'messages': [
            {'id': 1, 'role': 'user', 'content': query or 'first', 'truncated': False}],
            'snapshot': query or 'snapshot', 'generation': 'generation', 'page': 1, 'pages': 1,
            'page_size': 40, 'total': 1, 'new_messages': False, 'reset': False}}
    view = SessionHistoryDialog(page, 'one', lambda: selected['sid'], lambda role, text: ft.Text(text), api=api)
    view.open()
    return view, work, selected


def test_search_ignores_older_request_that_finishes_late():
    view, work, _ = fixture()
    first = work.pop(0)
    view.query.value = 'latest'
    view.load(search=True)
    work.pop(0)()
    first()
    assert view.rows.controls[0].value == 'latest'


def test_switch_session_or_close_prevents_late_render():
    view, work, selected = fixture()
    selected['sid'] = 'two'
    work.pop(0)()
    assert not view.rows.controls
    selected['sid'] = 'one'
    view.load()
    view.close()
    work.pop(0)()
    assert not view.rows.controls


def paged_history(page=2):
    return {'ok': True, 'history': {'session_id': 'one', 'query': '', 'messages': [
        {'id': page, 'role': 'user', 'content': f'page {page}', 'truncated': False}],
        'snapshot': 'original-snapshot', 'generation': 'generation', 'page': page,
        'pages': 3, 'page_size': 40, 'total': 100, 'new_messages': False, 'reset': False}}


@pytest.mark.parametrize('failure', [{'ok': False, 'error': 'temporary failure'}, OSError('busy')])
def test_failed_page_keeps_history_snapshot_and_restores_navigation(failure):
    view, work, _ = fixture()
    view.api = Mock(side_effect=[paged_history(), failure, paged_history(3)])
    work.pop(0)()
    original = view.rows.controls[0]
    view.move(1)
    assert view.previous.disabled and view.following.disabled
    work.pop(0)()
    assert view.rows.controls[0] is original
    assert view.current['page'] == 2 and view.snapshot == 'original-snapshot'
    assert not view.previous.disabled and not view.following.disabled
    view.move(1)
    work.pop(0)()
    assert view.rows.controls[0].value == 'page 3'
    assert view.api.call_args.args[0]['snapshot'] == 'original-snapshot'
    assert view.following.disabled and not view.previous.disabled


def test_failed_refresh_keeps_committed_history_snapshot():
    view, work, _ = fixture()
    view.api = Mock(side_effect=[paged_history(), {'ok': False, 'error': 'busy'}, paged_history(3)])
    work.pop(0)()
    view.load(search=True)
    work.pop(0)()
    assert 'snapshot' not in view.api.call_args.args[0]
    assert view.snapshot == 'original-snapshot'
    assert not view.previous.disabled and not view.following.disabled
    view.move(1)
    work.pop(0)()
    assert view.api.call_args.args[0]['snapshot'] == 'original-snapshot'


def chunk(offset=0):
    return {'ok': True, 'message': {'session_id': 'one', 'message_id': 7,
        'generation': 'generation', 'content': f'chunk {offset}', 'offset': offset,
        'next_offset': offset + 20000 if offset < 40000 else None, 'characters': 60000}}


def message_fixture(replies):
    view, work, selected = fixture()
    work.pop(0)()
    view.api = Mock(side_effect=replies)
    view.show_message(7, 'generation')
    dialog = view.page.show_dialog.call_args.args[0]
    content = dialog.content.content.controls[0]
    buttons = {control.content: control for control in dialog.actions if isinstance(control, ft.TextButton)}
    return view, work, selected, dialog, content, buttons


@pytest.mark.parametrize('failure', [{'ok': False, 'error': 'temporary failure'}, OSError('busy')])
def test_long_message_failed_next_chunk_preserves_text_and_retries_failed_offset(failure):
    view, work, _, dialog, content, buttons = message_fixture([chunk(), failure, chunk(20000)])
    work.pop(0)()
    buttons['下一段'].on_click(None)
    assert buttons['上一段'].disabled and buttons['下一段'].disabled
    work.pop(0)()
    assert content.value == 'chunk 0'
    assert buttons['上一段'].disabled and not buttons['下一段'].disabled
    retry = buttons['重试读取']
    assert retry.visible and not retry.disabled
    retry.on_click(None)
    assert retry.disabled and buttons['下一段'].disabled
    work.pop(0)()
    assert view.api.call_args.args[0]['offset'] == 20000
    assert content.value == 'chunk 20000'
    assert not buttons['上一段'].disabled and not buttons['下一段'].disabled
    assert not retry.visible


def test_long_message_initial_failure_can_be_retried_without_reopening():
    view, work, _, _, content, buttons = message_fixture([{'ok': False, 'error': 'busy'}, chunk()])
    work.pop(0)()
    assert content.value == ''
    assert buttons['上一段'].disabled and buttons['下一段'].disabled
    buttons['重试读取'].on_click(None)
    work.pop(0)()
    assert content.value == 'chunk 0'
    assert view.api.call_args.args[0]['offset'] == 0


@pytest.mark.parametrize('change', ['session', 'dismiss', 'parent'])
def test_long_message_late_failure_does_not_change_an_inactive_view(change):
    view, work, selected, dialog, content, buttons = message_fixture([chunk(), OSError('late failure')])
    work.pop(0)()
    buttons['下一段'].on_click(None)
    if change == 'session':
        selected['sid'] = 'two'
    elif change == 'dismiss':
        dialog.on_dismiss(None)
    else:
        view.dismiss()
    label = dialog.actions[0].value
    updates = view.page.update.call_count
    work.pop(0)()
    assert dialog.actions[0].value == label
    assert content.value == 'chunk 0'
    assert view.page.update.call_count == updates


def test_long_message_older_failure_cannot_replace_newer_success():
    view, work, _, dialog, content, buttons = message_fixture([
        chunk(), chunk(20000), OSError('old request failure')])
    work.pop(0)()
    buttons['下一段'].on_click(None)
    old_request = work.pop(0)
    buttons['下一段'].on_click(None)
    work.pop(0)()
    label = dialog.actions[0].value
    old_request()
    assert content.value == 'chunk 20000'
    assert dialog.actions[0].value == label
    assert not buttons['上一段'].disabled and not buttons['下一段'].disabled
