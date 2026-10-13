"""Native output browsing uses bounded, isolated requests and rejects stale replies."""
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft
import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    from src import config, settings
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(settings, '_PATH', tmp_path / 'settings.json')


def item(output_id='one', total_bytes=100):
    return {'output_id': output_id, 'tool': 'file.read', 'call_id': 'call-' + output_id,
            'kind': 'tool_result', 'total_bytes': total_bytes, 'sha256': 'a' * 64,
            'created_at': '2026-09-28T12:00:00Z'}


def listing(items=None, **extra):
    return {'ok': True, 'output': {'session_id': 'session-one', 'items': items or [item()],
                                 'total': 1, 'next_offset': None, **extra}}


def chunk(output_id='one', text='汉', offset=0, next_offset=3, **extra):
    return {'ok': True, 'output': {'session_id': 'session-one', **item(output_id),
        'text': text, 'offset': offset, 'next_offset': next_offset,
        'eof': next_offset is None, **extra}}


def make_view(api=None):
    from src.ui.tool_outputs import ToolOutputsDialog
    work, selected = [], {'sid': 'session-one'}
    page = SimpleNamespace(update=Mock(), run_thread=work.append, show_dialog=Mock(), pop_dialog=Mock())
    backend = api or Mock(return_value=listing())
    view = ToolOutputsDialog(page, 'session-one', lambda: selected['sid'], api=backend)
    return view, work, selected, backend


def test_list_loading_is_async_bounded_and_selection_reads_plain_text():
    backend = Mock(side_effect=[listing(), chunk(text='[link](file:///secret) <script>')])
    view, work, _, _ = make_view(backend)
    view.open()
    backend.assert_not_called()
    work.pop(0)()
    assert backend.call_args.args[0] == {
        'cmd': 'session_tool_outputs', 'session_id': 'session-one', 'offset': 0, 'limit': 20}
    view.list_rows.controls[0].on_click(None)
    work.pop(0)()
    assert backend.call_args.args[0]['limit'] == 4000
    assert isinstance(view.body, ft.Text) and view.body.selectable is True
    assert view.body.value == '[link](file:///secret) <script>'
    assert view.body_container.height is not None
    assert view.hash_text.value == 'a' * 64


def test_next_and_previous_body_use_returned_byte_cursors():
    backend = Mock(side_effect=[chunk(), chunk(text='bytes', offset=3, next_offset=8), chunk()])
    view, work, _, _ = make_view(backend)
    view.select_output(item())
    work.pop(0)()
    assert '字节' in view.body_label.value
    view.next_chunk()
    work.pop(0)()
    view.previous_chunk()
    work.pop(0)()
    assert [call.args[0]['offset'] for call in backend.call_args_list] == [0, 3, 0]
    assert view.body_previous.disabled is True
    assert view.body.value == '汉'


def test_list_pages_keep_previous_offset_and_show_partial_errors():
    backend = Mock(side_effect=[listing(total=22, next_offset=20),
        listing([item('later')], total=22, errors=[{'error': 'one entry damaged'}]), listing()])
    view, work, _, _ = make_view(backend)
    view.open()
    work.pop(0)()
    view.next_list()
    work.pop(0)()
    assert view.items[0]['output_id'] == 'later'
    assert '1' in view.list_label.value and '未能读取' in view.list_label.value
    view.previous_list()
    work.pop(0)()
    assert [call.args[0]['offset'] for call in backend.call_args_list] == [0, 20, 0]


def test_literal_search_and_hit_jump_use_byte_offset():
    search = {'ok': True, 'output': {'session_id': 'session-one', 'output_id': 'one',
        'matches': [{'offset': 42, 'preview': '<b>A.*  </b>'}], 'next_offset': 80, 'eof': False}}
    backend = Mock(side_effect=[chunk(), search, chunk(text='at hit', offset=42, next_offset=48)])
    view, work, _, _ = make_view(backend)
    view.select_output(item())
    work.pop(0)()
    view.query.value = 'A.*  '
    view.search()
    work.pop(0)()
    request = backend.call_args.args[0]
    assert request['query'] == 'A.*  ' and request['limit'] == 10 and request['offset'] == 0
    view.matches.controls[0].on_click(None)
    work.pop(0)()
    assert backend.call_args.args[0]['offset'] == 42
    assert view.body.value == 'at hit'


def test_search_pagination_resets_when_literal_query_changes():
    result = {'ok': True, 'output': {'session_id': 'session-one', 'output_id': 'one',
        'matches': [], 'next_offset': 60, 'eof': False}}
    backend = Mock(side_effect=[chunk(), result, result])
    view, work, _, _ = make_view(backend)
    view.select_output(item())
    work.pop(0)()
    view.query.value = 'first'
    view.search()
    work.pop(0)()
    view.query.value = 'second'
    view.next_search()
    work.pop(0)()
    assert backend.call_args.args[0]['offset'] == 0
    assert backend.call_args.args[0]['query'] == 'second'


def test_stale_list_and_read_do_not_replace_newer_selection():
    backend = Mock(side_effect=[listing([item('new')]), listing([item('old')]),
        chunk('second', 'latest'), chunk('first', 'stale')])
    view, work, _, _ = make_view(backend)
    view.open()
    old = work.pop(0)
    view.load_list()
    work.pop(0)()
    old()
    assert view.items[0]['output_id'] == 'new'
    view.select_output(item('first'))
    old = work.pop(0)
    view.select_output(item('second'))
    work.pop(0)()
    old()
    assert view.current['output_id'] == 'second' and view.body.value == 'latest'


@pytest.mark.parametrize('change', ['session', 'close'])
def test_switch_session_or_close_discards_late_success_and_error(change):
    backend = Mock(side_effect=[listing(), RuntimeError('late failure')])
    view, work, selected, _ = make_view(backend)
    view.open()
    view.load_list()
    if change == 'session':
        selected['sid'] = 'session-two'
    else:
        view.close()
    updates = view.page.update.call_count
    for job in work:
        job()
    assert not view.items
    assert view.page.update.call_count == updates


def test_failed_or_wrong_scope_read_preserves_success_and_navigation():
    backend = Mock(side_effect=[chunk(), {'ok': False, 'error': 'not found'},
        chunk(session_id='another-session')])
    view, work, _, _ = make_view(backend)
    view.select_output(item())
    work.pop(0)()
    view.next_chunk()
    work.pop(0)()
    assert view.body.value == '汉' and view.body_following.disabled is False
    assert 'not found' in view.body_label.value
    view.next_chunk()
    work.pop(0)()
    assert view.body.value == '汉' and '不匹配' in view.body_label.value


def test_failed_list_keeps_previous_rows_and_restores_paging():
    backend = Mock(side_effect=[listing(total=21, next_offset=20), RuntimeError('index unavailable')])
    view, work, _, _ = make_view(backend)
    view.open()
    work.pop(0)()
    rows = view.list_rows.controls[:]
    view.next_list()
    work.pop(0)()
    assert view.list_rows.controls == rows and view.items[0]['output_id'] == 'one'
    assert view.list_following.disabled is False
    assert 'index unavailable' in view.list_label.value


def test_failed_search_keeps_matches_and_successful_cursor():
    search = {'ok': True, 'output': {'session_id': 'session-one', 'output_id': 'one',
        'matches': [{'offset': 12, 'preview': 'old'}], 'next_offset': 20, 'eof': False}}
    backend = Mock(side_effect=[chunk(), search, {'ok': False, 'error': 'search unavailable'}])
    view, work, _, _ = make_view(backend)
    view.select_output(item())
    work.pop(0)()
    view.query.value = 'old'
    view.search()
    work.pop(0)()
    rows = view.matches.controls[:]
    view.next_search()
    work.pop(0)()
    assert view.matches.controls == rows and view.search_current['offset'] == 0
    assert view.search_following.disabled is False
    assert 'search unavailable' in view.search_label.value


def test_response_exceeding_byte_budget_cannot_replace_visible_page():
    backend = Mock(side_effect=[chunk(), chunk(text='汉' * 1334, offset=3, next_offset=None, total_bytes=9000)])
    view, work, _, _ = make_view(backend)
    view.select_output(item())
    work.pop(0)()
    view.next_chunk()
    work.pop(0)()
    assert view.body.value == '汉' and view.current['offset'] == 0
    assert '无效' in view.body_label.value


def test_pending_search_is_discarded_if_query_or_output_changes():
    search = {'ok': True, 'output': {'session_id': 'session-one', 'output_id': 'one',
        'matches': [{'offset': 12, 'preview': 'old'}], 'next_offset': None, 'eof': True}}
    backend = Mock(side_effect=[chunk(), search, search, chunk('two', 'new output')])
    view, work, _, _ = make_view(backend)
    view.select_output(item())
    work.pop(0)()
    view.query.value = 'old'
    view.search()
    view.query.value = 'new'
    work.pop(0)()
    assert not view.matches.controls
    view.query.value = 'old'
    view.search()
    old = work.pop(0)
    view.select_output(item('two'))
    old()
    work.pop(0)()
    assert not view.matches.controls and view.current['output_id'] == 'two'


def test_composer_toolbar_opens_fixed_selected_session(monkeypatch):
    from src.ui import session_composer, tool_outputs
    factory = Mock()
    monkeypatch.setattr(tool_outputs, 'ToolOutputsDialog', factory)
    page = SimpleNamespace(update=Mock(), show_dialog=Mock(), services=[])
    selected = {'sid': 'session-one'}
    composer = session_composer.SessionComposer(page, ft.TextField(), lambda: selected['sid'],
        lambda: '', Mock(), Mock())
    button = next(control for control in composer.toolbar.controls if control.content == '工具输出')
    button.on_click(None)
    assert factory.call_args.args[1] == 'session-one'
    assert factory.call_args.kwargs['api'] is session_composer.handle
    factory.return_value.open.assert_called_once()
    selected['sid'] = None
    composer.show_tool_outputs()
    assert factory.call_count == 1
