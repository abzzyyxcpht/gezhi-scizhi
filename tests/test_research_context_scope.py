"""Synthetic internal research calls inherit scope without process-global state."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

import pytest

from src import config, context_file, llm, project_workspace, settings, skills, workspace_features
from src.agent_runtime import AgentRuntime
from src.agents import literature


WORK = {'id': 'synthetic-1', 'title': 'Synthetic paper', 'abstract': 'We propose a synthetic method.',
        'demo': True, 'source_type': 'synthetic_demo'}
SECTIONS = [{'title': f'Section {i}', 'points': 'Synthetic summary', 'refs': [WORK['title']]}
            for i in range(3)]


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    data, app = tmp_path / 'data', tmp_path / 'application'
    data.mkdir()
    app.mkdir()
    monkeypatch.setattr(config, 'DATA_DIR', data)
    monkeypatch.setattr(config, 'ROOT_DIR', app)
    monkeypatch.setattr(context_file, '_cwd', lambda: app)
    monkeypatch.setattr(workspace_features, 'DATA_DIR', data)
    monkeypatch.setattr(settings, '_PATH', tmp_path / 'settings.json')
    monkeypatch.setattr(skills, 'SKILLS_DIR', tmp_path / 'empty-skills')
    monkeypatch.setattr(llm, 'available', lambda *a, **kw: True)
    monkeypatch.setattr(llm, 'chat_json', mock.Mock(side_effect=AssertionError('unexpected model request')))
    monkeypatch.setattr(literature, '_dual_source_search', mock.Mock(side_effect=AssertionError('no live search')))
    (app / 'GEZHI.md').write_text('UNRELATED_APPLICATION_RULE_CANARY', encoding='utf-8')
    projects = {}
    runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=tmp_path / 'sessions')
    for project_id in ('a', 'b'):
        project = tmp_path / f'project-{project_id}'
        project.mkdir()
        (project / 'GEZHI.md').write_text(f'PROJECT_{project_id.upper()}_RULE_CANARY', encoding='utf-8')
        projects[project_id] = {'id': project_id, 'path': str(project)}
        runtime.sessions.append(project_id, 'session_created', {'project_id': project_id, 'workspace_mode': 'research'})
        runtime.sessions.append(project_id, 'user_message', {'text': 'Synthetic task', 'agent': 'research', 'mode': 'agent'})
        runtime._workspaces[project_id] = {'topic': 'Synthetic topic', 'pool': [dict(WORK)], 'records': [dict(WORK)]}
    monkeypatch.setattr(project_workspace, 'get_project', lambda ident: projects[ident])
    settings.save({'agent_execution': 'planned'})
    return SimpleNamespace(runtime=runtime, root=tmp_path, projects=projects)


def _records(scoped, sid):
    return json.loads((scoped.root / 'sessions' / f'{sid}.research-contexts.json').read_text(encoding='utf-8'))


def _assert_project(messages, project_id):
    system = messages[0]['content']
    assert f'PROJECT_{project_id.upper()}_RULE_CANARY' in system
    assert f'PROJECT_{"B" if project_id == "a" else "A"}_RULE_CANARY' not in system
    assert 'UNRELATED_APPLICATION_RULE_CANARY' not in system


def test_direct_extraction_uses_journal_scope_and_expert_memory(scoped, monkeypatch):
    workspace_features.handle({'cmd': 'memory_save', 'memory': {
        'id': 'expert-a', 'title': 'expert preference', 'content': 'EXTRACTION_A_MEMORY_CANARY',
        'scope': 'role', 'role': 'extraction', 'project_id': 'a', 'approved': True}})
    chat = mock.Mock(return_value={'method': WORK['abstract']})
    monkeypatch.setattr(llm, 'chat_json', chat)
    result = scoped.runtime._run_named('extraction', {'__session_id': 'a', 'request': 'Extract',
        'project_id': 'b', 'role': 'critic', 'research_context': {'project_id': 'b'}})
    assert result['ok']
    _assert_project(chat.call_args.args[0], 'a')
    assert 'EXTRACTION_A_MEMORY_CANARY' in chat.call_args.args[0][0]['content']
    record = _records(scoped, 'a')['entries'][0]
    assert record['node'] == 'extract' and record['outcome'] == 'returned'
    assert record['context_manifest']['project_id'] == 'a'
    assert record['context_manifest']['agent'] == 'extraction'
    assert 'EXTRACTION_A_MEMORY_CANARY' not in json.dumps(record)
    assert 'PROJECT_A_RULE_CANARY' not in json.dumps(record)


def test_planned_child_internal_request_keeps_parent_project(scoped, monkeypatch):
    chat = mock.Mock(return_value={'method': WORK['abstract']})
    monkeypatch.setattr(llm, 'chat_json', chat)
    result = scoped.runtime._subagent_spawn({'__session_id': 'a', 'agent': 'extraction', 'request': 'Extract'})
    assert result['ok']
    _assert_project(chat.call_args.args[0], 'a')
    record = _records(scoped, result['child_session'])['entries'][0]
    assert record['context_manifest']['project_id'] == 'a'
    assert record['context_manifest']['agent'] == 'extraction'


def test_synthesis_and_literature_requests_use_bound_context(scoped, monkeypatch):
    chat = mock.Mock(side_effect=[{'plan': ['Group supplied evidence']}, {'sections': SECTIONS},
                                 {'reviews': [{'idx': 0, 'keep': True, 'reason': 'Synthetic relevance'}]}])
    monkeypatch.setattr(llm, 'chat_json', chat)
    assert scoped.runtime._run_named('synthesis', {'__session_id': 'a', 'request': 'Synthesize'})['ok']
    monkeypatch.setattr(literature, '_dual_source_search', lambda *a, **kw: ([dict(WORK)], {'openalex': 1, 'crossref': 0}))
    monkeypatch.setattr(literature, '_rank_candidates', lambda *a, **kw: ([0.9], {'tier': 'lexical'}))
    monkeypatch.setattr(config, 'LIT_MAX_ROUND', 1)
    assert scoped.runtime._run_named('literature', {'__session_id': 'a', 'request': 'Search'})['ok']
    assert chat.call_count == 3
    for call in chat.call_args_list:
        _assert_project(call.args[0], 'a')
    records = _records(scoped, 'a')['entries']
    assert [row['context_manifest']['agent'] for row in records] == ['synthesis', 'synthesis', 'literature']


def test_parallel_project_requests_do_not_share_scope(scoped, monkeypatch):
    entered = threading.Barrier(2)
    captured = []
    def chat(messages, **kwargs):
        entered.wait(timeout=5)
        captured.append(messages)
        return {'method': WORK['abstract']}
    monkeypatch.setattr(llm, 'chat_json', chat)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda sid: scoped.runtime._run_named('extraction',
            {'__session_id': sid, 'request': 'Extract'}), ('a', 'b')))
    assert all(row['ok'] for row in outcomes)
    assert len(captured) == 2
    for project_id in ('a', 'b'):
        matching = [msgs for msgs in captured if f'PROJECT_{project_id.upper()}_RULE_CANARY' in msgs[0]['content']]
        assert len(matching) == 1
        _assert_project(matching[0], project_id)
        assert _records(scoped, project_id)['entries'][0]['context_manifest']['project_id'] == project_id


def test_planned_parallel_child_paths_propagate_scope(scoped, monkeypatch):
    monkeypatch.setattr(llm, 'chat_json', lambda messages, **kw: (
        _assert_project(messages, 'a') or {'method': WORK['abstract'], 'reviews': [{'idx': 0, 'keep': True}]}))
    monkeypatch.setattr(literature, '_dual_source_search', lambda *a, **kw: ([dict(WORK)], {'openalex': 1, 'crossref': 0}))
    monkeypatch.setattr(literature, '_rank_candidates', lambda *a, **kw: ([0.9], {'tier': 'lexical'}))
    result = scoped.runtime._subagent_fanout({'__session_id': 'a', 'agents': ['literature', 'extraction'],
                                             'requests': ['Search', 'Extract']})
    assert result['ok']
    for child in scoped.runtime._children['a']:
        assert _records(scoped, child)['entries'][0]['context_manifest']['project_id'] == 'a'


def test_internal_context_records_only_attempted_calls_and_are_bounded(scoped, monkeypatch):
    from src import prompts, research_context
    binding = research_context.binding_for_session(scoped.runtime.sessions, 'a')
    prompts.extract_one('Synthetic topic', WORK, context_scope=binding, return_manifest=True)
    assert not (scoped.root / 'sessions' / 'a.research-contexts.json').exists()
    monkeypatch.setattr(llm, 'available', lambda *a, **kw: False)
    scoped.runtime._run_named('extraction', {'__session_id': 'a', 'request': 'Offline extraction'})
    assert not (scoped.root / 'sessions' / 'a.research-contexts.json').exists()
    monkeypatch.setattr(llm, 'available', lambda *a, **kw: True)
    monkeypatch.setattr(llm, 'chat_json', mock.Mock(side_effect=RuntimeError('PRIVATE_PROVIDER_ERROR')))
    scoped.runtime._run_named('extraction', {'__session_id': 'a', 'request': 'Failed extraction'})
    assert _records(scoped, 'a')['entries'][0]['outcome'] == 'error'
    assert 'PRIVATE_PROVIDER_ERROR' not in json.dumps(_records(scoped, 'a'))
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: {'method': WORK['abstract']})
    for _ in range(18):
        scoped.runtime._run_named('extraction', {'__session_id': 'a', 'request': 'Extract'})
    records = _records(scoped, 'a')
    assert len(records['entries']) == 16 and records['omitted_entries'] == 3
    assert len({row['request_id'] for row in records['entries']}) == 16


def test_receipt_failure_preserves_success_and_original_provider_error(scoped, monkeypatch, caplog):
    from src import research_context
    binding = research_context.binding_for_session(scoped.runtime.sessions, 'a')
    monkeypatch.setattr(research_context, '_record', mock.Mock(side_effect=OSError('PRIVATE_PATH_CANARY')))
    response = {'method': 'synthetic response'}
    chat = mock.Mock(return_value=response)
    monkeypatch.setattr(llm, 'chat_json', chat)
    result = research_context.chat_json([], node='extract', prompt_key='synthetic', tier='extraction',
                                        context_scope=binding, context_manifest=None)
    assert result is response and chat.call_count == 1
    provider_error = RuntimeError('PRIVATE_PROVIDER_CANARY')
    chat.side_effect = provider_error
    with pytest.raises(RuntimeError) as caught:
        research_context.chat_json([], node='extract', prompt_key='synthetic', tier='extraction',
                                   context_scope=binding, context_manifest=None)
    assert caught.value is provider_error and chat.call_count == 2
    assert '科研上下文' in caplog.text
    assert 'PRIVATE_PATH_CANARY' not in caplog.text and 'PRIVATE_PROVIDER_CANARY' not in caplog.text


def test_concurrent_receipts_for_one_session_are_not_lost(scoped, monkeypatch):
    from src import research_context
    binding = research_context.binding_for_session(scoped.runtime.sessions, 'a')
    entered = threading.Barrier(2)
    def chat(*args, **kwargs):
        entered.wait(timeout=5)
        return {}
    monkeypatch.setattr(llm, 'chat_json', chat)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda index: research_context.chat_json([], node=f'node-{index}',
            prompt_key='synthetic', tier='extraction', context_scope=binding,
            context_manifest={'schema_version': 1, 'project_id': 'a'}), range(2)))
    entries = _records(scoped, 'a')['entries']
    assert {row['node'] for row in entries} == {'node-0', 'node-1'}
    assert len({row['request_id'] for row in entries}) == 2
