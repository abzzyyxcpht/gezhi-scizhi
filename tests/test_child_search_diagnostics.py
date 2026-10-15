"""LLM children report only retrieval diagnostics from their own current turn."""
from __future__ import annotations

import copy
import unittest
from unittest import mock

from src import llm
from src.agents.specs import get_spec
from tests.test_adaptive_hardening import response
from tests import test_runtime_recovery_boundaries as recovery


STALE = {'source_counts': {'openalex': 99}, 'source_status': {'openalex': 'success'},
         'degraded': False, 'warnings': ['old parent warning']}
CURRENT = {'source_counts': {'openalex': 0, 'crossref': 1},
           'source_status': {'openalex': 'failed', 'crossref': 'success'},
           'degraded': True, 'warnings': ['OpenAlex unavailable this turn']}
FAILED = {'source_counts': {'openalex': 0, 'crossref': 0},
          'source_status': {'openalex': 'failed', 'crossref': 'failed'},
          'degraded': True, 'warnings': ['Both sources unavailable this turn']}


class ChildSearchDiagnosticsTest(unittest.TestCase):
    setUp = recovery.RuntimeRecoveryBoundaryTest.setUp
    online_runtime = recovery.RuntimeRecoveryBoundaryTest.online_runtime
    register_material_tool = staticmethod(recovery.RuntimeRecoveryBoundaryTest.register_material_tool)

    def parent(self, runtime):
        sid = 'parent'
        runtime._session_agent[sid] = 'research'
        runtime.sessions.append(sid, 'user_message', {'text': 'parent topic'})
        runtime._workspaces[sid] = {'search_metadata': copy.deepcopy(STALE),
                                    'pool': [{'id': 'original', 'title': 'original'}]}
        runtime._save_workspace(sid)
        return sid

    @staticmethod
    def search_tool(runtime, *, failed=False, archived=False):
        metadata = copy.deepcopy(FAILED if failed else CURRENT)
        def search(args):
            if not failed:
                runtime._workspaces[args['__session_id']].update(
                    pool=[{'id': 'fresh', 'title': 'fresh'}], search_metadata=metadata)
            # Failed retrieval deliberately leaves the inherited workspace intact.
            return {'ok': not failed, **({'details': 'x' * 20000} if archived else {}),
                    'search_metadata': metadata, **({'error': 'search failed'} if failed else {})}
        runtime._register('literature.run', 'network', 'mock retrieval', search,
                          runtime._obj_schema({'request': {'type': 'string'}}, ['request']))
        return metadata

    @staticmethod
    def child_model(messages, **kwargs):
        if messages[-1]['role'] == 'user' and len(messages) == 2:
            return response(calls=[('search', 'literature.run', {'request': 'topic'})])
        return response('child finished')

    def test_completed_and_failed_driver_fanout_carry_actual_diagnostics(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                runtime = self.online_runtime()
                sid = self.parent(runtime)
                runtime.policy.decisions['literature.run'] = 'allow'
                metadata = self.search_tool(runtime, failed=failed)
                with mock.patch.object(llm, 'chat', side_effect=self.child_model):
                    result = runtime._subagent_fanout({'__session_id': sid,
                        'agents': ['literature'], 'requests': ['topic']})
                branch = result['branches'][0]
                self.assertEqual(branch.get('search_metadata'), metadata)
                self.assertEqual(branch['source_counts'], metadata['source_counts'])
                self.assertEqual(branch['warnings'], metadata['warnings'])
                self.assertTrue(result['degraded'])
                self.assertEqual(branch['ok'], not failed)

    def test_spawn_diagnostics_survive_archived_tool_receipt(self):
        runtime = self.online_runtime()
        sid = self.parent(runtime)
        runtime.policy.decisions['literature.run'] = 'allow'
        metadata = self.search_tool(runtime, archived=True)
        with mock.patch.object(llm, 'chat', side_effect=self.child_model):
            result = runtime._subagent_spawn({'__session_id': sid, 'agent': 'literature', 'request': 'topic'})
        self.assertEqual(result.get('search_metadata'), metadata)
        self.assertEqual(result.get('source_counts'), metadata['source_counts'])
        child = result['child_session']
        receipt = next(e['payload']['result'] for e in runtime.sessions.read(child) if e['type'] == 'tool_result')
        self.assertIn('output_ref', receipt)

    def test_approval_resume_uses_new_success_or_failure_diagnostics(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                runtime = self.online_runtime()
                self.search_tool(runtime, failed=failed)
                def parent_model(messages, **kwargs):
                    if '文献猎手' in messages[0]['content']:
                        return self.child_model(messages, **kwargs)
                    return response(calls=[('fanout', 'subagent.fanout', {
                        'agents': ['literature'], 'requests': ['topic']})])
                def seed_parent(sid):
                    runtime._workspaces[sid].update(search_metadata=copy.deepcopy(STALE),
                        pool=[{'id': 'original', 'title': 'original'}])
                with mock.patch.object(llm, 'chat', side_effect=parent_model):
                    waiting = runtime.start('parallel retrieval', on_started=seed_parent)
                sid = waiting['session_id']
                self.assertEqual(waiting['status'], 'waiting_children')
                child = runtime._children[sid][0]
                restored = self.online_runtime()
                metadata = self.search_tool(restored, failed=failed)
                with mock.patch.object(llm, 'chat', return_value=response('finished')):
                    restored.approve(child, 'search', 'allow')
                resumed = self.online_runtime()
                with mock.patch.object(llm, 'chat', return_value=response('report result')):
                    self.assertEqual(resumed.continue_session(sid)['status'], 'done')
                branch = resumed._workspaces[sid]['fanout'][0]
                self.assertEqual(branch.get('search_metadata'), metadata)
                self.assertEqual(branch['source_counts'], metadata['source_counts'])
                self.assertEqual(branch['warnings'], metadata['warnings'])
                if failed:
                    self.assertEqual(resumed._workspaces[sid]['pool'][0]['id'], 'original')

    def test_no_retrieval_does_not_reuse_inherited_parent_diagnostics(self):
        runtime = self.online_runtime()
        sid = self.parent(runtime)
        with mock.patch.object(llm, 'chat', return_value=response('only answered')):
            result = runtime._subagent_fanout({'__session_id': sid,
                'agents': ['literature'], 'requests': ['no retrieval']})
        self.assertNotIn('search_metadata', result['branches'][0])
        self.assertEqual(result['branches'][0]['source_counts'], {})
        self.assertEqual(result['warnings'], [])

    def test_top_level_local_fallback_diagnostics_survive_empty_source_metadata(self):
        runtime = self.online_runtime()
        sid = self.parent(runtime)
        child = runtime._begin_child(sid, get_spec('literature'), 'local fallback')
        runtime.sessions.append(child, 'tool_result', {'id': 'search', 'name': 'literature.run',
            'result': {'ok': True, 'partial': True, 'search_metadata': {}, 'degraded': True,
                       'search_status': 'failed', 'warnings': ['检索评分失败；已接收本地材料']}})
        result = runtime._child_result_from_driver(get_spec('literature'),
                                                   {'session_id': child, 'status': 'done'})
        self.assertTrue(result['ok'])
        self.assertTrue(result['degraded'])
        self.assertEqual(result['warnings'], ['检索评分失败；已接收本地材料'])
        self.assertEqual(result['search_metadata']['search_status'], 'failed')
        self.assertEqual(result['source_counts'], {})

    def test_failed_spawn_after_approval_reports_diagnostics_without_replacing_parent_evidence(self):
        runtime = self.online_runtime()
        self.search_tool(runtime, failed=True)
        def seed_parent(sid):
            runtime._workspaces[sid].update(search_metadata=copy.deepcopy(STALE),
                pool=[{'id': 'original', 'title': 'original'}])
        with mock.patch.object(llm, 'chat', side_effect=[
                response(calls=[('spawn', 'subagent.spawn', {'agent': 'literature', 'request': 'topic'})]),
                response(calls=[('search', 'literature.run', {'request': 'topic'})])]):
            waiting = runtime.start('delegate retrieval', on_started=seed_parent)
        sid = waiting['session_id']
        child = runtime._children[sid][0]
        restored = self.online_runtime()
        self.search_tool(restored, failed=True)
        with mock.patch.object(llm, 'chat', return_value=response('failed search')):
            self.assertEqual(restored.approve(child, 'search', 'allow')['status'], 'error')
        resumed = self.online_runtime()
        with mock.patch.object(llm, 'chat', return_value=response('report failed sources')) as chat:
            self.assertEqual(resumed.continue_session(sid)['status'], 'done')
        self.assertIn(FAILED['warnings'][0], str(chat.call_args.args[0]))
        self.assertEqual(resumed._workspaces[sid]['search_metadata'], STALE)
        self.assertEqual(resumed._workspaces[sid]['pool'][0]['id'], 'original')

    def test_child_diagnostics_stop_at_current_turn_and_ignore_other_tools(self):
        runtime = self.online_runtime()
        sid = self.parent(runtime)
        child = runtime._begin_child(sid, get_spec('literature'), 'old query')
        runtime.sessions.append(child, 'tool_result', {'id': 'old', 'name': 'literature.run',
            'result': {'ok': True, 'search_metadata': CURRENT}})
        sibling = runtime._begin_child(sid, get_spec('literature'), 'sibling query')
        runtime.sessions.append(sibling, 'tool_result', {'id': 'sibling', 'name': 'literature.run',
            'result': {'ok': True, 'search_metadata': CURRENT}})
        runtime.sessions.append(child, 'user_message', {'text': 'new turn without retrieval'})
        runtime.sessions.append(child, 'tool_result', {'id': 'unrelated', 'name': 'workspace.read',
            'result': {'ok': True, 'search_metadata': CURRENT}})
        result = runtime._child_result_from_driver(get_spec('literature'),
            {'session_id': child, 'status': 'done', 'content': 'answer'})
        self.assertNotIn('search_metadata', result)
        runtime.sessions.append(child, 'tool_result', {'id': 'current', 'name': 'literature.run',
            'result': {'ok': False, 'search_metadata': FAILED}})
        result = runtime._child_result_from_driver(get_spec('literature'),
            {'session_id': child, 'status': 'error'})
        self.assertEqual(result.get('search_metadata'), FAILED)


if __name__ == '__main__':
    unittest.main()
