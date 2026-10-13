from __future__ import annotations

from unittest import mock

import pytest

from src import config, context_file, prompts, settings, skills


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from src import workspace_features
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(workspace_features, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "_PATH", tmp_path / "settings.json")


def test_project_context_manifest_is_frozen_and_reports_budget(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "GEZHI.md").write_text("# 项目规则\n" + "尾部约束=" + "x" * 120, encoding="utf-8")
    (tmp_path / "GEZHI.md").write_text("# 应用规则\n不要被项目会话读取", encoding="utf-8")
    with mock.patch.object(config, "ROOT_DIR", tmp_path), mock.patch.object(
        context_file, "_cwd", return_value=tmp_path
    ):
        _, manifest = prompts.agent_system(
            "测试角色", stage="planning", context_budget=120,
            workspace_mode="research", project_root=project,
            project_id="project-a", return_manifest=True,
        )
    assert manifest["schema_version"] == 1
    assert manifest["basis"] == "frozen_prefix"
    assert manifest["project_id"] == "project-a"
    paths = [row.get("path", "") for row in manifest["sources"]]
    assert str(project / "GEZHI.md") in paths
    assert str(tmp_path / "GEZHI.md") not in paths
    assert any(row["status"] == "budget_deferred" for row in manifest["sources"])


def test_legacy_context_info_reports_truncation(tmp_path):
    context = tmp_path / "GEZHI.md"
    context.write_text("头\n" + "尾" * 3000, encoding="utf-8")
    with mock.patch.object(config, "ROOT_DIR", tmp_path), mock.patch.object(
        context_file, "_cwd", return_value=tmp_path
    ):
        info = context_file.load_sources(max_chars=200)
    assert info[0]["total_chars"] > info[0]["included_chars"]
    assert info[0]["truncated"] is True
    assert info[0]["status"] == "included"


def test_disabled_skill_cannot_be_loaded():
    skills.set_active("academic-writing", False)
    result = skills.load_skill_for_tool("academic-writing")
    assert result["ok"] is False
    assert result["status"] == "disabled"


def test_skill_paging_rejects_changed_body_and_manual_index(tmp_path, monkeypatch):
    folder = tmp_path / 'skills' / 'manual'
    folder.mkdir(parents=True)
    path = folder / 'SKILL.md'
    path.write_text('---\nname: manual\ndisable-model-invocation: true\nstages: planning\n---\nmanual instructions', encoding='utf-8')
    monkeypatch.setattr(skills, 'SKILLS_DIR', folder.parent)
    skills.set_active('manual', True)
    assert skills.load_skill_for_tool('manual')['status'] == 'manual_only'
    assert not skills.select_for_stage('planning')['selected']
    path.write_text('---\nname: manual\nstages: planning\n---\n0123456789', encoding='utf-8')
    first = skills.load_skill_for_tool('manual', max_chars=5)
    second = skills.load_skill_for_tool('manual', cursor=first['next_cursor'], sha256=first['sha256'])
    assert first['body'] + second['body'] == '0123456789'
    path.write_text('---\nname: manual\nstages: planning\n---\nchanged', encoding='utf-8')
    assert not skills.load_skill_for_tool('manual', cursor=5, sha256=first['sha256'])['ok']


def test_driver_captures_project_prefix_and_status_never_reassembles(tmp_path, monkeypatch):
    import json
    from src.agent_runtime import AgentRuntime
    from src.agent_driver import AdaptiveDriver
    from src import session_controls, project_workspace
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'GEZHI.md').write_text('ONLY_PROJECT_A_RULE', encoding='utf-8')
    app = tmp_path / 'application'
    app.mkdir()
    (app / 'GEZHI.md').write_text('UNRELATED_APPLICATION_RULE', encoding='utf-8')
    monkeypatch.setattr(config, 'ROOT_DIR', app)
    monkeypatch.setattr(context_file, '_cwd', lambda: app)
    monkeypatch.setattr(project_workspace, 'get_project', lambda ident: {'id': ident, 'path': str(project)})
    runtime = AgentRuntime(offline=True, llm_enabled=False, session_root=tmp_path / 'sessions')
    sid = 'manifest1'
    runtime.sessions.append(sid, 'session_created', {'project_id': 'a', 'workspace_mode': 'general'})
    runtime.sessions.append(sid, 'user_message', {'text': 'hello', 'mode': 'agent', 'agent': 'general', 'workspace_mode': 'general'})
    monkeypatch.setattr(AdaptiveDriver, '_identity', staticmethod(lambda: {'model': 'fixture', 'protocol': 'responses'}))
    monkeypatch.setattr(AdaptiveDriver, '_run', lambda self, state: state)
    driver = AdaptiveDriver(runtime)
    state = driver.start(sid, 'hello', 'general')
    assert 'ONLY_PROJECT_A_RULE' in state['messages'][0]['content']
    assert 'UNRELATED_APPLICATION_RULE' not in state['messages'][0]['content']
    assert state['context_manifest']['project_id'] == 'a'
    (project / 'GEZHI.md').write_text('NEW_NOT_LOADED', encoding='utf-8')
    monkeypatch.setattr(prompts, 'agent_system', mock.Mock(side_effect=AssertionError('status reassembled')))
    observed = session_controls.session_status(sid, store=runtime.sessions)
    assert observed['context_manifest']['sources'] == state['context_manifest']['sources']
    assert observed['workspace_mode'] == 'general'
    assert 'ONLY_PROJECT_A_RULE' not in json.dumps(observed)

    # Tool reads are observations, distinct from the frozen L1 skill index.
    call = {'id': 'skill-read', 'name': 'skills.load', 'arguments': {'name': 'academic-writing'}}
    driver._observe(state, call, skills.load_skill_for_tool('academic-writing'))
    loaded = session_controls.session_status(sid, store=runtime.sessions)['context_manifest']
    assert any(row['kind'] == 'skill_body' and row['status'] == 'loaded' for row in loaded['sources'])
    # A restarted driver reuses the original prefix and provenance.
    state['status'] = 'done'
    driver._save(state)
    runtime.sessions.append(sid, 'completed', {'status': 'done'})
    continued = AdaptiveDriver(runtime).reply(sid, 'follow up')
    assert continued['context_manifest'] == state['context_manifest']
    assert 'NEW_NOT_LOADED' not in continued['messages'][0]['content']


def test_manifest_public_whitelist_and_unknown_version():
    from src.context_manifest import public_manifest
    assert public_manifest({'schema_version': 99, 'body': 'private'}) is None
    value = public_manifest({'schema_version': 1, 'status': 'captured', 'body': 'private',
        'sources': [{'kind': 'memory', 'name': 'style', 'content': 'do not publish', 'used_chars': 22}]})
    assert 'body' not in value
    assert 'content' not in value['sources'][0]


def test_child_uses_parent_project_with_own_role(tmp_path, monkeypatch):
    from src.agent_runtime import AgentRuntime
    from src.agent_driver import AdaptiveDriver
    from src.agents.specs import get_spec
    from src import project_workspace
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'GEZHI.md').write_text('CHILD_PROJECT_RULE', encoding='utf-8')
    monkeypatch.setattr(project_workspace, 'get_project', lambda ident: {'id': ident, 'path': str(project)})
    runtime = AgentRuntime(offline=True, llm_enabled=False, session_root=tmp_path / 'sessions')
    runtime.sessions.append('parent', 'session_created', {'project_id': 'a', 'workspace_mode': 'general'})
    runtime.sessions.register_sidechain('child', 'parent')
    runtime.sessions.append('child', 'user_message', {'text': 'inspect', 'agent': 'explore'})
    monkeypatch.setattr(AdaptiveDriver, '_identity', staticmethod(lambda: {'model': 'fixture'}))
    monkeypatch.setattr(AdaptiveDriver, '_run', lambda self, state: state)
    state = runtime._run_child_driver('parent', 'child', get_spec('explore'), 'inspect')
    assert 'CHILD_PROJECT_RULE' in state['messages'][0]['content']
    assert state['context_manifest']['agent'] == 'explore'
    assert state['context_manifest']['workspace_mode'] == 'general'


def test_model_planner_uses_bound_project(tmp_path, monkeypatch):
    from src import llm, project_workspace
    from src.agent_runtime import AgentRuntime
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'GEZHI.md').write_text('PLAN_PROJECT_RULE', encoding='utf-8')
    monkeypatch.setattr(project_workspace, 'get_project', lambda ident: {'id': ident, 'path': str(project)})
    runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=tmp_path / 'sessions')
    runtime.sessions.append('plan1', 'session_created', {'project_id': 'a'})
    monkeypatch.setattr(llm, 'available', lambda *args: True)
    seen = []
    def chat(messages, **kwargs):
        seen.extend(messages)
        return {'steps': [{'tool': 'workspace.read', 'arguments': {}}]}
    monkeypatch.setattr(llm, 'chat_json', chat)
    outcome = runtime.send('plan1', 'plan', mode='plan')
    assert 'PLAN_PROJECT_RULE' in seen[0]['content']
    assert outcome['plan']['context_manifest']['project_id'] == 'a'


def test_implicit_project_rules_are_not_reattached_as_live_user_material(tmp_path, monkeypatch):
    from src import session_workspace, project_workspace, bridge, session_queue
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'GEZHI.md').write_text('RULE_ONLY_IN_FROZEN_PREFIX', encoding='utf-8')
    monkeypatch.setattr(project_workspace, 'get_project', lambda ident: {'id': ident, 'path': str(project)})
    session = session_workspace.handle({'cmd': 'chat_new', 'project_id': 'p'})
    sid = session['session_id']
    runtime = mock.Mock()
    runtime.send.return_value = {'session_id': sid, 'status': 'done'}
    monkeypatch.setattr(bridge, '_runtime', lambda **kw: runtime)
    result = session_workspace.handle({'cmd': 'chat_turn', 'session_id': sid, 'request': 'hello', 'online': True})
    assert result['ok']
    assert runtime.send.call_args.args[1] == 'hello'
    row = session_workspace._get(sid)
    assert session_queue._materials(row)[1] == []


def test_inspector_reports_actual_memory_chars_and_extension_budget(tmp_path, monkeypatch):
    from src import workspace_features
    monkeypatch.setattr(skills, 'SKILLS_DIR', tmp_path / 'empty-skills')
    (config.DATA_DIR).mkdir()
    (config.DATA_DIR / 'GEZHI.md').write_text('用户全局规则', encoding='utf-8')
    saved = workspace_features.handle({'cmd': 'memory_save', 'memory': {
        'title': 'writing', 'content': '用简洁中文', 'approved': True}})['memory']
    messages, manifest = prompts.build_with_manifest('request', context_budget=6000)
    actual = len(messages[0]['content']) - len(prompts.SYSTEM_PROMPT)
    assert manifest['budget']['used_chars'] == actual
    memory = next(row for row in manifest['sources'] if row['kind'] == 'memory')
    assert memory['used_chars'] > 0
    assert memory['sha256']
    # Edits without reapproval stop entering new prefixes and remain inspectable.
    workspace_features.handle({'cmd': 'memory_save', 'memory': {'id': saved['id'], 'content': '未批准的变更'}})
    messages, manifest = prompts.build_with_manifest('request')
    assert '未批准的变更' not in messages[0]['content']
    assert next(row for row in manifest['sources'] if row['kind'] == 'memory')['status'] == 'unapproved'


def test_skill_catalog_transports_metadata_without_bodies():
    from src import bridge
    listed = bridge.handle({'cmd': 'skills_list'})
    assert listed['ok'] and listed['skills']
    assert all('body' not in item and 'source' in item for item in listed['skills'])
    selection = bridge.handle({'cmd': 'skills_selection', 'stage': 'planning'})['selection']
    assert all('body' not in item for item in selection['selected'])


@pytest.mark.parametrize('context', ['fork # isolated execution', '"fork" # isolated execution'])
def test_yaml_comments_cannot_bypass_skill_context_restrictions(tmp_path, monkeypatch, context):
    folder = tmp_path / 'skills' / 'isolated'
    folder.mkdir(parents=True)
    (folder / 'SKILL.md').write_text(
        f'---\nname: isolated\nstages: planning\ncontext: {context}\n---\nprivate body', encoding='utf-8')
    monkeypatch.setattr(skills, 'SKILLS_DIR', folder.parent)
    assert skills.list_skills()[0]['invocation_status'] == 'unsupported'
    result = skills.load_skill_for_tool('isolated')
    assert result['status'] == 'unsupported' and not result['ok']
    assert 'body' not in result


def test_skill_yaml_scalars_stage_arrays_and_multiline_descriptions(tmp_path, monkeypatch):
    folder = tmp_path / 'skills' / 'fixture'
    folder.mkdir(parents=True)
    path = folder / 'SKILL.md'
    path.write_text('---\nname: "fixture"\ndescription: >-\n  first line\n  second line\n'
                    'stages: [planning, audit]\ndisable-model-invocation: false # allow model\n'
                    'user-invocable: false # hidden from manual menu\n'
                    'argument-hint: "#issue" # keep the quoted hash\n---\nbody', encoding='utf-8')
    monkeypatch.setattr(skills, 'SKILLS_DIR', folder.parent)
    skill = skills.list_skills()[0]
    assert skill['name'] == 'fixture'
    assert skill['description'] == 'first line second line'
    assert skill['argument_hint'] == '#issue'
    assert skill['user_invocable'] is False and skill['disable_model_invocation'] is False
    assert skills.select_for_stage('planning')['selected'][0]['name'] == 'fixture'
    assert skills.load_skill_for_tool('fixture')['ok']
    path.write_text('---\nname: fixture\ndisable-model-invocation: true # prohibit\n---\nbody', encoding='utf-8')
    assert skills.load_skill_for_tool('fixture')['status'] == 'manual_only'


def test_invalid_skill_invocation_metadata_is_not_executable(tmp_path, monkeypatch):
    folder = tmp_path / 'skills' / 'fixture'
    folder.mkdir(parents=True)
    (folder / 'SKILL.md').write_text('---\nname: fixture\ndisable-model-invocation: [false]\n---\nbody', encoding='utf-8')
    monkeypatch.setattr(skills, 'SKILLS_DIR', folder.parent)
    assert not skills.list_skills()
    assert not skills.load_skill_for_tool('fixture')['ok']


def test_planned_session_exposes_distinct_bounded_research_request_contexts(tmp_path, monkeypatch):
    import json
    from src.agent_runtime import SessionStore
    from src.state_store import write_json
    from src import session_controls
    store = SessionStore(tmp_path / 'sessions')
    sid = 'research-observation'
    store.append(sid, 'user_message', {'text': 'research', 'mode': 'agent', 'agent': 'extraction'})
    store.append(sid, 'completed', {'status': 'done'})
    path = store.root / f'{sid}.research-contexts.json'
    manifest = {'schema_version': 1, 'basis': 'frozen_prefix', 'status': 'captured',
                'project_id': 'project-a', 'body': 'PRIVATE_PREFIX', 'sources': [
                    {'kind': 'memory', 'name': f'memory-{i}', 'status': 'included',
                     'content': 'PRIVATE_MEMORY'} for i in range(30)]}
    rows = [{'request_id': f'request-{i}', 'node': 'extract', 'prompt_key': 'extract_one.v3',
             'outcome': 'returned', 'observed_at': '2026-10-06T00:00:00Z',
             'context_manifest': manifest, 'response': 'PRIVATE_MODEL_RESPONSE'} for i in range(16)]
    write_json(path, {'schema_version': 1, 'entries': rows, 'omitted_entries': 2})
    monkeypatch.setattr(prompts, 'build_with_manifest', mock.Mock(side_effect=AssertionError('reassembled')))
    status = session_controls.session_status(sid, store=store)
    assert status['context_manifest'] is None
    observed = status['research_contexts']
    assert [row['request_id'] for row in observed['entries']] == [f'request-{i}' for i in range(12, 16)]
    assert observed['omitted_entries'] == 14
    assert len(observed['entries'][0]['context_manifest']['sources']) == 24
    assert observed['entries'][0]['context_manifest']['omitted_sources'] == 6
    assert 'PRIVATE_' not in json.dumps(status)
    with mock.patch.object(session_controls, '_load_research_contexts', side_effect=AssertionError('reread')):
        assert session_controls.session_status(sid, store=store)['research_contexts'] == observed


def test_research_request_context_read_error_is_explicit_and_can_recover(tmp_path):
    from src.agent_runtime import SessionStore
    from src import session_controls
    from src.state_store import write_json
    store = SessionStore(tmp_path / 'sessions')
    sid = 'research-corrupt'
    store.append(sid, 'user_message', {'text': 'research'})
    path = store.root / f'{sid}.research-contexts.json'
    assert session_controls.session_status(sid, store=store)['research_contexts'] is None
    path.write_text('PRIVATE_BROKEN_FILE', encoding='utf-8')
    unavailable = session_controls.session_status(sid, store=store)['research_contexts']
    assert unavailable['issue'] and not unavailable['entries']
    assert 'PRIVATE_' not in str(unavailable)
    write_json(path, {'schema_version': 1, 'entries': [], 'omitted_entries': 0})
    assert not session_controls.session_status(sid, store=store)['research_contexts'].get('issue')
