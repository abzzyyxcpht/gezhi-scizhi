"""session_rewind 回滚语义：截断用户轮次、清理执行状态、保持投影一致。"""
import json
import threading
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, llm, session_workspace, settings
from src.agent_runtime import AgentRuntime, SessionStore
from tests.test_adaptive_driver import response


class SessionRewindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for obj, key, value in [(config, 'DATA_DIR', self.root), (settings, '_PATH', self.root / 'settings.json')]:
            patcher = mock.patch.object(obj, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _seed(self, sid='rewind01'):
        """构造两轮已完成的事件日志（含 assistant_delta 与 subagent_*）。"""
        store = SessionStore(self.root / 'sessions')
        store.append(sid, 'user_message', {'text': '第一轮问题', 'mode': 'agent'})
        store.append(sid, 'assistant_delta', {'step': 1, 'delta': '第一'})
        store.append(sid, 'assistant_message', {'content': '第一轮回答'})
        store.append(sid, 'completed', {'status': 'done'})
        store.append(sid, 'user_message', {'text': '第二轮问题', 'mode': 'agent'})
        store.append(sid, 'subagent_started', {'child': 'c1', 'agent': 'literature', 'status': 'running', 'steps': 0, 'tokens': 0})
        store.append(sid, 'assistant_delta', {'step': 2, 'delta': '第二'})
        store.append(sid, 'subagent_completed', {'child': 'c1', 'status': 'done', 'steps': 3, 'tokens': 12})
        store.append(sid, 'completed', {'status': 'done'})
        return store

    def test_rewind_truncates_last_turn_and_cleans_driver_state(self):
        self._seed()
        sessions = self.root / 'sessions'
        (sessions / 'rewind01.driver.json').write_text('{"schema_version": 1}', encoding='utf-8')
        (sessions / 'rewind01.cancel').touch()
        result = session_workspace.rewind_session('rewind01', 1)
        self.assertEqual({'rewound_to': 4, 'removed': 5}, result)
        self.assertFalse((sessions / 'rewind01.driver.json').exists())
        self.assertFalse((sessions / 'rewind01.cancel').exists())
        events = SessionStore(sessions).read('rewind01')
        self.assertEqual(5, len(events))  # 保留 4 条 + 回滚审计事件
        self.assertEqual('completed', events[3]['type'])
        self.assertNotIn('第二轮问题', json.dumps(events, ensure_ascii=False))
        marker = events[-1]
        self.assertEqual('completed', marker['type'])
        self.assertEqual('rewound', marker['payload']['reason'])
        snap = SessionStore(sessions).snapshot('rewind01')
        self.assertEqual('done', snap['status'])
        self.assertEqual('第一轮问题', snap['request'])

    def test_rewind_multiple_turns_keeps_through_nth_from_last_user_message(self):
        store = self._seed()
        store.append('rewind01', 'user_message', {'text': '第三轮问题', 'mode': 'agent'})
        store.append('rewind01', 'assistant_message', {'content': '第三轮回答'})
        store.append('rewind01', 'completed', {'status': 'done'})
        result = session_workspace.rewind_session('rewind01', turns=2)
        # 整轮移除最后 2 轮：保留「第一轮问题」整轮（索引 0–3）
        self.assertEqual({'rewound_to': 4, 'removed': 8}, result)
        texts = [e['payload'].get('text') for e in SessionStore(self.root / 'sessions').read('rewind01')
                 if e['type'] == 'user_message']
        self.assertEqual(['第一轮问题'], texts)

    def test_rewind_rejects_turns_beyond_user_turns(self):
        self._seed()
        with self.assertRaises(ValueError):
            session_workspace.rewind_session('rewind01', turns=2)
        with self.assertRaises(ValueError):
            session_workspace.rewind_session('rewind01', turns=0)
        with self.assertRaises(ValueError):
            session_workspace.rewind_session('rewind01', turns=True)

    def test_rewind_rejects_missing_and_empty_sessions(self):
        with self.assertRaisesRegex(ValueError, '会话不存在'):
            session_workspace.rewind_session('no-such-session', 1)
        sid = session_workspace.handle({'cmd': 'chat_new', 'title': '空白'})['session_id']
        with self.assertRaisesRegex(ValueError, '用户轮次'):
            session_workspace.rewind_session(sid, 1)

    def test_rewind_rejects_running_session(self):
        store = SessionStore(self.root / 'sessions')
        store.append('running01', 'user_message', {'text': '还在运行', 'mode': 'agent'})
        with self.assertRaisesRegex(ValueError, '/cancel'):
            session_workspace.rewind_session('running01', 1)

    def test_rewind_rejects_waiting_approval_session(self):
        store = SessionStore(self.root / 'sessions')
        store.append('pending01', 'user_message', {'text': '问题', 'mode': 'agent'})
        store.append('pending01', 'permission_requested', {'tool': 'danger', 'tool_call_id': 'c9'})
        with self.assertRaisesRegex(ValueError, '/cancel'):
            session_workspace.rewind_session('pending01', 1)

    def test_rewind_rejects_parent_waiting_for_children_without_changing_journal(self):
        store = self._seed()
        store.append('rewind01', 'user_message', {'text': '等待子任务', 'mode': 'agent'})
        store.append('rewind01', 'driver_paused', {'status': 'waiting_children'})
        before = store._path('rewind01').read_bytes()
        with self.assertRaisesRegex(ValueError, '/cancel'):
            session_workspace.rewind_session('rewind01', 1)
        self.assertEqual(before, store._path('rewind01').read_bytes())

    def test_rewind_restores_only_redacted_visible_history_to_the_model(self):
        store = SessionStore(self.root / 'sessions')
        sid = 'remember01'
        store.append(sid, 'user_message', {'text': 'Remember REDWOOD; api_key=hidden-value',
                                          'mode': 'agent', 'agent': 'research', 'offline': False})
        store.append(sid, 'assistant_message', {'content': 'I remember REDWOOD',
            'tool_calls': [{'id': 'old-call', 'name': 'test.write', 'arguments': {}}],
            'reasoning_content': 'private-reasoning'})
        store.append(sid, 'tool_result', {'id': 'old-call', 'result': {'private': 'old-tool-payload'}})
        store.append(sid, 'completed', {'status': 'done'})
        store.append(sid, 'user_message', {'text': 'Discard BLUEBIRD', 'mode': 'agent'})
        store.append(sid, 'assistant_message', {'content': 'discard-this-answer'})
        store.append(sid, 'completed', {'status': 'done'})
        session_workspace.rewind_session(sid, 1)
        runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=self.root / 'sessions')
        writer = mock.Mock(return_value={'ok': True})
        runtime._register('test.write', 'write', 'Write fixture', writer)
        sent = []

        def answer(messages, **kwargs):
            sent.append(json.loads(json.dumps(messages)))
            return response('REDWOOD remains')

        with mock.patch('src.bridge._runtime', return_value=runtime), \
                mock.patch.object(llm, 'available', return_value=True), \
                mock.patch.object(llm, 'chat', side_effect=answer), \
                mock.patch('src.agent_driver.prompts.agent_system', return_value='CURRENT SYSTEM'):
            result = session_workspace.handle({'cmd': 'chat_turn', 'session_id': sid,
                                               'request': 'What should you remember?', 'online': True})
            self.assertTrue(result['ok'], result)
            followup = session_workspace.handle({'cmd': 'chat_turn', 'session_id': sid,
                                                'request': 'Continue from that answer.', 'online': True})
            self.assertTrue(followup['ok'], followup)
        self.assertEqual(['system', 'user', 'assistant', 'user'], [m['role'] for m in sent[0]])
        self.assertTrue(sent[0][0]['content'].startswith('CURRENT SYSTEM'))
        self.assertEqual('Remember REDWOOD; api_key=[REDACTED]', sent[0][1]['content'])
        self.assertEqual({'role': 'assistant', 'content': 'I remember REDWOOD'}, sent[0][2])
        self.assertEqual('What should you remember?', sent[0][-1]['content'])
        encoded = json.dumps(sent)
        for removed in ('hidden-value', 'BLUEBIRD', 'discard-this-answer', 'private-reasoning',
                        'old-tool-payload', 'old-call'):
            self.assertNotIn(removed, encoded)
        self.assertEqual(1, sum(m.get('content') == sent[0][1]['content'] for m in sent[1]))
        self.assertEqual('Continue from that answer.', sent[1][-1]['content'])
        writer.assert_not_called()

    def test_rewind_storage_failure_preserves_original_journal_and_driver(self):
        store = self._seed()
        path = store._path('rewind01')
        before = path.read_bytes()
        driver = self.root / 'sessions' / 'rewind01.driver.json'
        driver.write_text('{"schema_version": 1}', encoding='utf-8')
        with mock.patch('os.replace', side_effect=OSError('storage unavailable')):
            with self.assertRaisesRegex(OSError, 'storage unavailable'):
                session_workspace.rewind_session('rewind01', 1)
        self.assertEqual(before, path.read_bytes())
        self.assertTrue(driver.is_file())
        self.assertEqual([], list(path.parent.glob('.*.rewind.tmp')))

    def test_rewind_serializes_with_runtime_execution_and_rechecks_status(self):
        from src.session_lock import session_lock

        store = self._seed()
        entered, finished = threading.Event(), threading.Event()
        outcome = []

        def rewind():
            entered.set()
            outcome.append(session_workspace.handle({'cmd': 'session_rewind', 'session_id': 'rewind01'}))
            finished.set()

        worker = threading.Thread(target=rewind)
        try:
            with session_lock(store._path('rewind01')):
                worker.start()
                self.assertTrue(entered.wait(2))
                self.assertFalse(finished.wait(.1), 'rewind must wait for the running session operation')
                store.append('rewind01', 'user_message', {'text': 'concurrent runtime turn', 'mode': 'agent'})
        finally:
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(outcome[0]['ok'])
        self.assertEqual('running', store.snapshot('rewind01')['status'])

    def test_rewind_marker_prevents_reusing_driver_left_by_failed_cleanup(self):
        store = self._seed()
        driver = self.root / 'sessions' / 'rewind01.driver.json'
        driver.write_text('{"schema_version": 1}', encoding='utf-8')
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == driver:
                raise OSError('driver temporarily locked')
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, 'unlink', new=unlink):
            rewound = session_workspace.handle({'cmd': 'session_rewind', 'session_id': 'rewind01'})
        self.assertTrue(rewound['ok'], rewound)
        self.assertIn('已回滚', rewound['warning'])
        self.assertEqual(['rewind01.driver.json'], rewound['cleanup_pending'])
        self.assertEqual('rewound', store.read('rewind01')[-1]['payload']['reason'])
        self.assertTrue(driver.is_file())
        runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=self.root / 'sessions')
        with mock.patch('src.bridge._runtime', return_value=runtime), \
                mock.patch.object(llm, 'available', return_value=True), \
                mock.patch.object(llm, 'chat', return_value=response('恢复完成')) as model:
            result = session_workspace.handle({'cmd': 'chat_turn', 'session_id': 'rewind01',
                                               'request': '继续保留的第一轮', 'online': True})
        self.assertTrue(result['ok'], result)
        history = json.dumps(model.call_args.args[0], ensure_ascii=False)
        self.assertIn('第一轮问题', history)
        self.assertNotIn('第二轮问题', history)

    def test_rewind_revokes_removed_session_grants_but_retains_earlier_grants(self):
        runtime = AgentRuntime(offline=True, session_root=self.root / 'sessions')
        effects = []
        for name in ('test.keep', 'test.drop'):
            runtime._register(name, 'write', name, lambda args, n=name: effects.append(n) or {'ok': True})

        def plan(request, mode='agent', agent='research'):
            names = ['test.keep', 'test.drop'] if request == 'verify' else [f'test.{request}']
            return runtime._plan_payload(mode, request,
                                         [{'tool': name, 'arguments': {}} for name in names], agent)

        with mock.patch.object(runtime, 'plan', side_effect=plan):
            first = runtime.start('keep')
            sid = first['session_id']
            runtime.approve(sid, first['pending']['id'], 'allow_session')
            second = runtime.send(sid, 'drop')
            runtime.approve(sid, second['pending']['id'], 'allow_session')
            session_workspace.rewind_session(sid)
            result = runtime.send(sid, 'verify')
        self.assertEqual('waiting_approval', result['status'])
        self.assertEqual('test.drop', result['pending']['tool'])
        self.assertEqual(['test.keep', 'test.drop', 'test.keep'], effects)

    def test_restoring_rewound_session_replaces_other_runtime_cached_grants_and_workspace(self):
        store = self._seed()
        runtime = AgentRuntime(offline=True, session_root=self.root / 'sessions')
        runtime.policy.allow_for_session('test.drop', 'rewind01')
        runtime._workspaces['rewind01'] = {'topic': 'stale process cache'}
        runtime._session_results['rewind01'] = {'stale': True}
        runtime._pending['removed-call'] = ('rewind01', None, [], 0)
        session_workspace.rewind_session('rewind01')
        # Another runtime may already have rebuilt the driver after the rewind.
        store.append('rewind01', 'user_message', {'text': 'new retained turn', 'mode': 'agent'})
        store.append('rewind01', 'driver_started', {'driver': 'adaptive'})
        (self.root / 'sessions' / 'rewind01.workspace.json').write_text(json.dumps({
            'session_id': 'rewind01', 'workspace': {'topic': 'actual saved effects'},
            'results': {}, 'children': []}), encoding='utf-8')
        runtime._restore_session_meta('rewind01')
        self.assertEqual('ask', runtime.policy.decision('test.drop', 'write', session_id='rewind01'))
        self.assertEqual({'topic': 'actual saved effects'}, runtime._workspaces['rewind01'])
        self.assertNotIn('rewind01', runtime._session_results)
        self.assertNotIn('removed-call', runtime._pending)

    def test_running_rewind_rejects_without_waiting_for_turn_lock(self):
        from src.session_lock import session_lock

        store = self._seed()
        store.append('rewind01', 'user_message', {'text': 'model is running', 'mode': 'agent'})
        result, finished = [], threading.Event()

        def rewind():
            result.append(session_workspace.handle({'cmd': 'session_rewind', 'session_id': 'rewind01'}))
            finished.set()

        worker = threading.Thread(target=rewind)
        try:
            with session_lock(self.root / 'sessions' / 'rewind01.chat-turn'):
                worker.start()
                self.assertTrue(finished.wait(1), 'an active turn must be rejected before waiting on its lock')
        finally:
            worker.join(2)
        self.assertFalse(result[0]['ok'])
        self.assertIn('/cancel', result[0]['error'])

    def test_session_can_continue_after_rewind_with_consistent_projection(self):
        self._seed()
        result = session_workspace.rewind_session('rewind01', 1)
        self.assertEqual(5, result['removed'])
        runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=self.root / 'sessions')
        with mock.patch('src.bridge._runtime', return_value=runtime), \
                mock.patch.object(llm, 'available', return_value=True), \
                mock.patch.object(llm, 'chat', return_value=response('回滚后的新回答')):
            outcome = session_workspace.handle(
                {'cmd': 'chat_turn', 'session_id': 'rewind01', 'request': '换个角度继续', 'online': True})
        self.assertTrue(outcome['ok'], outcome)
        store = SessionStore(self.root / 'sessions')
        snap = store.snapshot('rewind01')
        self.assertEqual('done', snap['status'])
        self.assertEqual('换个角度继续', snap['request'])
        events = store.read('rewind01')
        user_turns = [e for e in events if e['type'] == 'user_message']
        self.assertEqual(['第一轮问题', '换个角度继续'],
                         [e['payload']['text'] for e in user_turns])
        self.assertIn('回滚后的新回答', [e['payload'].get('content') for e in events
                                     if e['type'] == 'assistant_message'])

    def test_chat_get_carries_new_event_types_verbatim(self):
        self._seed()
        result = session_workspace.handle({'cmd': 'chat_get', 'session_id': 'rewind01'})
        self.assertTrue(result['ok'], result)
        types = [e['type'] for e in result['events']]
        self.assertIn('assistant_delta', types)
        self.assertIn('subagent_started', types)
        self.assertIn('subagent_completed', types)
        delta = next(e for e in result['events'] if e['type'] == 'assistant_delta')
        self.assertEqual({'step': 1, 'delta': '第一'}, delta['payload'])
