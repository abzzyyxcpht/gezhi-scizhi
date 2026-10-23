"""Native outcome review must stay bound to the version the person inspected."""
from __future__ import annotations

from unittest.mock import Mock

import flet as ft

from tests.test_outcome_panel import fixture, report, text

REVISION = 'a' * 64
NEXT_REVISION = 'b' * 64


def loaded():
    checks = report('satisfied', 'pending')
    checks['revision'] = REVISION
    panel, api, pending, dialogs, page = fixture(checks)
    panel.refresh('task-a')
    pending.pop()()
    return panel, api, pending, dialogs, page


def fill(panel, decision='approved'):
    panel.open_review()
    panel.reviewer.value = 'Jane Doe'
    panel.review_note.value = '核对了引用与材料'
    panel.review_decision.value = decision


def saved_reply(decision='approved', revision=REVISION):
    return {'ok': True, 'task_id': 'task-a', 'review': {
        'by': 'Jane Doe', 'note': '核对了引用与材料',
        'decision': decision, 'revision': revision,
        'at': '2026-10-23T13:00:00+08:00',
    }}


def test_refresh_button_rechecks_same_task_after_material_changes():
    panel, api, pending, _, _ = loaded()
    changed = report('stale', 'pending')
    changed['revision'] = NEXT_REVISION
    api.return_value = {'ok': True, 'checks': changed}
    panel.refresh_button.on_click(None)
    assert pending
    pending.pop()()
    assert api.call_count == 2
    assert panel.checks['revision'] == NEXT_REVISION
    assert '需' in text(panel.content)


def test_unreadable_checklist_cannot_enable_manual_approval():
    panel, api, pending, _, _ = loaded()
    api.return_value = {'ok': False, 'error': 'temporary failure'}
    panel.refresh('task-a', force=True)
    pending.pop()()
    assert panel.review_button.disabled
    assert not panel.refresh_button.disabled


def test_review_requires_explicit_decision_before_writing():
    panel, api, pending, _, _ = loaded()
    panel.open_review()
    panel.reviewer.value = 'Jane Doe'
    panel.review_note.value = '还有材料需要补充'
    panel.save_review()
    assert api.call_count == 1
    assert not pending
    assert panel.dialog_error.visible
    assert '结果' in panel.dialog_error.value


def test_save_is_async_and_uses_the_inspected_revision():
    panel, api, pending, _, _ = loaded()
    fill(panel)
    api.return_value = saved_reply()
    panel.save_review()
    assert api.call_count == 1  # UI handler schedules IO instead of blocking.
    assert panel.save_button.disabled
    assert panel._saving
    pending.pop(0)()
    assert api.call_args_list[1].args[0] == {
        'cmd': 'outcome_review', 'task_id': 'task-a', 'record': {
            'by': 'Jane Doe', 'note': '核对了引用与材料',
            'decision': 'approved', 'expected_revision': REVISION,
        }}


def test_changes_requested_is_a_distinct_recorded_decision():
    panel, api, pending, _, _ = loaded()
    fill(panel, 'changes_requested')
    api.return_value = saved_reply('changes_requested')
    panel.save_review()
    pending.pop(0)()
    assert api.call_args_list[1].args[0]['record']['decision'] == 'changes_requested'


def test_late_save_reply_does_not_close_a_new_dialog():
    panel, api, pending, dialogs, page = loaded()
    fill(panel)
    panel.save_review()
    work = pending.pop()
    panel.close_review()
    checks = report('pending')
    checks['revision'] = NEXT_REVISION
    api.return_value = {'ok': True, 'checks': checks}
    panel.refresh('task-b')
    pending.pop()()
    panel.open_review()
    new_dialog = panel.dialog
    page.pop_dialog.reset_mock()
    dialogs.clear()
    api.return_value = saved_reply()
    work()
    assert panel.dialog is new_dialog
    assert not page.pop_dialog.called
    assert not dialogs


def test_same_task_cannot_save_a_second_decision_while_first_save_is_running():
    panel, api, pending, _, _ = loaded()
    fill(panel)
    panel.save_review()
    first_save = pending.pop()
    panel.close_review()
    panel.open_review()
    assert panel.dialog is None
    assert '保存' in panel.notice.value
    assert api.call_count == 1
    api.return_value = saved_reply()
    first_save()
    panel.open_review()
    assert panel.dialog is not None


def test_old_dialog_dismiss_cannot_clear_a_new_dialog():
    panel, _, _, _, _ = loaded()
    panel.open_review()
    old = panel.dialog
    panel.close_review()
    panel.open_review()
    current = panel.dialog
    old.on_dismiss(None)
    assert panel.dialog is current
    assert panel._review_task == 'task-a'
    assert panel._review_revision == REVISION


def test_wrong_version_save_acknowledgement_is_not_success():
    panel, api, pending, dialogs, _ = loaded()
    fill(panel)
    api.return_value = saved_reply(revision=NEXT_REVISION)
    panel.save_review()
    pending.pop()()
    assert panel.dialog is not None
    assert panel.dialog_error.visible
    assert not any(isinstance(item, ft.SnackBar) for item in dialogs)


def test_incomplete_save_receipt_does_not_claim_a_record_exists():
    panel, api, pending, dialogs, _ = loaded()
    fill(panel)
    reply = saved_reply()
    reply['review'].pop('at')
    api.return_value = reply
    panel.save_review()
    pending.pop()()
    assert panel.dialog is not None
    assert panel.dialog_error.visible
    assert not any(isinstance(item, ft.SnackBar) for item in dialogs)


def test_no_revision_cannot_be_used_to_record_approval():
    checks = report('satisfied')
    checks.pop('revision', None)
    panel, api, pending, _, _ = fixture(checks)
    panel.refresh('task-a')
    pending.pop()()
    assert panel.review_button.disabled
    panel.open_review()
    assert panel.dialog is None


def test_invalid_report_cannot_claim_all_checks_passed():
    checks = {'items': [None], 'total': 1, 'satisfied': 1,
              'verdict': 'all_satisfied', 'revision': REVISION}
    panel, _, pending, _, _ = fixture(checks)
    panel.refresh('task-a')
    pending.pop()()
    assert panel.review_button.disabled
    assert not panel.summary_card.visible


def test_reset_closes_only_the_panels_active_review_dialog():
    panel, _, _, _, page = loaded()
    panel.open_review()
    page.pop_dialog = Mock()
    panel.reset()
    assert page.pop_dialog.call_count == 1
    assert panel.dialog is None
    panel.reset()
    assert page.pop_dialog.call_count == 1
