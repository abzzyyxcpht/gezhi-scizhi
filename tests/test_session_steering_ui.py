from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from src.ui.session_steering import SessionSteeringPanel


def setup():
    work, calls = [], []
    selected = {'sid': 'one', 'epoch': 1}
    status = {'session_id': 'one', 'turn_id': 'turn-one', 'state': 'open', 'enabled': True,
              'status': 'running', 'items': []}
    def api(request):
        calls.append(request)
        if request['cmd'] == 'steer_status':
            return {'ok': True, 'steering': dict(status)}
        return {'ok': True, 'steering': dict(status), 'ticket': {
            'id': 'ticket', 'request_id': request['steer_request_id'], 'state': 'pending'}}
    page = SimpleNamespace(update=Mock(), run_thread=work.append)
    draft = ft.TextField(value='核对反例')
    panel = SessionSteeringPanel(page, draft, lambda: selected['sid'], epoch=lambda: selected['epoch'], api=api)
    panel.refresh(force=True)
    work.pop(0)()
    return panel, draft, selected, status, work, calls


def test_late_steer_receipt_never_clears_new_session_draft():
    panel, draft, selected, _, work, _ = setup()
    panel.add()
    selected.update(sid='two', epoch=2)
    draft.value = '新会话草稿'
    work.pop(0)()
    assert draft.value == '新会话草稿'


def test_receipt_after_typing_keeps_changed_draft_and_binds_original_turn():
    panel, draft, _, _, work, calls = setup()
    panel.add()
    draft.value = '另一个未发送草稿'
    work.pop(0)()
    assert draft.value == '另一个未发送草稿'
    assert calls[-1]['expected_turn_id'] == 'turn-one'


def test_steer_receipt_preserves_same_text_retyped_after_submission():
    panel, draft, _, _, work, _ = setup()
    revision = {'generation': 1}
    panel.draft_revision = lambda: ('session:one', revision['generation'])
    panel.on_draft_changed = Mock()
    panel.add()
    for text in ('', '核对反例'):
        draft.value = text
        revision['generation'] += 1
        panel.draft_changed()
    work.pop(0)()
    assert draft.value == '核对反例'
    panel.on_draft_changed.assert_not_called()


def test_steer_receipt_clears_unchanged_revision_and_notifies_draft_owner():
    panel, draft, _, _, work, _ = setup()
    panel.draft_revision = lambda: ('session:one', 4)
    panel.on_draft_changed = Mock()
    panel.add()
    work.pop(0)()
    assert draft.value == ''
    panel.on_draft_changed.assert_called_once_with()


def test_uncertain_steer_revision_change_cannot_retarget_original_submission():
    panel, draft, _, status, work, calls = setup()
    revision = {'generation': 1}
    panel.draft_revision = lambda: ('session:one', revision['generation'])
    original = panel.api
    panel.api = lambda request: {'ok': True, 'steering': dict(status)}
    panel.add()
    pending = panel.pending
    work.pop(0)()
    revision['generation'] += 2
    panel.api = original
    status['turn_id'] = 'turn-two'
    panel.refresh(force=True)
    work.pop(0)()
    panel.add()
    assert not work and '回合已改变' in panel.notice.value
    assert panel.pending == pending and draft.value == '核对反例'
    assert all(call['cmd'] != 'steer_add' for call in calls)


def test_uncertain_receipt_retry_never_retargets_the_new_turn():
    panel, draft, _, status, work, calls = setup()
    original = panel.api
    panel.api = lambda request: {'ok': True, 'steering': dict(status)}
    panel.add()
    first = panel.pending
    work.pop(0)()
    assert draft.value == '核对反例' and panel.pending == first
    panel.api = original
    status['turn_id'] = 'turn-two'
    panel.refresh(force=True)
    work.pop(0)()
    panel.add()
    assert not work and '回合已改变' in panel.notice.value
    draft.value = '修改后的要求'
    panel.add()
    work.pop(0)()
    assert calls[-1]['expected_turn_id'] == 'turn-two' and draft.value == ''
