"""Recovery boundaries use isolated state and deterministic model responses."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from src import config, llm, model_registry, settings
from src.agent_driver import AdaptiveDriver
from src.agent_runtime import AgentRuntime
from src.agents.specs import get_spec
from tests.test_adaptive_hardening import response


class RuntimeRecoveryBoundaryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for patch in (mock.patch.object(config, 'DATA_DIR', self.root),
                      mock.patch.object(settings, '_PATH', self.root / 'settings.json'),
                      mock.patch.object(AgentRuntime, '_run_hooks', return_value={}),
                      mock.patch.object(llm, 'available', return_value=True),
                      mock.patch.object(model_registry, 'resolve', return_value={
                          'connection_id': 'fake', 'profile_id': 'fake', 'model': 'fake',
                          'protocol': 'responses', 'base_url': 'https://not-used.invalid',
                          'context_window': 32768, 'max_output_tokens': 8192})):
            patch.start()
            self.addCleanup(patch.stop)

    def test_durable_cancel_prevents_old_approval_after_restart(self):
        for decision in ('allow', 'allow_session'):
            with self.subTest(decision=decision):
                root = self.root / decision
                first = AgentRuntime(session_root=root)
                effect = mock.Mock(return_value={'ok': True})
                first._register('test.write', 'write', 'write', effect)
                plan = {'steps': [{'tool': 'test.write', 'arguments': {}}]}
                with mock.patch.object(first, 'plan', return_value=plan):
                    waiting = first.start('write once')
                sid = waiting['session_id']
                # Crash after cancellation became durable, before finalization.
                (root / f'{sid}.cancel').touch()
                first.sessions.append(sid, 'cancel_requested', {'message': 'stop'})
                restored = AgentRuntime(session_root=root)
                restored._register('test.write', 'write', 'write', effect)

                outcome = restored.approve(sid, waiting['pending']['id'], decision)

                self.assertEqual(outcome['status'], 'cancelled')
                effect.assert_not_called()
                events = restored.sessions.read(sid)
                self.assertFalse(any(e['type'] == 'permission_granted' for e in events))
                self.assertNotIn('test.write', restored.policy._session_grants.get(sid, set()))
                again = restored.approve(sid, waiting['pending']['id'], decision)
                self.assertEqual(again['status'], 'cancelled')
                self.assertEqual(sum(e['type'] == 'completed' for e in restored.sessions.read(sid)), 1)

    def online_runtime(self):
        runtime = AgentRuntime(session_root=self.root / 'sessions', offline=False, llm_enabled=True,
                               permissions={'subagent.spawn': 'allow', 'subagent.fanout': 'allow'})
        self.register_material_tool(runtime)
        return runtime

    @staticmethod
    def register_material_tool(runtime, *, barrier=None):
        def material(args):
            if barrier is not None:
                barrier.wait(timeout=5)
            request = args['request']
            runtime._workspaces[args['__session_id']].update(
                pool=[{'id': request, 'title': request}], digest_md=request + ' digest')
            return {'ok': request != 'failed', **({'error': 'failed'} if request == 'failed' else {})}
        runtime._register('literature.run', 'network', 'mock material', material,
                          runtime._obj_schema({'request': {'type': 'string'}}, ['request']))

    def waiting_spawn(self, runtime):
        with mock.patch.object(llm, 'chat', side_effect=[
                response(calls=[('spawn', 'subagent.spawn', {'agent': 'literature', 'request': 'sample'})]),
                response(calls=[('search', 'literature.run', {'request': 'sample'})])]):
            waiting = runtime.start('delegate material')
        self.assertEqual(waiting['status'], 'waiting_children')
        sid = waiting['session_id']
        runtime._workspaces[sid]['parent_note'] = 'retain me'
        runtime._save_workspace(sid)
        return sid, runtime._children[sid][0]

    def test_spawn_merges_after_parent_resume_and_preserves_restart_workspace(self):
        first = self.online_runtime()
        sid, child = self.waiting_spawn(first)
        restored = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('child finished')):
            self.assertEqual(restored.approve(child, 'search', 'allow')['status'], 'done')
        saved = json.loads((first.sessions.root / f'{sid}.workspace.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['workspace'].get('parent_note'), 'retain me')
        self.assertNotIn('pool', saved['workspace'])  # Only the parent consumes the result.

        resumed = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('parent finished')):
            self.assertEqual(resumed.continue_session(sid)['status'], 'done')
        self.assertEqual(resumed._workspaces[sid]['pool'][0]['id'], 'sample')
        self.assertEqual(resumed._workspaces[sid]['parent_note'], 'retain me')
        with self.assertRaises(ValueError):
            resumed.continue_session(sid)
        check = self.online_runtime()
        check._restore_session_meta(sid)
        self.assertEqual(check._workspaces[sid]['digest_md'], 'sample digest')

    def waiting_fanout(self, runtime, requests):
        original = runtime.tools['workspace.read']
        def ready_material(args):
            sid = args['__session_id']
            runtime._workspaces[sid]['pool'] = [{'id': 'ready', 'title': 'ready'}]
            return original.handler(args)
        runtime._register('workspace.read', 'read', 'mock ready material', ready_material, original.parameters)
        def chat(messages, **kwargs):
            request = messages[1]['content']
            if '文献猎手' in messages[0]['content']:
                if request == 'ready':
                    return (response('ready finished') if messages[-1]['role'] == 'tool' else
                            response(calls=[('read-ready', 'workspace.read', {})]))
                return response(calls=[('search-' + request, 'literature.run', {'request': request})])
            return response(calls=[('fanout', 'subagent.fanout', {
                'agents': ['literature'] * len(requests), 'requests': requests})])
        with mock.patch.object(llm, 'chat', side_effect=chat):
            waiting = runtime.start('parallel material')
        self.assertEqual(waiting['status'], 'waiting_children')
        return waiting['session_id'], list(runtime._children[waiting['session_id']])

    def test_fanout_parallel_approvals_merge_in_input_order_after_restart(self):
        runtime = self.online_runtime()
        sid, children = self.waiting_fanout(runtime, ['first', 'ready', 'second'])
        barrier = threading.Barrier(2)
        def approve(child, name):
            restored = self.online_runtime()
            self.register_material_tool(restored, barrier=barrier)
            return restored.approve(child, 'search-' + name, 'allow')
        with mock.patch.object(llm, 'chat', return_value=response('child finished')):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(approve, children[2], 'second'),
                           pool.submit(approve, children[0], 'first')]
                self.assertEqual([future.result(timeout=10)['status'] for future in results], ['done', 'done'])
        resumed = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('parent finished')):
            self.assertEqual(resumed.continue_session(sid)['status'], 'done')
        self.assertEqual([work['id'] for work in resumed._workspaces[sid]['pool']], ['first', 'ready', 'second'])
        self.assertTrue(all(row['ok'] for row in resumed._workspaces[sid]['fanout']))
        self.assertNotIn('digest_md', resumed._workspaces[sid])  # Fanout merges pools, not unrelated branch fields.

    def test_fanout_waits_for_all_children_and_excludes_failed_or_cancelled_artifacts(self):
        runtime = self.online_runtime()
        sid, children = self.waiting_fanout(runtime, ['ready', 'failed', 'cancelled'])
        with mock.patch.object(llm, 'chat', return_value=response('must not run')) as chat:
            self.assertEqual(runtime.continue_session(sid)['status'], 'waiting_children')
        chat.assert_not_called()
        self.assertFalse(runtime._child_handoffs[sid]['consumed'])
        restored = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('failure remains')):
            self.assertEqual(restored.approve(children[1], 'search-failed', 'allow')['status'], 'error')
        self.assertIn('pool', restored._workspaces[children[1]])  # Failed effects exist but are not adopted.
        self.assertEqual(restored.cancel_session(children[2])['status'], 'cancelled')
        parent = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('report partial result')):
            self.assertEqual(parent.continue_session(sid)['status'], 'done')
        self.assertEqual([work['id'] for work in parent._workspaces[sid]['pool']], ['ready'])
        self.assertEqual([row['ok'] for row in parent._workspaces[sid]['fanout']], [True, False, False])

    def test_cancelled_parent_does_not_consume_finished_child_or_call_model(self):
        runtime = self.online_runtime()
        sid, child = self.waiting_spawn(runtime)
        with mock.patch.object(llm, 'chat', return_value=response('child finished')):
            runtime.approve(child, 'search', 'allow')
        self.assertEqual(runtime.cancel_session(sid)['status'], 'cancelled')
        restored = self.online_runtime()
        with mock.patch.object(llm, 'chat') as chat:
            restored._restore_session_meta(child)
            restored._sync_child_outcome(child, {'status': 'done', 'content': 'late child receipt'})
            with self.assertRaises(ValueError):
                restored.continue_session(sid)
        chat.assert_not_called()
        restored._restore_session_meta(sid)
        self.assertNotIn('pool', restored._workspaces[sid])
        self.assertEqual(restored._workspaces[sid]['parent_note'], 'retain me')

    def test_all_unsuccessful_children_preserve_existing_parent_evidence(self):
        runtime = self.online_runtime()
        sid, children = self.waiting_fanout(runtime, ['failed', 'cancelled'])
        original = [{'id': 'reviewed', 'title': 'Previously reviewed material'}]
        runtime._workspaces[sid]['pool'] = original
        runtime._save_workspace(sid)
        restored = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('failure remains')):
            self.assertEqual(restored.approve(children[0], 'search-failed', 'allow')['status'], 'error')
        restored.cancel_session(children[1])
        parent = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('report failed branches')):
            parent.continue_session(sid)
        self.assertEqual(parent._workspaces[sid]['pool'], original)
        self.assertEqual([row['ok'] for row in parent._workspaces[sid]['fanout']], [False, False])

    def test_crash_after_consumption_does_not_merge_child_again(self):
        runtime = self.online_runtime()
        sid, child = self.waiting_spawn(runtime)
        with mock.patch.object(llm, 'chat', return_value=response('child finished')):
            runtime.approve(child, 'search', 'allow')
        with mock.patch.object(AdaptiveDriver, '_run', side_effect=SystemExit('crash after merge')):
            with self.assertRaises(SystemExit):
                runtime.continue_session(sid)
        self.assertEqual(runtime._workspaces[sid]['digest_md'], 'sample digest')
        self.assertTrue(runtime._child_handoffs[sid]['consumed'])
        runtime._workspaces[sid]['digest_md'] = 'reviewed parent digest'
        runtime._save_workspace(sid)
        restored = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('parent finished')):
            self.assertEqual(restored.continue_session(sid)['status'], 'done')
        self.assertEqual(restored._workspaces[sid]['digest_md'], 'reviewed parent digest')

    def test_crash_before_driver_observation_restores_child_wait_before_running_model(self):
        runtime = self.online_runtime()
        observe = AdaptiveDriver._observe
        def crash_after_receipt(driver, state, call, result):
            if call['name'] == 'subagent.spawn':
                raise SystemExit('receipt saved before driver observation')
            return observe(driver, state, call, result)
        with mock.patch.object(AdaptiveDriver, '_observe', new=crash_after_receipt), \
             mock.patch.object(llm, 'chat', side_effect=[
                 response(calls=[('spawn', 'subagent.spawn', {'agent': 'literature', 'request': 'sample'})]),
                 response(calls=[('search', 'literature.run', {'request': 'sample'})])]):
            with self.assertRaises(SystemExit):
                runtime.start('delegate material')
        sid = next(iter(runtime._children))
        child = runtime._children[sid][0]
        restored = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('must wait')) as chat:
            self.assertEqual(restored.continue_session(sid)['status'], 'waiting_children')
        chat.assert_not_called()
        with mock.patch.object(llm, 'chat', return_value=response('finished')):
            restored.approve(child, 'search', 'allow')
            self.assertEqual(restored.continue_session(sid)['status'], 'done')
        self.assertEqual(restored._workspaces[sid]['pool'][0]['id'], 'sample')
        self.assertEqual(sum(event['type'] == 'tool_call' for event in restored.sessions.read(sid)), 1)

    def test_legacy_fanout_recovers_current_group_without_unrelated_child(self):
        for abbreviated in (False, True):
            with self.subTest(abbreviated=abbreviated):
                runtime = self.online_runtime()
                sid, children = self.waiting_fanout(runtime, ['first', 'ready', 'second'])
                unrelated = runtime._begin_child(sid, get_spec('literature'), 'unrelated')
                runtime._workspaces[unrelated]['pool'] = [{'id': 'unrelated', 'title': 'unrelated'}]
                runtime._finish_child(sid, unrelated, get_spec('literature'), {'ok': True}, merge_workspace=False)
                workspace_path = runtime.sessions.root / f'{sid}.workspace.json'
                saved = json.loads(workspace_path.read_text(encoding='utf-8'))
                saved.pop('child_handoff', None)
                workspace_path.write_text(json.dumps(saved), encoding='utf-8')
                if abbreviated:
                    events = runtime.sessions.read(sid)
                    for event in events:
                        if event['type'] == 'tool_result' and event['payload']['name'] == 'subagent.fanout':
                            event['payload']['result'].pop('branches', None)
                            event['payload']['result']['truncated'] = True
                    runtime.sessions._path(sid).write_text(
                        ''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
                restored = self.online_runtime()
                with mock.patch.object(llm, 'chat', return_value=response('finished')):
                    restored.approve(children[2], 'search-second', 'allow')
                    restored.approve(children[0], 'search-first', 'allow')
                    self.assertEqual(restored.continue_session(sid)['status'], 'done')
                self.assertEqual([work['id'] for work in restored._workspaces[sid]['pool']],
                                 ['first', 'ready', 'second'])

    def test_deterministic_parent_restores_and_consumes_child_after_restart(self):
        runtime = AgentRuntime(session_root=self.root / 'sessions', offline=False, llm_enabled=False,
                               permissions={'subagent.spawn': 'allow'})
        self.register_material_tool(runtime)
        plan = {'steps': [{'tool': 'subagent.spawn', 'arguments': {'agent': 'literature', 'request': 'sample'}},
                          {'tool': 'workspace.read', 'arguments': {}}]}
        with mock.patch.object(runtime, 'plan', return_value=plan), \
             mock.patch.object(runtime, '_subagent_driver_available', return_value=True), \
             mock.patch.object(llm, 'chat', return_value=response(calls=[
                 ('search', 'literature.run', {'request': 'sample'})])):
            waiting = runtime.start('delegate material')
        sid = waiting['session_id']
        self.assertEqual(waiting['status'], 'waiting_children')
        child = runtime._children[sid][0]
        restored = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('child finished')):
            restored.approve(child, 'search', 'allow')
        parent = AgentRuntime(session_root=runtime.sessions.root)
        with mock.patch.object(llm, 'chat') as chat:
            self.assertEqual(parent.continue_session(sid)['status'], 'done')
        chat.assert_not_called()
        self.assertEqual(parent._workspaces[sid]['pool'][0]['id'], 'sample')


if __name__ == '__main__':
    unittest.main()
