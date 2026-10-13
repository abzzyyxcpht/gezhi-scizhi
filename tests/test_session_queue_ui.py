"""Queue controls are explicit, identity-bound and preserve unaccepted drafts."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import flet as ft
import pytest


def queue(sid='a', *, enabled=True, items=None, **changes):
    return {'session_id': sid, 'revision': 1, 'enabled': enabled, 'paused': False,
            'pause_reason': '', 'resume_required': False, 'worker_active': False,
            'items': items or [], 'counts': {}, **changes}


def item(ident, state='queued', request=None):
    return {'id': ident, 'request_id': 'request-' + ident, 'state': state,
            'request': request or ('研究问题 ' + ident), 'error': '', 'agent': 'research',
            'mode': 'agent', 'offline': True, 'project_id': ''}


def acceptance(request, ident='one'):
    ticket = {**item(ident, request=request.get('request')), 'request_id': request['queue_request_id']}
    return {'ok': True, 'queue': queue(request['session_id'], items=[ticket]), 'ticket': dict(ticket)}


def fixture(api=None):
    assert importlib.util.find_spec('src.ui.session_queue') is not None, 'queue UI is not implemented'
    module = importlib.import_module('src.ui.session_queue')
    selected = {'sid': 'a', 'epoch': 1, 'time': 0.0}
    work = []
    page = SimpleNamespace(update=Mock(), run_thread=work.append, show_dialog=Mock(), pop_dialog=Mock())
    request = ft.TextField(value='下一步研究')
    api = api or Mock(return_value={'ok': True, 'queue': queue()})
    panel = module.SessionQueuePanel(page, request, lambda: selected['sid'],
        epoch=lambda: selected['epoch'], clock=lambda: selected['time'], api=api)
    return panel, selected, request, work, api, page


def load(panel, work):
    panel.refresh(force=True)
    work.pop(0)()


def test_disabled_queue_keeps_stored_items_and_exposes_opt_in():
    panel, _, _, work, _, _ = fixture(Mock(return_value={'ok': True,
        'queue': queue(enabled=False, items=[item('one')])}))
    load(panel, work)
    assert panel.enable_button.visible and panel.dock.visible
    assert panel.add_button.disabled
    assert len(panel.rows.controls) == 1


def test_empty_enabled_queue_dock_is_hidden_but_add_action_is_available():
    panel, _, _, work, _, _ = fixture()
    load(panel, work)
    assert not panel.dock.visible and not panel.enable_button.visible
    assert not panel.add_button.disabled


def test_enabling_is_an_explicit_settings_write_and_does_not_remove_items():
    api = Mock(side_effect=[{'ok': True, 'queue': queue(enabled=False)}, {'ok': True},
                            {'ok': True, 'queue': queue(enabled=True)}])
    panel, _, _, work, _, _ = fixture(api)
    load(panel, work)
    panel.set_enabled(True)
    work.pop()()
    assert api.call_args_list[1].args[0] == {'cmd': 'settings_save', 'settings': {'experimental_message_queue': True}}
    assert not panel.add_button.disabled
    assert all(call.args[0]['cmd'] != 'queue_remove' for call in api.call_args_list)


def test_add_clears_only_the_unchanged_draft_after_server_acceptance():
    api = Mock(side_effect=lambda request: {'ok': True, 'queue': queue()} if request['cmd'] == 'queue_list' else acceptance(request))
    panel, _, request, work, _, _ = fixture(api)
    load(panel, work)
    panel.add()
    assert request.value == '下一步研究'
    work.pop()()
    assert request.value == ''
    assert api.call_args.args[0]['session_id'] == 'a'
    assert api.call_args.args[0]['request'] == '下一步研究'
    assert api.call_args.args[0]['queue_request_id']


def test_add_failure_preserves_draft_and_reuses_idempotency_key_on_retry():
    attempts = []
    def respond(request):
        if request['cmd'] == 'queue_list':
            return {'ok': True, 'queue': queue()}
        attempts.append(request)
        return {'ok': False, 'error': '连接中断'} if len(attempts) == 1 else acceptance(request)
    api = Mock(side_effect=respond)
    panel, _, request, work, _, _ = fixture(api)
    load(panel, work)
    panel.add()
    work.pop()()
    assert request.value == '下一步研究'
    panel.add()
    work.pop()()
    assert api.call_args_list[1].args[0]['queue_request_id'] == api.call_args_list[2].args[0]['queue_request_id']


def test_add_preserves_text_typed_during_acceptance():
    panel, _, request, work, api, _ = fixture()
    load(panel, work)
    api.side_effect = acceptance
    panel.add()
    request.value = '刚写的新草稿'
    work.pop()()
    assert request.value == '刚写的新草稿'
    assert '新草稿' in panel.notice.value and '未发送' in panel.notice.value


def test_add_preserves_same_text_retyped_after_submission():
    panel, _, request, work, api, _ = fixture()
    load(panel, work)
    api.side_effect = acceptance
    revision = {'generation': 1}
    panel.draft_revision = lambda: ('session:a', revision['generation'])
    panel.on_draft_changed = Mock()
    panel.add()
    for text in ('', '下一步研究'):
        request.value = text
        revision['generation'] += 1
        panel.draft_changed()
    work.pop()()
    assert request.value == '下一步研究'
    assert '新草稿' in panel.notice.value and '未发送' in panel.notice.value
    panel.on_draft_changed.assert_not_called()


def test_add_clears_unchanged_revision_and_notifies_draft_owner():
    panel, _, request, work, api, _ = fixture()
    load(panel, work)
    api.side_effect = acceptance
    panel.draft_revision = lambda: ('session:a', 4)
    panel.on_draft_changed = Mock()
    panel.add()
    work.pop()()
    assert request.value == ''
    panel.on_draft_changed.assert_called_once_with()


def test_uncertain_queue_retry_keeps_request_id_when_only_draft_revision_changes():
    attempts = []
    def respond(request):
        if request['cmd'] == 'queue_list':
            return {'ok': True, 'queue': queue()}
        attempts.append(request)
        return {'ok': False, 'error': '回执中断'} if len(attempts) == 1 else acceptance(request)
    panel, _, request, work, _, _ = fixture(Mock(side_effect=respond))
    load(panel, work)
    revision = {'generation': 1}
    panel.draft_revision = lambda: ('session:a', revision['generation'])
    panel.add()
    work.pop()()
    request.value = ''
    revision['generation'] += 1
    request.value = '下一步研究'
    revision['generation'] += 1
    panel.add()
    work.pop()()
    assert attempts[0]['queue_request_id'] == attempts[1]['queue_request_id']
    assert set(attempts[1]) == {'cmd', 'session_id', 'request', 'queue_request_id'}
    assert request.value == ''


def test_malformed_acceptance_does_not_erase_a_draft_without_a_ticket():
    api = Mock(side_effect=[{'ok': True, 'queue': queue()}, {'ok': True, 'queue': queue()}])
    panel, _, request, work, _, _ = fixture(api)
    load(panel, work)
    panel.add()
    work.pop()()
    assert request.value == '下一步研究'
    assert '标识' in panel.notice.value


@pytest.mark.parametrize('mismatch', ['request_id', 'session_id', 'ticket_id'])
def test_wrong_acceptance_identity_keeps_draft_and_original_retry_key(mismatch):
    attempts = []
    def respond(request):
        if request['cmd'] == 'queue_list':
            return {'ok': True, 'queue': queue()}
        attempts.append(request)
        response = acceptance(request)
        if len(attempts) == 1:
            if mismatch == 'request_id':
                response['ticket']['request_id'] = 'another-request'
            elif mismatch == 'session_id':
                response['queue']['session_id'] = 'b'
            else:
                response['ticket']['id'] = 'another-ticket'
        return response
    panel, _, request, work, _, _ = fixture(Mock(side_effect=respond))
    load(panel, work)
    panel.add()
    work.pop()()
    assert request.value == '下一步研究'
    panel.add()
    work.pop()()
    assert attempts[0]['queue_request_id'] == attempts[1]['queue_request_id']
    assert request.value == ''


@pytest.mark.parametrize('discard', [False, True])
@pytest.mark.parametrize('remove_ok', [False, True])
def test_explicit_successful_removal_releases_only_the_matching_uncertain_submission(discard, remove_ok):
    saved, attempts = [], []
    def respond(request):
        if request['cmd'] == 'queue_list':
            return {'ok': True, 'queue': queue(items=saved)}
        if request['cmd'] == 'queue_add':
            attempts.append(request)
            response = acceptance(request)
            if not saved:
                saved.extend(response['queue']['items'])
                return {'ok': False, 'error': '已接受但回执连接中断'}
            return response
        assert request['cmd'] == ('queue_discard' if discard else 'queue_remove')
        if not remove_ok:
            return {'ok': False, 'error': '移除失败'}
        saved[0]['state'] = 'removed'
        return {'ok': True, 'queue': queue(items=saved), 'ticket': dict(saved[0])}
    panel, _, _, work, _, _ = fixture(Mock(side_effect=respond))
    load(panel, work)
    panel.add()
    work.pop()()
    load(panel, work)
    panel.remove('one', discard=discard)
    work.pop()()
    panel.add()
    work.pop()()
    assert (attempts[0]['queue_request_id'] != attempts[1]['queue_request_id']) is remove_ok


def test_draft_update_after_switch_clears_old_queue_before_any_new_read():
    panel, selected, _, work, _, _ = fixture(Mock(return_value={'ok': True, 'queue': queue(items=[item('old')])}))
    load(panel, work)
    selected.update(sid='b', epoch=2)
    panel.draft_changed()
    assert not panel.rows.controls
    assert not panel.dock.visible
    assert panel.add_button.disabled


def test_needs_review_uses_explicit_discard_and_keeps_resume_reason():
    panel, _, _, work, api, _ = fixture(Mock(return_value={'ok': True,
        'queue': queue(items=[item('review', 'needs_review')], paused=True, pause_reason='附件已变化')}))
    load(panel, work)
    assert '放弃' in panel.reason.value and '重新加入' in panel.reason.value
    panel.remove('review', session_id='a', discard=True)
    work.pop()()
    assert api.call_args.args[0]['cmd'] == 'queue_discard'


def test_submission_identity_survives_selection_change_without_touching_new_draft():
    panel, selected, request, work, api, _ = fixture()
    load(panel, work)
    panel.add()
    selected.update(sid='b', epoch=2)
    request.value = '会话 B 草稿'
    work.pop()()
    assert api.call_args.args[0]['session_id'] == 'a'
    assert request.value == '会话 B 草稿'


def test_reordering_sends_the_complete_queued_order_only():
    rows = [item('one'), item('sending', 'dispatching'), item('two'), item('three')]
    panel, _, _, work, api, _ = fixture(Mock(return_value={'ok': True, 'queue': queue(items=rows)}))
    load(panel, work)
    panel.move('two', -1)
    work.pop()()
    assert api.call_args.args[0] == {'cmd': 'queue_reorder', 'session_id': 'a', 'ordered_ids': ['two', 'one', 'three']}


def test_read_is_throttled_and_never_resumes_a_paused_queue():
    panel, selected, _, work, api, page = fixture(Mock(return_value={'ok': True,
        'queue': queue(items=[item('one')], paused=True, resume_required=True, pause_reason='重启后需手动恢复')}))
    load(panel, work)
    assert '手动恢复' in panel.reason.value
    assert panel.resume_button.visible
    page.update.reset_mock()
    selected['time'] = 0.5
    panel.refresh()
    assert not work
    assert [call.args[0]['cmd'] for call in api.call_args_list] == ['queue_list']


def test_stale_row_cannot_remove_a_ticket_from_the_new_session():
    panel, selected, _, work, api, _ = fixture(Mock(return_value={'ok': True, 'queue': queue(items=[item('one')])}))
    load(panel, work)
    selected.update(sid='b', epoch=2)
    panel.remove('one', session_id='a')
    assert not work and api.call_count == 1


def test_real_bridge_accepts_ui_idempotency_field_and_keeps_current_turn_running():
    from src import bridge, config, settings, session_queue as core
    from src.agent_runtime import SessionStore
    with tempfile.TemporaryDirectory(prefix='scizhi-ui-queue-') as directory:
        root = Path(directory)
        with patch.object(config, 'DATA_DIR', root), patch.object(settings, '_PATH', root / 'settings.json'):
            settings.save({'experimental_message_queue': True})
            store = SessionStore(root / 'sessions')
            store.append('a', 'user_message', {'text': '原始研究', 'agent': 'research', 'mode': 'agent', 'offline': True})
            dispatch = Mock(side_effect=AssertionError('current turn has not finished'))
            manager = core.SessionQueue(root, dispatch=dispatch, poll_interval=.01)
            try:
                with patch.object(core, '_MANAGER', manager):
                    panel, _, request, work, _, _ = fixture(bridge.handle)
                    load(panel, work)
                    panel.add()
                    work.pop()()
                    assert request.value == ''
                    assert len(panel.queue['items']) == 1
                    assert panel.queue['items'][0]['request_id'].startswith('gui-')
                    assert store.snapshot('a')['status'] == 'running'
                    dispatch.assert_not_called()
            finally:
                manager.close()
