"""成果验收清单面板：空态、五档状态、复核刷新与文案纪律。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from src import outcome_checks
from src.ui.outcome_panel import OutcomeCheckPanel
from src.ui.theme import palette as p

TITLES = {
    'satisfied': '文献池', 'failed': '参考文献', 'stale': '材料完整性',
    'pending': '人工复核', 'unknown': '研究包缺口',
}
KINDS = {
    'satisfied': 'artifact_present', 'failed': 'artifact_present',
    'stale': 'material_integrity', 'pending': 'manual_review',
    'unknown': 'deficit_free',
}
DETAILS = {
    'satisfied': '已记录该产物',
    'failed': '任务记录中没有该产物',
    'stale': '1 份材料内容已变化；原结论需要复核',
    'pending': '尚未记录人工复核',
    'unknown': '还没有生成研究包清单',
}
EVIDENCE = {
    'satisfied': [{'artifact': 'lit_filter'}],
    'failed': [],
    'stale': [{'material': 'W1', 'recorded_sha256': 'a1b2c3', 'current_sha256': 'd4e5f6'}],
    'pending': [],
    'unknown': [],
}


def item(state: str, index: int = 0) -> dict:
    return {'id': f'check:{state}:{index}', 'title': f'{TITLES[state]} {index}',
            'criterion': f'{TITLES[state]}的验收条件', 'kind': KINDS[state],
            'state': state, 'evidence': EVIDENCE[state], 'detail': DETAILS[state]}


def report(*states: str) -> dict:
    """用后端自身的 evaluate 生成判定，保证 UI 测试与真实回执同形。"""
    return outcome_checks.evaluate([item(state, index)
                                    for index, state in enumerate(states)])


def walk(control):
    yield control
    for name in ('content', 'title', 'subtitle', 'leading', 'trailing'):
        child = getattr(control, name, None)
        if isinstance(child, ft.Control):
            yield from walk(child)
    for child in getattr(control, 'controls', []) or []:
        yield from walk(child)


def text(control) -> str:
    return '\n'.join(str(node.value) for node in walk(control)
                     if isinstance(node, ft.Text))


def fixture(checks=None):
    pending, dialogs = [], []
    page = SimpleNamespace(update=Mock(), run_thread=pending.append,
                           show_dialog=dialogs.append, pop_dialog=Mock())
    api = Mock(return_value={'ok': True, 'checks':
                             checks if checks is not None else report('satisfied')})
    panel = OutcomeCheckPanel(page, api=api)
    return panel, api, pending, dialogs, page


def load(panel, api, pending, task='task-a'):
    panel.refresh(task)
    pending.pop()()
    return panel


def test_no_open_task_shows_explicit_empty_state_without_backend_call():
    panel, api, _, _, _ = fixture()
    panel.refresh(None)
    assert api.call_count == 0
    assert panel.empty.visible
    assert '没有打开的研究任务' in panel.empty.value
    assert panel.review_button.disabled
    assert panel.items.controls == []


def test_unreadable_checklist_reports_the_reason_instead_of_blank():
    panel, api, pending, _, page = fixture()
    api.return_value = {'ok': False, 'error': '请先打开有效的研究任务'}
    panel.refresh('task-a')
    pending.pop()()
    blob = text(panel.content)
    assert '暂时无法读取验收清单' in blob
    assert '请先打开有效的研究任务' in blob
    assert panel.empty.visible
    assert panel.items.controls == []
    assert page.update.called


def test_empty_report_is_an_explicit_empty_state_not_a_success():
    panel, api, pending, _, _ = fixture()
    api.return_value = {'ok': True, 'checks': report()}
    load(panel, api, pending)
    blob = text(panel.content)
    assert '该任务还没有可核验的成果记录' in blob
    assert '无法给出结论' in blob
    assert panel.empty.visible
    assert '全部检查项已有记录支持' not in blob


def test_verdict_symbols_criteria_details_and_evidence_are_visible():
    panel, api, pending, _, _ = fixture()
    api.return_value = {'ok': True, 'checks':
                        report('satisfied', 'failed', 'stale', 'pending', 'unknown')}
    load(panel, api, pending)
    blob = text(panel.content)
    for symbol in ('✓', '✗', '⚠', '○', '?'):
        assert symbol in blob, symbol
    for fragment in ('已核实', '未通过', '已过期', '待人工确认', '无法核验',
                     '验收条件', '判定说明', '证据 ·', 'artifact=lit_filter',
                     'material=W1', 'recorded_sha256=a1b2c3',
                     '需要复核', '现有记录不足以支持'):
        assert fragment in blob, fragment
    assert '1/5 项已有记录支持' in panel.count.value
    assert panel.summary_card.visible
    assert not panel.empty.visible
    assert not panel.review_button.disabled
    assert '未通过 1' in blob and '需复核 1' in blob


def test_pending_and_unknown_are_never_described_as_completed():
    panel, api, pending, _, _ = fixture()
    api.return_value = {'ok': True, 'checks': report('pending', 'unknown')}
    load(panel, api, pending)
    blob = text(panel.content)
    assert '尚未完成' in blob
    assert '全部检查项已有记录支持' not in blob
    pending_card = text(panel.items.controls[0])
    unknown_card = text(panel.items.controls[1])
    assert '待人工确认' in pending_card and '已完成' not in pending_card
    assert '无法核验' in unknown_card and '已完成' not in unknown_card


def test_stale_item_highlights_that_the_original_conclusion_needs_review():
    panel, api, pending, _, _ = fixture()
    api.return_value = {'ok': True, 'checks': report('stale')}
    load(panel, api, pending)
    card = panel.items.controls[0]
    card_text = text(card)
    assert '原结论需要复核' in card_text
    assert '已过期' in card_text
    symbol = next(node for node in walk(card)
                  if isinstance(node, ft.Text) and node.value == '⚠')
    assert symbol.color == p.WARN


def test_review_dialog_records_the_review_and_refreshes_the_checklist():
    panel, api, pending, dialogs, page = fixture()
    before, after = report('failed', 'stale', 'pending'), report(
        'satisfied', 'satisfied', 'satisfied')
    api.side_effect = [
        {'ok': True, 'checks': before},
        {'ok': True, 'task_id': 'task-a',
         'review': {'by': '张三', 'note': '已核对引用与图表'}},
        {'ok': True, 'checks': after},
    ]
    load(panel, api, pending)
    assert not panel.review_button.disabled
    panel.open_review()
    assert isinstance(dialogs[-1], ft.AlertDialog)
    panel.reviewer.value = '张三'
    panel.review_note.value = '已核对引用与图表'
    panel.save_review()
    review_request = api.call_args_list[1].args[0]
    assert review_request == {'cmd': 'outcome_review', 'task_id': 'task-a',
                              'record': {'by': '张三', 'note': '已核对引用与图表'}}
    assert page.pop_dialog.called
    assert isinstance(dialogs[-1], ft.SnackBar)
    pending.pop()()  # 复核成功后自动重新读取清单
    assert api.call_args_list[2].args[0] == {'cmd': 'outcome_checks',
                                             'task_id': 'task-a'}
    blob = text(panel.content)
    assert '全部检查项已有记录支持' in blob
    assert '原结论需要复核' not in blob


def test_review_requires_both_fields_and_keeps_the_dialog_on_backend_error():
    panel, api, pending, _, page = fixture()
    load(panel, api, pending)
    panel.open_review()
    panel.save_review()
    assert panel.dialog_error.visible
    assert '请填写复核人' in panel.dialog_error.value
    assert api.call_count == 1  # 只有最初的清单读取，没有写复核
    assert not page.pop_dialog.called
    api.return_value = {'ok': False, 'error': '复核记录缺少复核人'}
    panel.reviewer.value = '张三'
    panel.review_note.value = '已核对引用'
    panel.save_review()
    assert not page.pop_dialog.called
    assert panel.dialog is not None
    assert '复核未保存' in panel.dialog_error.value
    assert '复核记录缺少复核人' in panel.dialog_error.value
    assert pending == []  # 写失败不触发清单刷新


def test_review_dialog_never_writes_into_another_task_after_switching():
    panel, api, pending, _, page = fixture()
    load(panel, api, pending)
    panel.open_review()
    panel.reviewer.value = '张三'
    panel.review_note.value = '已核对引用'
    panel.refresh('task-b')
    pending.pop()()
    panel.save_review()
    assert '任务已切换' in panel.dialog_error.value
    assert api.call_count == 2  # 只读取了两个任务的清单，没有写复核
    assert not page.pop_dialog.called


def test_late_reply_never_replaces_another_tasks_checklist():
    panel, api, pending, _, _ = fixture()
    # 回执顺序按实际执行顺序给出：task-b 的读取先返回，task-a 的旧读取最后返回。
    api.side_effect = [
        {'ok': True, 'checks': report('failed', 'pending')},
        {'ok': True, 'checks': report('satisfied', 'satisfied')},
    ]
    panel.refresh('task-a')
    panel.refresh('task-b')
    pending[1]()  # 新任务的回执先到
    pending[0]()  # 旧任务的迟到回执必须被丢弃
    blob = text(panel.content)
    assert '未通过' in blob
    assert '已核实' not in blob
    assert panel.task_id == 'task-b'


def test_reset_clears_previous_task_content_and_disables_review():
    panel, api, pending, _, _ = fixture()
    load(panel, api, pending)
    assert panel.items.controls
    panel.reset()
    assert panel.task_id is None
    assert panel.items.controls == []
    assert panel.review_button.disabled
    assert panel.empty.visible
    assert '没有打开的研究任务' in panel.empty.value
