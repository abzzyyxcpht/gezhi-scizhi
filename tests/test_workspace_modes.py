"""General/research boundaries with isolated settings and no provider requests."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src import config, settings, command_center, prompts, session_workspace, llm
from src.agent_runtime import AgentRuntime, SessionStore
from src.agent_launch import AgentLauncher
from src.bridge import handle
from tests.test_adaptive_driver import response


class WorkspaceModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for obj, name, value in ((config, 'DATA_DIR', self.root),
                                 (settings, '_PATH', self.root / 'settings.json')):
            patcher = mock.patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def runtime(self):
        return AgentRuntime(session_root=self.root / 'sessions', llm_enabled=False)

    def test_general_tools_exclude_research_and_keep_write_approval(self):
        runtime = self.runtime()
        result = runtime.start('查看项目', agent='general')
        sid = result['session_id']
        tools = {row['function']['name'] for row in runtime.tools_schema(sid)}
        self.assertTrue({'file.read', 'file.edit', 'shell.run', 'skills.load'} <= tools)
        self.assertFalse({'research.create', 'research.run', 'literature.run', 'notebook.save'} & tools)
        self.assertEqual('ask', runtime._agent_gate(sid, 'file.write', 'write'))
        self.assertEqual('deny', runtime._agent_gate(sid, 'research.create', 'write'))

    def test_general_offline_never_creates_a_research_task(self):
        runtime = self.runtime()
        with mock.patch('src.service.create_task', side_effect=AssertionError('unexpected research pipeline')):
            result = runtime.start('帮我修复 Python 程序', agent='general')
        self.assertEqual('general', result['agent'])
        self.assertIn('模型', result['content'])
        tools = [e['payload']['name'] for e in runtime.sessions.read(result['session_id'])
                 if e['type'] == 'tool_call']
        self.assertEqual(['workspace.read'], tools)

    def test_general_system_does_not_inject_research_memory_or_identity(self):
        with mock.patch('src.skills.select_for_stage', return_value={
                'selected': [], 'deferred': [], 'missing': [], 'budget': {'used_chars': 0}}), \
                mock.patch('src.context_file.load_body', return_value=''), \
                mock.patch('src.workspace_features.memory_context', return_value='RESEARCH MEMORY') as memory:
            system = prompts.agent_system('通用智能体', workspace_mode='general')
        self.assertIn('通用', system)
        self.assertNotIn('高校科研场景的科研助理', system)
        self.assertNotIn('RESEARCH MEMORY', system)
        memory.assert_not_called()

    def test_mode_bridge_persists_and_rejects_invalid_values(self):
        self.assertEqual('research', handle({'cmd': 'workbench_mode'})['mode'])
        result = handle({'cmd': 'workbench_mode', 'action': 'set', 'mode': 'general'})
        self.assertTrue(result['ok'], result)
        self.assertEqual('general', settings.load()['workspace_mode'])
        self.assertEqual('general', result['agent'])
        self.assertFalse(handle({'cmd': 'workbench_mode', 'action': 'set', 'mode': 'invalid'})['ok'])
        self.assertEqual('general', settings.load()['workspace_mode'])

    def test_mode_compare_and_swap_rejects_stale_and_aba_writers(self):
        initial = handle({'cmd': 'workbench_mode'})
        first = handle({'cmd': 'workbench_mode', 'mode': 'general', 'expected_revision': initial['revision']})
        self.assertTrue(first['ok'], first)
        self.assertGreater(first['revision'], initial['revision'])
        settings.save({'workspace_mode': 'research'})
        stale = handle({'cmd': 'workbench_mode', 'mode': 'general', 'expected_revision': initial['revision']})
        self.assertFalse(stale['ok'])
        self.assertEqual('stale_revision', stale['code'])
        self.assertEqual('research', settings.load()['workspace_mode'])
        refreshed = handle({'cmd': 'workbench_mode'})
        retried = handle({'cmd': 'workbench_mode', 'mode': 'general', 'expected_revision': refreshed['revision']})
        self.assertTrue(retried['ok'], retried)

    def test_new_bridge_launch_uses_current_workspace_mode(self):
        settings.save({'workspace_mode': 'general'})
        runtime = mock.Mock()
        runtime.start.return_value = {'status': 'done'}
        with mock.patch('src.bridge._runtime', return_value=runtime):
            self.assertTrue(handle({'cmd': 'agent_run', 'request': '检查文件'})['ok'])
        runtime.start.assert_called_once_with('检查文件', 'agent', 'general', workspace_mode='general')

    def test_existing_offline_session_rejects_role_changes(self):
        runtime = self.runtime()
        sid = runtime.start('查看工作区', agent='general')['session_id']
        settings.save({'workspace_mode': 'research'})
        with self.assertRaisesRegex(ValueError, '新建|分叉'):
            runtime.send(sid, '继续', agent='research')
        self.assertEqual('general', runtime.sessions.snapshot(sid)['agent'])

    def test_draft_and_fork_keep_the_saved_workspace_mode(self):
        settings.save({'workspace_mode': 'general'})
        created = session_workspace.handle({'cmd': 'chat_new'})
        sid = created['session_id']
        self.assertEqual('general', created['session']['workspace_mode'])
        self.assertEqual('general', created['session']['agent'])
        settings.save({'workspace_mode': 'research'})
        forked = session_workspace.handle({'cmd': 'chat_fork', 'session_id': sid})
        self.assertEqual('general', forked['session']['agent'])
        switched = session_workspace.handle({'cmd': 'chat_fork', 'session_id': sid, 'workspace_mode': 'research'})
        self.assertEqual('research', switched['session']['agent'])

    def test_shared_catalog_exposes_groups_modes_and_complete_commands(self):
        result = handle({'cmd': 'command_catalog', 'workspace_mode': 'general'})
        self.assertTrue(result['ok'], result)
        commands = {row['name']: row for row in result['commands']}
        self.assertIn('mode', commands)
        self.assertIn('research', commands['new']['modes'])
        self.assertNotIn('general', commands['new']['modes'])
        self.assertIn('general', commands['agent']['modes'])
        self.assertTrue(result['groups'])

    def test_mode_command_dispatches_through_bridge(self):
        with mock.patch('src.bridge.handle', return_value={'ok': True}) as dispatch:
            result = command_center.execute('/mode general')
        self.assertTrue(result['ok'], result)
        dispatch.assert_called_once_with({'cmd': 'workbench_mode', 'action': 'set', 'mode': 'general'})

    def test_background_continuation_publishes_same_session_and_is_idempotent(self):
        settings.save({'workspace_mode': 'general'})
        sid = session_workspace.handle({'cmd': 'chat_new'})['session_id']
        runtime = self.runtime()
        launcher = AgentLauncher()
        release = threading.Event()
        entered = threading.Event()
        original = runtime.plan

        def delayed(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args)

        with mock.patch('src.bridge._runtime', return_value=runtime), \
                mock.patch.object(runtime, 'plan', side_effect=delayed), \
                mock.patch.object(runtime, 'send', wraps=runtime.send) as send:
            try:
                first = launcher.launch(runtime, '检查项目', agent='general',
                                        session_id=sid, launch_id='continue-once')
                self.assertTrue(entered.wait(5))
                self.assertEqual('running', runtime.sessions.snapshot(sid)['status'])
                second = launcher.launch(runtime, '检查项目', agent='general',
                                         session_id=sid, launch_id='continue-once')
                self.assertEqual(sid, first['session_id'])
                self.assertEqual(sid, second['session_id'])
                self.assertEqual(1, send.call_count)
                self.assertFalse(launcher.status(sid)['finished'])
            finally:
                release.set()
                self.assertTrue(launcher.join(sid, 5))
        self.assertEqual('general', launcher.status(sid)['result']['agent'])
        messages = [e for e in runtime.sessions.read(sid) if e['type'] == 'user_message']
        self.assertEqual(1, len(messages))

    def test_general_review_remains_general_and_read_only(self):
        runtime = self.runtime()
        result = runtime.start('检查代码', mode='review', agent='general')
        self.assertEqual('general', result['agent'])
        self.assertEqual('deny', runtime._agent_gate(result['session_id'], 'shell.run', 'write'))

    def test_explicit_workspace_purpose_survives_specialist_role_and_followup(self):
        created = session_workspace.handle({'cmd': 'chat_new', 'workspace_mode': 'general', 'agent': 'explore'})
        self.assertTrue(created['ok'], created)
        sid = created['session_id']
        self.assertEqual('general', created['session']['workspace_mode'])
        runtime = self.runtime()
        with mock.patch.object(runtime, 'plan', return_value={'steps': [], 'mode': 'agent', 'agent': 'explore'}):
            first = runtime.send(sid, '检查项目', agent='explore', workspace_mode='general')
            settings.save({'workspace_mode': 'research'})
            second = runtime.send(sid, '继续检查', agent='explore')
        self.assertEqual('general', first['workspace_mode'])
        self.assertEqual('general', second['workspace_mode'])
        self.assertEqual('general', runtime.sessions.snapshot(sid)['workspace_mode'])
        messages = [e['payload'] for e in runtime.sessions.read(sid) if e['type'] == 'user_message']
        self.assertEqual(['general', 'general'], [e['workspace_mode'] for e in messages])
        with self.assertRaisesRegex(ValueError, '新建|分叉'):
            runtime.send(sid, '改变用途', agent='explore', workspace_mode='research')

    def test_native_workspace_purpose_survives_driver_reply_and_restart(self):
        runtime = AgentRuntime(session_root=self.root / 'sessions', offline=False, llm_enabled=True)
        with mock.patch.object(llm, 'available', return_value=True), \
                mock.patch.object(llm, 'chat', return_value=response('已检查')):
            first = runtime.start('检查代码', agent='explore', workspace_mode='general')
            resumed = AgentRuntime(session_root=self.root / 'sessions', offline=False, llm_enabled=True)
            second = resumed.send(first['session_id'], '继续检查', agent='explore')
        self.assertEqual('general', first['workspace_mode'])
        self.assertEqual('general', second['workspace_mode'])
        self.assertEqual('general', resumed.sessions.snapshot(first['session_id'])['workspace_mode'])

    def test_fork_preserves_specialist_role_execution_and_workspace_purpose(self):
        created = session_workspace.handle({'cmd': 'chat_new', 'workspace_mode': 'general',
                                             'agent': 'explore', 'mode': 'plan'})
        self.assertTrue(created['ok'], created)
        forked = session_workspace.handle({'cmd': 'chat_fork', 'session_id': created['session_id']})
        self.assertTrue(forked['ok'], forked)
        self.assertEqual('general', forked['session']['workspace_mode'])
        self.assertEqual('explore', forked['session']['agent'])
        self.assertEqual('plan', forked['session']['mode'])

    def test_plan_prompt_uses_saved_purpose_separately_from_role(self):
        runtime = AgentRuntime(session_root=self.root / 'sessions', offline=False, llm_enabled=True)
        with mock.patch.object(llm, 'available', return_value=True), \
                mock.patch.object(llm, 'chat_json', return_value={'steps': [{'tool': 'workspace.read'}]}) as model:
            plan = runtime.plan('检查代码', 'plan', 'explore', workspace_mode='general')
        self.assertEqual('general', plan['workspace_mode'])
        self.assertEqual('explore', plan['agent'])
        self.assertTrue(model.call_args.args[0][0]['content'].startswith(prompts.GENERAL_SYSTEM_PROMPT))

    def test_draft_background_turn_honors_online_setting(self):
        settings.save({'workspace_mode': 'general', 'default_offline': False})
        sid = session_workspace.handle({'cmd': 'chat_new'})['session_id']
        offline = mock.Mock(offline=True)
        offline.sessions.snapshot.return_value = SessionStore(self.root / 'sessions').snapshot(sid)
        online = mock.Mock(offline=False)
        online.sessions.snapshot.return_value = offline.sessions.snapshot.return_value
        with mock.patch('src.bridge._session_runtime', return_value=offline), \
                mock.patch('src.bridge._runtime', return_value=online) as select, \
                mock.patch('src.agent_launch.launcher.launch', return_value={'session_id': sid}) as launch:
            result = handle({'cmd': 'agent_launch', 'session_id': sid, 'request': '检查项目'})
        self.assertTrue(result['ok'], result)
        select.assert_called_once_with(offline=False)
        self.assertIs(online, launch.call_args.args[0])

    def test_native_general_turn_keeps_prompt_and_tools_after_global_switch(self):
        runtime = AgentRuntime(session_root=self.root / 'sessions', offline=False, llm_enabled=True)
        with mock.patch.object(llm, 'available', return_value=True), \
                mock.patch.object(llm, 'chat', return_value=response('已检查')) as model:
            first = runtime.start('检查代码', agent='general')
            original_system = model.call_args.args[0][0]['content']
            self.assertTrue(original_system.startswith(prompts.GENERAL_SYSTEM_PROMPT))
            offered = {tool['function']['name'] for tool in model.call_args.kwargs['tools']}
            self.assertIn('file.edit', offered)
            self.assertNotIn('research.create', offered)
            settings.save({'workspace_mode': 'research'})
            resumed = AgentRuntime(session_root=self.root / 'sessions', offline=False, llm_enabled=True)
            result = resumed.send(first['session_id'], '继续检查', agent='general')
            self.assertEqual('general', result['agent'])
            self.assertEqual(original_system, model.call_args.args[0][0]['content'])


if __name__ == '__main__':
    unittest.main()
