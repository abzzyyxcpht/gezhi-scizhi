"""Current-session observability stays truthful and never mutates chat input."""
from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace
from unittest.mock import Mock


def status(session_id='session-a', **changes):
    row = {
        'session_id': session_id, 'status': 'done', 'driver': 'adaptive', 'agent': 'research',
        'mode': 'agent', 'model': {'model': 'saved-model', 'protocol': 'responses'},
        'context': {'tokens': 24000, 'limit_tokens': 128000, 'measurement': 'heuristic_tokens',
                    'compressions': 2, 'messages': 40},
        'budget': {'token_limit': 100000, 'remaining_tokens': 88000,
                   'model_calls': 4, 'max_steps': 16, 'remaining_steps': 12},
        'tokens': 12000, 'usage_complete': True,
        'session_usage': {'tokens': 35000, 'model_calls': 10, 'usage_complete': True},
        'waiting_reason': '', 'can_compact': True, 'compact_unavailable_reason': '',
        'updated_at': '2026-09-27T10:00:00+00:00',
    }
    return {**row, **changes}


def panel_fixture(api=None):
    assert importlib.util.find_spec('src.ui.session_status') is not None, 'session status component is not implemented'
    module = importlib.import_module('src.ui.session_status')
    selected = {'sid': 'session-a', 'epoch': 1, 'busy': False, 'time': 0.0}
    queued = []
    page = SimpleNamespace(update=Mock(), run_thread=queued.append)
    api = api or Mock(return_value={'ok': True, 'status': status()})
    panel = module.SessionStatusPanel(page, lambda: selected['sid'],
        epoch=lambda: selected['epoch'], busy=lambda: selected['busy'], api=api,
        clock=lambda: selected['time'])
    return panel, selected, queued, page, api


def load(panel, queued):
    panel.refresh(force=True)
    queued.pop(0)()


def test_status_distinguishes_context_estimate_current_budget_and_session_total():
    panel, _, queued, _, _ = panel_fixture()
    load(panel, queued)
    assert 'saved-model' in panel.summary.value
    assert '估算' in panel.metrics.value and '24,000' in panel.metrics.value and '128,000' in panel.metrics.value
    assert '本轮' in panel.budget.value and '88,000' in panel.budget.value
    assert '会话' in panel.usage.value and '35,000' in panel.usage.value
    assert '12,000' in panel.budget.value
    assert not panel.compact_button.disabled


def test_unknown_limits_and_partial_usage_do_not_display_false_zeroes():
    row = status(context={'tokens': 8000, 'limit_tokens': None, 'measurement': 'heuristic_tokens',
                          'compressions': 0, 'messages': 3},
                 tokens=None, usage_complete=False, session_usage={'tokens': 12000, 'model_calls': 3, 'usage_complete': False})
    panel, _, queued, _, _ = panel_fixture(Mock(return_value={'ok': True, 'status': row}))
    load(panel, queued)
    assert '上限未记录' in panel.metrics.value
    assert '未报送' in panel.warning.value
    assert '本轮已报送 未报告' in panel.budget.value
    assert '0%' not in panel.metrics.value


def test_legacy_usage_counts_keep_unknown_completeness_explicit_without_warning():
    row = status(usage_complete=None, session_usage={'tokens': 12000, 'model_calls': 3, 'usage_complete': None})
    panel, _, queued, _, _ = panel_fixture(Mock(return_value={'ok': True, 'status': row}))
    load(panel, queued)
    assert '完整性未记录' in panel.budget.value
    assert '完整性未记录' in panel.usage.value
    assert not panel.warning.visible


def test_legacy_model_is_not_replaced_by_the_current_global_configuration():
    row = status(model={'model': None, 'protocol': None}, context=None, budget=None, tokens=None, session_usage=None,
                 can_compact=False, compact_unavailable_reason='没有可整理的模型上下文')
    panel, _, queued, _, _ = panel_fixture(Mock(return_value={'ok': True, 'status': row}))
    load(panel, queued)
    assert '未记录模型' in panel.summary.value
    assert '没有可整理' in panel.reason.value
    assert panel.compact_button.disabled


def test_refresh_throttles_requests_and_unchanged_status_does_not_redraw():
    panel, selected, queued, page, api = panel_fixture()
    load(panel, queued)
    page.update.reset_mock()
    selected['time'] = 0.9
    panel.refresh()
    assert not queued
    selected['time'] = 1.0
    panel.refresh()
    panel.refresh(force=True)
    assert len(queued) == 1
    queued.pop()()
    assert api.call_count == 2
    page.update.assert_not_called()


def test_late_state_response_cannot_replace_another_session_or_turn():
    panel, selected, queued, _, _ = panel_fixture(Mock(side_effect=[
        {'ok': True, 'status': status('session-b', model={'model': 'new-model', 'protocol': 'responses'})},
        {'ok': True, 'status': status()},
    ]))
    panel.refresh()
    old = queued.pop()
    selected.update(sid='session-b', epoch=2)
    panel.refresh()
    queued.pop()()
    old()
    assert 'new-model' in panel.summary.value
    assert panel.status['session_id'] == 'session-b'


def test_same_session_new_epoch_discards_a_late_response():
    panel, selected, queued, _, _ = panel_fixture()
    panel.refresh()
    old = queued.pop()
    selected['epoch'] += 1
    old()
    assert panel.status is None


def test_read_error_keeps_last_good_same_session_but_clears_on_switch():
    panel, selected, queued, _, _ = panel_fixture(Mock(side_effect=[
        {'ok': True, 'status': status()}, {'ok': False, 'error': '暂不可读'},
        {'ok': False, 'error': '会话不存在'},
    ]))
    load(panel, queued)
    load(panel, queued)
    assert 'saved-model' in panel.summary.value
    assert '暂不可读' in panel.feedback.value
    assert panel.compact_button.disabled
    selected.update(sid='missing', epoch=2)
    load(panel, queued)
    assert 'saved-model' not in panel.summary.value
    assert panel.status is None


def test_compaction_uses_returned_status_and_reports_actual_change():
    api = Mock(side_effect=[{'ok': True, 'status': status()}, {
        'ok': True, 'result': {'session_id': 'session-a', 'changed': True, 'before_tokens': 24000,
            'after_tokens': 10000, 'removed_messages': 12, 'method': 'deterministic', 'model_calls': 0,
            'message': '已整理'}, 'status': status(context={'tokens': 10000, 'limit_tokens': 128000,
                'compressions': 3, 'measurement': 'heuristic_tokens', 'messages': 28})}])
    panel, _, queued, _, _ = panel_fixture(api)
    load(panel, queued)
    panel.compact()
    panel.compact()
    assert len(queued) == 1 and panel.compact_button.disabled
    queued.pop()()
    assert api.call_args.args[0] == {'cmd': 'session_compact', 'session_id': 'session-a'}
    assert api.call_count == 2
    assert '24,000' in panel.feedback.value and '10,000' in panel.feedback.value
    assert '不调用模型' in panel.feedback.value
    assert '10,000' in panel.metrics.value


def test_noop_compaction_does_not_claim_freed_context():
    api = Mock(side_effect=[{'ok': True, 'status': status()}, {
        'ok': True, 'result': {'session_id': 'session-a', 'changed': False, 'before_tokens': 24000,
            'after_tokens': 24000, 'removed_messages': 0, 'method': 'deterministic', 'model_calls': 0,
            'message': '当前上下文无需整理'}, 'status': status()}])
    panel, _, queued, _, _ = panel_fixture(api)
    load(panel, queued)
    panel.compact()
    queued.pop()()
    assert panel.feedback.value == '当前上下文无需整理'
    assert '已整理' not in panel.feedback.value


def test_committed_compaction_keeps_the_backend_audit_write_warning_visible():
    warning = '上下文已整理，但审计记录写入失败；请检查存储，未调用模型。'
    api = Mock(side_effect=[{'ok': True, 'status': status()}, {
        'ok': True, 'result': {'session_id': 'session-a', 'changed': True, 'before_tokens': 24000,
            'after_tokens': 10000, 'removed_messages': 12, 'method': 'deterministic', 'model_calls': 0,
            'message': warning}, 'status': status()}])
    panel, _, queued, _, _ = panel_fixture(api)
    load(panel, queued)
    panel.compact()
    queued.pop()()
    assert panel.feedback.visible
    assert '24,000' in panel.feedback.value and '10,000' in panel.feedback.value
    assert warning in panel.feedback.value


def test_busy_or_backend_denied_compaction_is_not_submitted():
    row = status(can_compact=False, compact_unavailable_reason='等待工具审批，请先处理')
    panel, selected, queued, _, api = panel_fixture(Mock(return_value={'ok': True, 'status': row}))
    load(panel, queued)
    panel.compact()
    assert not queued and api.call_count == 1
    assert '审批' in panel.reason.value
    panel.status = status()
    selected['busy'] = True
    panel.compact()
    assert not queued and api.call_count == 1


def test_late_compaction_result_does_not_change_new_selection():
    api = Mock(side_effect=[{'ok': True, 'status': status()}, {'ok': True,
        'result': {'changed': True, 'before_tokens': 24000, 'after_tokens': 10000}, 'status': status()}])
    panel, selected, queued, _, _ = panel_fixture(api)
    load(panel, queued)
    panel.compact()
    selected.update(sid='session-b', epoch=2)
    queued.pop()()
    assert '已整理' not in panel.feedback.value


def test_repeated_identical_read_error_does_not_redraw_the_existing_error():
    panel, selected, queued, page, _ = panel_fixture(Mock(return_value={'ok': False, 'error': '暂不可读'}))
    load(panel, queued)
    page.update.reset_mock()
    selected['time'] = 2.0
    load(panel, queued)
    page.update.assert_not_called()


def test_read_started_before_compaction_cannot_restore_old_metrics():
    compacted = status(context={'tokens': 10000, 'limit_tokens': 128000, 'compressions': 3,
                                'measurement': 'heuristic_tokens', 'messages': 20})
    def api(request):
        if request['cmd'] == 'session_compact':
            return {'ok': True, 'status': compacted, 'result': {'changed': True,
                'before_tokens': 24000, 'after_tokens': 10000, 'method': 'deterministic', 'model_calls': 0}}
        return {'ok': True, 'status': status()}
    panel, _, queued, _, _ = panel_fixture(api)
    load(panel, queued)
    panel.refresh(force=True)
    old_read = queued.pop()
    panel.compact()
    queued.pop()()
    old_read()
    assert '10,000' in panel.metrics.value
    assert '24,000 /' not in panel.metrics.value
