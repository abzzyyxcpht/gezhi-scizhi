from types import SimpleNamespace
from unittest.mock import Mock

from src.ui.evidence_reviews import EvidenceReviewInbox


def fixture():
    work, calls = [], []
    page = SimpleNamespace(update=Mock(), run_thread=work.append, show_dialog=Mock(), pop_dialog=Mock())
    record = {'id': 'record-one', 'version': 3, 'title': '假设'}
    jobs = []
    def api(req):
        calls.append(req)
        if req['cmd'] == 'review_list':
            return {'ok': True, 'enabled': True, 'jobs': list(jobs), 'total': len(jobs)}
        if req['cmd'] == 'review_start':
            row = {'id': 'job-one', 'request_id': req['review_request_id'], 'record_id': req['record_id'],
                   'record_version': req['expected_version'], 'title': '假设', 'mode': req['mode'],
                   'status': 'queued', 'stale': False, 'can_accept': False}
            jobs.append(row)
            return {'ok': True, 'job': row}
        return {'ok': False, 'error': 'unexpected request'}
    panel = EvidenceReviewInbox(page, api=api)
    panel.open(record)
    work.pop(0)()
    return panel, work, calls, page, jobs


def test_opening_inbox_never_starts_a_review_or_writes_notebook():
    panel, work, calls, _, _ = fixture()
    assert [request['cmd'] for request in calls] == ['review_list']
    assert not panel.start_button.disabled and panel.record['version'] == 3


def test_creation_captures_record_version_and_unknown_reply_keeps_retry_identity():
    panel, work, calls, _, _ = fixture()
    original = panel.api
    panel.api = lambda request: original(request) if request['cmd'] != 'review_start' else {'ok': True}
    panel.start()
    key = panel.pending_start[3]
    work.pop(0)()
    assert panel.pending_start[3] == key
    work.pop(0)()  # read after mutation
    panel.api = original
    panel.start()
    work.pop(0)()
    request = next(row for row in calls if row['cmd'] == 'review_start')
    assert request['expected_version'] == 3 and request['review_request_id'] == key
    assert panel.pending_start is None


def test_closed_view_ignores_late_result_without_opening_or_accepting_it():
    panel, work, calls, page, _ = fixture()
    panel.start()
    panel.close()
    dialogs = page.show_dialog.call_count
    work.pop(0)()
    assert page.show_dialog.call_count == dialogs
    assert not any(row['cmd'] == 'review_accept' for row in calls)


def test_stale_job_has_no_accept_action_and_idle_refresh_keeps_rows():
    panel, work, _, page, jobs = fixture()
    jobs.append({'id': 'job-one', 'record_id': 'record-one', 'record_version': 2, 'title': '旧假设',
                 'mode': 'local', 'status': 'ready', 'stale': True, 'can_accept': False})
    panel.refresh()
    work.pop(0)()
    actions = panel.rows.controls[0].controls[-1].controls
    assert '保存为复核备注' not in [action.content for action in actions]
    first = panel.rows.controls[0]
    page.update.reset_mock()
    panel.refresh()
    work.pop(0)()
    assert panel.rows.controls[0] is first
    page.update.assert_not_called()


def test_mismatched_accept_receipt_does_not_claim_a_note_was_saved():
    panel, work, _, _, _ = fixture()
    panel.api = lambda request: {'ok': True, 'job': {'id': 'different-job', 'status': 'accepted'}}
    panel.action('review_accept', 'job-one')
    work.pop(0)()
    assert '不匹配' in panel.notice.value and '已保存为研究记录' not in panel.notice.value
