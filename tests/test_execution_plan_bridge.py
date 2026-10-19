from types import SimpleNamespace
from unittest import mock

from src import bridge, remote_control
from src.agent_runtime import SessionStore
from src.session_controls import session_status


def test_readonly_plan_bridge_rejects_unknown_or_unsafe_sessions(tmp_path):
    store = SessionStore(tmp_path)
    with mock.patch.object(bridge, '_runtime', return_value=SimpleNamespace(sessions=store)):
        for sid in ('missing', '../escape', ''):
            assert bridge.handle({'cmd': 'session_plan', 'session_id': sid})['ok'] is False
    assert not list(tmp_path.glob('*.jsonl'))


def test_plan_status_and_bridge_share_current_turn_projection(tmp_path):
    store = SessionStore(tmp_path)
    store.append('fixture', 'user_message', {'text': 'Inspect workspace', 'agent': 'research'})
    store.append('fixture', 'turn_started', {'turn_id': 'fixture-turn'})
    with mock.patch.object(bridge, '_runtime', return_value=SimpleNamespace(sessions=store)):
        response = bridge.handle({'cmd': 'session_plan', 'session_id': 'fixture'})
    assert response['ok'] and response['plan']['has_plan'] is False
    assert response['plan']['session_id'] == 'fixture'
    assert session_status('fixture', store=store)['execution_plan'] == response['plan']
    assert len(store.read('fixture')) == 2


def test_remote_plan_command_is_readonly_and_accepts_no_write_fields():
    assert 'session_plan' in remote_control._READ_ONLY
    assert remote_control._validated({'cmd': 'session_plan', 'session_id': 'one'})['cmd'] == 'session_plan'
    try:
        remote_control._validated({'cmd': 'session_plan', 'session_id': 'one', 'steps': []})
    except ValueError:
        pass
    else:
        raise AssertionError('Read-only progress must reject mutation fields')
