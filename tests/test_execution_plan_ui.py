"""Execution progress is bounded, explicit about evidence and session-scoped."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from src.ui.execution_plan import ExecutionPlanPanel
from src.ui.session_status import SessionStatusPanel
from src.ui.theme import configure_typography


def plan(**changes):
    return {
        'session_id': 'session-a', 'turn_id': 'turn-1', 'revision': 1,
        'has_plan': True, 'completed': 1, 'total': 3, 'open_count': 2, 'ready': False,
        'steps': [
            {'id': 'read', 'title': '读取研究材料', 'criterion': '整理材料来源与缺失项',
             'kind': 'execution', 'status': 'completed', 'evidence': ['read-1'],
             'evidence_kind': 'tool_receipt', 'evidence_details': [
                 {'call_id': 'read-1', 'tool': 'file.read', 'summary': '已读取 2 份材料'}]},
            {'id': 'analyse', 'title': '分析关键证据', 'criterion': '区分观察结果与推断',
             'kind': 'reasoning', 'status': 'in_progress', 'evidence_kind': 'none'},
            {'id': 'export', 'title': '导出复核清单', 'criterion': '生成包含来源的清单',
             'kind': 'execution', 'status': 'pending', 'evidence_kind': 'none'},
        ], **changes,
    }


def walk(control):
    yield control
    for name in ('content', 'title', 'subtitle', 'leading', 'trailing'):
        child = getattr(control, name, None)
        if isinstance(child, ft.Control):
            yield from walk(child)
    for child in getattr(control, 'controls', []) or []:
        yield from walk(child)


def text(panel):
    return '\n'.join(str(c.value) for c in walk(panel.content) if isinstance(c, ft.Text))


def test_empty_plan_hides_card_and_clears_previous_content():
    panel = ExecutionPlanPanel()
    assert not panel.content.visible
    assert panel.update(plan())
    assert panel.content.visible
    assert panel.update({'has_plan': False, 'steps': []})
    assert not panel.content.visible
    assert '读取研究材料' not in text(panel)
    assert not panel.update(None)


def test_real_controls_show_progress_criteria_and_receipt_semantics():
    panel = ExecutionPlanPanel()
    panel.update(plan())
    assert isinstance(panel.details, ft.ExpansionTile)
    assert not panel.details.expanded
    assert panel.progress.value == 1 / 3
    assert '1/3' in panel.count.value
    assert '分析关键证据' in panel.current.value
    assert '区分观察结果与推断' in text(panel)
    assert '工具回执已关联' in text(panel)
    assert 'file.read' in text(panel)
    assert '已验证' not in text(panel)


def test_unchanged_snapshot_preserves_controls_and_expansion_state():
    panel = ExecutionPlanPanel()
    snapshot = plan()
    panel.update(snapshot)
    panel.details.expanded = True
    controls = panel.steps.controls
    assert not panel.update(deepcopy(snapshot))
    assert panel.steps.controls is controls
    assert panel.details.expanded
    snapshot['steps'][1]['status'] = 'blocked'
    snapshot['steps'][1]['reason'] = '缺少对照组，请补充数据'
    assert panel.update(snapshot)
    assert panel.details.expanded
    assert '受阻' in panel.current.value
    assert '缺少对照组，请补充数据' in text(panel)


def test_new_session_or_turn_collapses_old_expansion():
    panel = ExecutionPlanPanel()
    panel.update(plan())
    panel.details.expanded = True
    panel.update(plan(session_id='session-b'))
    assert not panel.details.expanded
    panel.details.expanded = True
    panel.update(plan(session_id='session-b', turn_id='turn-2'))
    assert not panel.details.expanded


def test_model_statement_is_not_presented_as_a_tool_receipt():
    snapshot = plan()
    snapshot['steps'][0].update(kind='reasoning', evidence_kind='model_statement',
                                evidence=[], evidence_details=[])
    panel = ExecutionPlanPanel()
    panel.update(snapshot)
    assert '模型声明' in text(panel)
    assert '工具回执已关联' not in text(panel)


def test_untrusted_text_is_plain_redacted_and_bounded_including_tooltips():
    snapshot = plan()
    secret = 'sk-abcdefghijklmnopqrstuvwx'
    title = '<script>alert(1)</script> ' + secret + ' 标题' * 10000
    snapshot['steps'][0].update(title=title, criterion='api_key=do-not-display',
                                reason='Bearer do-not-display', status='blocked')
    snapshot['steps'][0]['evidence_details'][0]['summary'] = 'password=hidden-password'
    panel = ExecutionPlanPanel()
    panel.update(snapshot)
    controls = list(walk(panel.content))
    visible = text(panel) + '\n'.join(str(c.tooltip or '') for c in controls)
    assert secret not in visible and 'do-not-display' not in visible
    assert 'hidden-password' not in visible
    assert '[REDACTED]' in visible
    assert '<script>' in visible
    assert not any(isinstance(c, ft.Markdown) for c in controls)
    assert max(len(str(c.value)) for c in controls if isinstance(c, ft.Text)) < 1200
    assert len(visible) < 10000
    assert panel.current.max_lines == 2


def test_large_or_malformed_plan_stays_bounded_and_progress_is_finite():
    snapshot = plan(total=-1, completed=True)
    snapshot['steps'] = [None, 'bad', *[dict(plan()['steps'][0], id=str(i)) for i in range(1000)]]
    panel = ExecutionPlanPanel()
    panel.update(snapshot)
    assert len(panel.steps.controls) <= 34
    assert 0 <= panel.progress.value <= 1
    assert '未显示' in text(panel)


def test_polling_metadata_and_read_errors_do_not_rebuild_steps():
    panel = ExecutionPlanPanel()
    snapshot = plan()
    panel.update(snapshot)
    controls = panel.steps.controls
    snapshot['revision'] += 1
    snapshot['updated_at'] = '2026-10-19T10:00:00Z'
    assert not panel.update(snapshot)
    assert panel.update(snapshot, read_error=True)
    assert panel.steps.controls is controls
    assert panel.notice.visible
    assert panel.update(snapshot, read_error=False)
    assert panel.steps.controls is controls
    assert not panel.notice.visible


def test_cancelled_plan_is_not_presented_as_fully_completed():
    snapshot = plan(completed=1, ready=False, open_count=0)
    for item in snapshot['steps'][1:]:
        item['status'] = 'cancelled'
    panel = ExecutionPlanPanel()
    panel.update(snapshot)
    assert panel.progress.value == 1 / 3
    assert '已取消' in text(panel)
    assert '步骤已完成' not in panel.current.value


def test_reduced_motion_uses_zero_duration():
    try:
        configure_typography({'ui_animations': False})
        panel = ExecutionPlanPanel()
        assert panel.details.animation_style.duration == 0
    finally:
        configure_typography({})


def test_status_refresh_preserves_plan_on_failure_and_clears_before_session_load():
    selected = {'id': 'session-a', 'epoch': 0}
    work = []
    page = SimpleNamespace(update=Mock(), run_thread=work.append)
    row = {'session_id': 'session-a', 'status': 'running', 'execution_plan': plan()}
    api = Mock(side_effect=[{'ok': True, 'status': row}, {'ok': False, 'error': '暂不可读'}])
    panel = SessionStatusPanel(page, lambda: selected['id'], epoch=lambda: selected['epoch'], api=api)
    panel.refresh(force=True)
    work.pop()()
    assert panel.execution_plan.content.visible
    panel.refresh(force=True)
    work.pop()()
    assert '读取研究材料' in text(panel.execution_plan)
    assert panel.execution_plan.notice.visible
    assert '暂不可读' in panel.execution_plan.notice.value
    assert api.call_count == 2
    selected.update(id='session-b', epoch=1)
    panel.refresh(force=True)
    assert not panel.execution_plan.content.visible
    assert '读取研究材料' not in text(panel.execution_plan)


def test_successful_status_with_plan_read_error_keeps_last_plan_until_explicit_clear():
    work = []
    page = SimpleNamespace(update=Mock(), run_thread=work.append)
    payloads = [plan(), {'session_id': 'session-a', 'has_plan': False,
                        'error': '执行计划暂不可读，请稍后重试'},
                {'session_id': 'session-a', 'has_plan': False, 'steps': []}]
    api = Mock(side_effect=[{'ok': True, 'status': {'session_id': 'session-a',
                        'status': 'running', 'execution_plan': payload}} for payload in payloads])
    panel = SessionStatusPanel(page, lambda: 'session-a', api=api)
    panel.refresh(force=True)
    work.pop()()
    controls = panel.execution_plan.steps.controls
    panel.refresh(force=True)
    work.pop()()
    assert not panel._read_error
    assert panel.execution_plan.content.visible
    assert panel.execution_plan.steps.controls is controls
    assert panel.execution_plan.notice.visible
    assert '读取研究材料' in text(panel.execution_plan)
    panel.refresh(force=True)
    work.pop()()
    assert not panel.execution_plan.content.visible
    assert '读取研究材料' not in text(panel.execution_plan)


def test_plan_error_without_same_session_history_never_displays_another_session_plan():
    panel = ExecutionPlanPanel()
    panel.update(plan())
    panel.update({'session_id': 'session-b', 'has_plan': False, 'error': '读取失败'})
    assert panel.content.visible and panel.notice.visible
    assert '暂不可读' in panel.notice.value
    assert not panel.details.visible and not panel.progress.visible
    assert '读取研究材料' not in text(panel)
    panel.update(plan(session_id='session-b'))
    assert panel.details.visible and panel.progress.visible
    assert not panel.notice.visible


def test_selectable_step_text_does_not_preallocate_unused_lines():
    panel = ExecutionPlanPanel()
    panel.update(plan())
    step_controls = [control for step in panel.steps.controls[:3] for control in walk(step)]
    selected = [control for control in step_controls if isinstance(control, ft.Text) and control.selectable]
    assert selected
    # Flutter SelectableText reserves max_lines of height even for one-line values.
    assert all(control.max_lines is None for control in selected)
    assert all(control.tight for control in step_controls if isinstance(control, ft.Column))
    assert panel.steps.tight and panel.content.content.tight
