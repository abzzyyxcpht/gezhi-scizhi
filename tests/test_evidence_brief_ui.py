"""Recorded evidence is readable, scoped and never auto-submitted as a new task."""
import importlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import flet as ft


def brief(task_id=None):
    return {'scope': {'task_id': task_id}, 'total_records': 1, 'visible_records': 1,
        'records_truncated': False, 'boundary': '关系来自录入者判断，尚未执行科学验证。',
        'records': [{'id': 'hypothesis1', 'version': 3, 'title': '检索是否可靠', 'question': '可靠性边界是什么？',
            'kind': 'hypothesis', 'status': 'planned', 'evidence_truncated': False,
            'counts': {'supports': 1, 'challenges': 1, 'context': 0, 'synthetic': 1, 'unique_sources': 2},
            'gaps': ['尚未完成实验复现'], 'followup_draft': '请核对反驳材料的适用范围并给出出处。',
            'provenance': {'scientific_verification': 'not_performed', 'evidence_relations': 'user_asserted'},
            'groups': {'supports': [{'source': {'id': 'paper1', 'title': '支持材料', 'synthetic': False},
                'quote': '支持原文', 'scope': 'abstract', 'locator': '摘要', 'note': '', 'synthetic': False, 'quote_truncated': False}],
                'challenges': [{'source': {'id': 'sample2', 'synthetic': True}, 'quote': '反驳原文',
                    'scope': 'full_text', 'locator': '第 2 页', 'note': '', 'synthetic': True, 'quote_truncated': True}],
                'context': []}}]}


def fixture(task_id=None):
    assert importlib.util.find_spec('src.ui.evidence_brief') is not None, 'evidence brief UI is not implemented'
    module = importlib.import_module('src.ui.evidence_brief')
    selected = {'task_id': task_id}
    pending, dialogs = [], []
    page = SimpleNamespace(update=Mock(), run_thread=pending.append, show_dialog=dialogs.append, pop_dialog=Mock())
    request, api = ft.TextField(value=''), Mock(return_value={'ok': True, 'brief': brief(task_id)})
    panel = module.EvidenceBriefPanel(page, request, lambda: selected['task_id'], api=api)
    return panel, request, api, pending, dialogs, selected


def texts(control):
    found = []
    if isinstance(getattr(control, 'value', None), str):
        found.append(control.value)
    for name in ('controls', 'content', 'title', 'subtitle'):
        value = getattr(control, name, None)
        for child in value if isinstance(value, list) else [value]:
            if isinstance(child, ft.Control):
                found.append(texts(child))
    return '\n'.join(found)


def test_task_scope_and_evidence_material_limits_are_visible():
    panel, _, api, work, _, selected = fixture('task-a')
    panel.open()
    selected['task_id'] = 'task-b'
    work.pop()()
    api.assert_called_once_with({'cmd': 'evidence_brief', 'task_id': 'task-a'})
    assert 'task-a' in panel.scope_label.value
    value = texts(panel.rows)
    for fragment in ('支持原文', '反驳原文', '摘要', '全文', '合成', '截断', 'paper1', '第 2 页'):
        assert fragment in value
    assert '科学验证' in panel.boundary.value


def test_no_task_means_explicit_all_records_scope():
    panel, _, api, work, _, _ = fixture()
    panel.open()
    work.pop()()
    api.assert_called_once_with({'cmd': 'evidence_brief'})
    assert '全部记录' in panel.scope_label.value


def test_relation_filter_only_changes_visible_materials_without_another_read():
    panel, _, api, work, _, _ = fixture()
    panel.open()
    work.pop()()
    panel.relation.value = 'challenges'
    panel.relation.on_select(None)
    assert '反驳原文' in texts(panel.rows)
    assert '支持原文' not in texts(panel.rows)
    assert api.call_count == 1


def test_followup_only_fills_an_empty_draft_and_never_sends_or_queues():
    panel, request, api, work, _, _ = fixture()
    panel.open()
    work.pop()()
    request.value = '已有草稿'
    panel.as_next_question()
    assert request.value == '已有草稿' and '保留' in panel.notice.value
    request.value = ''
    panel.as_next_question()
    assert request.value == brief()['records'][0]['followup_draft']
    assert '尚未发送' in panel.notice.value
    assert api.call_count == 1 and not work


def test_closed_dialog_ignores_late_read_results():
    panel, _, _, work, _, _ = fixture()
    panel.open()
    panel.close()
    work.pop()()
    assert panel.records == []


def test_real_brief_bridge_renders_scoped_records_without_modifying_the_notebook():
    from src import bridge, config, research_notebook
    with tempfile.TemporaryDirectory(prefix='scizhi-ui-evidence-') as directory:
        root = Path(directory)
        with patch.object(config, 'DATA_DIR', root):
            created = research_notebook.handle({'cmd': 'notebook_save', 'record': {
                'kind': 'hypothesis', 'title': '真实持久记录', 'question': '范围是什么？',
                'prediction': '需要检验', 'task_id': 'task-a', 'evidence': [{
                    'relation': 'challenges', 'source': {'id': 'W1'}, 'quote': '反例原文', 'scope': 'fulltext'}]}})
            assert created['ok']
            before = {path.name: path.read_bytes() for path in root.glob('*.json')}
            panel, request, _, work, _, _ = fixture('task-a')
            panel.api = bridge.handle
            panel.open()
            work.pop()()
            assert '反例原文' in texts(panel.rows) and '全文' in texts(panel.rows)
            panel.as_next_question()
            assert created['record']['id'] in request.value
            assert before == {path.name: path.read_bytes() for path in root.glob('*.json')}
