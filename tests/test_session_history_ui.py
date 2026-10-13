from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft
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
