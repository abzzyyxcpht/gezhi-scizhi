"""Main session and internal research-model provenance remain distinct and safe."""
from types import SimpleNamespace
from unittest.mock import Mock


def _manifest():
    return {
        'schema_version': 1, 'status': 'captured', 'basis': 'frozen_prefix',
        'project_id': 'inner-project', 'agent': 'research', 'stage': 'extraction',
        'sources': [{'kind': 'instructions', 'name': 'GEZHI.md', 'scope': 'project',
                     'status': 'included', 'path': 'D:/inner-project/GEZHI.md',
                     'body': 'PRIVATE SOURCE BODY'}],
        'system_prompt': 'PRIVATE SYSTEM PROMPT',
    }


def _entry(index=1, outcome='returned'):
    return {
        'request_id': f'inner-{index}', 'node': 'extract', 'prompt_key': 'evidence_extract',
        'outcome': outcome, 'observed_at': '2026-10-06T16:20:30+08:00',
        'context_manifest': _manifest(), 'raw_response': 'PRIVATE MODEL RESPONSE',
    }


def test_research_records_distinguish_main_prefix_return_and_error_without_body():
    from src.ui.session_status import session_context_lines

    row = {'context_manifest': None, 'research_contexts': {
        'schema_version': 1, 'entries': [_entry(), _entry(2, 'error')], 'omitted_entries': 0,
    }}
    text = '\n'.join(session_context_lines(row))
    for fragment in ('主会话前缀', '未记录（旧会话或规则流程）', '近期工具内部模型请求',
                     'inner-1', 'extract', 'evidence_extract', '请求已返回', '请求失败',
                     '2026-10-06T16:20:30', 'inner-project', 'GEZHI.md'):
        assert fragment in text
    assert 'PRIVATE' not in text
    assert '已通过验证' not in text and '已采用' not in text


def test_empty_records_preserve_legacy_display_and_read_failure_is_visible():
    from src.ui.session_status import context_manifest_lines, session_context_lines

    for research in (None, {'schema_version': 1, 'entries': []}, {'schema_version': 9, 'entries': [_entry()]}):
        assert session_context_lines({'context_manifest': _manifest(), 'research_contexts': research}) == context_manifest_lines(_manifest())
    failure = '\n'.join(session_context_lines({'research_contexts': {
        'schema_version': 1, 'entries': [], 'omitted_entries': 0,
        'issue': '工具内部模型请求记录暂不可读，请稍后重试。 api_key=fixture-secret',
    }}))
    assert '暂不可读，请稍后重试' in failure
    assert 'fixture-secret' not in failure
    assert '没有调用' not in failure and '请求已返回' not in failure


def test_research_records_keep_latest_four_and_combine_omissions():
    from src.ui.session_status import session_context_lines

    row = {'research_contexts': {'schema_version': 1,
        'entries': [_entry(i) for i in range(7)], 'omitted_entries': 5}}
    text = '\n'.join(session_context_lines(row))
    assert 'inner-0' not in text and 'inner-2' not in text
    assert text.index('inner-3') < text.index('inner-6')
    assert '另有 8 条工具内部模型请求未显示' in text


def test_planned_panel_with_only_inner_request_manifest_is_visible():
    from src.ui.session_status import SessionStatusPanel

    queued = []
    page = SimpleNamespace(update=Mock(), run_thread=queued.append)
    row = {'session_id': 'planned-inner', 'status': 'done', 'driver': 'planned',
        'workspace_mode': 'research', 'agent': 'research', 'mode': 'agent',
        'context_manifest': None,
        'research_contexts': {'schema_version': 1, 'entries': [_entry()]}}
    panel = SessionStatusPanel(page, lambda: 'planned-inner',
        api=Mock(return_value={'ok': True, 'status': row}))
    panel.refresh(force=True)
    queued.pop()()
    assert panel.context_sources.visible
    assert panel.context_sources.expanded is False
    assert '规则流程' in panel.summary.value
    assert '近期工具内部模型请求' in panel.context_source_detail.value
    assert 'inner-project' in panel.context_source_detail.value
    assert 'PRIVATE' not in panel.context_source_detail.value
