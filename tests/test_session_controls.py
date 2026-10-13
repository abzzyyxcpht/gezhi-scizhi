"""Shared session observations and explicit, offline context maintenance."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.agent_runtime import SessionStore
from src.state_store import write_json
from src import session_controls as controls


class SessionControlsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(Path(self.tmp.name) / 'sessions')
        self.sid = 'observe01'
        self.path = self.store.root / f'{self.sid}.driver.json'
        self.store.append(self.sid, 'user_message', {'text': '研究目标', 'mode': 'agent'})
        self.store.append(self.sid, 'driver_started', {'driver': 'adaptive'})
        self.store.append(self.sid, 'completed', {'status': 'done'})
        messages = [{'role': 'system', 'content': 'Keep evidence and permission boundaries.'},
                    {'role': 'user', 'content': '研究目标'}]
        for index in range(10):
            messages += [{'role': 'user', 'content': f'问题{index} ' + '材料内容 ' * 150},
                         {'role': 'assistant', 'content': f'观察{index} ' + '核验结果 ' * 150}]
        self.state = {'schema_version': 1, 'session_id': self.sid, 'status': 'done',
            'messages': messages, 'original_request': '研究目标', 'tokens': 30,
            'model_calls': 2, 'steps': 2, 'max_steps': 16, 'token_budget': 1000,
            'usage_complete': True, 'context_limit_tokens': 32000, 'compressions': 0,
            'pending': None, 'queue': [], 'active_call': None, 'active_calls': [],
            'model_identity': {'model': 'recorded-model', 'protocol': 'responses',
                               'credential_fingerprint': 'must-not-be-public', 'base_url': 'https://private.example'},
            'turn_usage': [{'tokens': 50, 'model_calls': 3, 'usage_complete': False}]}
        write_json(self.path, self.state)

    def status(self):
        return controls.session_status(self.sid, store=self.store)

    def compact(self):
        return controls.compact_session(self.sid, store=self.store)

    def test_status_is_read_only_and_preserves_measurement_scopes(self):
        before = self.path.read_bytes(), self.store.read(self.sid)
        result = self.status()
        self.assertEqual('recorded-model', result['model']['model'])
        self.assertEqual(30, result['tokens'])
        self.assertEqual(80, result['session_usage']['tokens'])
        self.assertFalse(result['session_usage']['usage_complete'])
        self.assertEqual(970, result['budget']['remaining_tokens'])
        self.assertEqual(14, result['budget']['remaining_steps'])
        self.assertEqual('heuristic_tokens', result['context']['measurement'])
        self.assertGreater(result['context']['tokens'], 0)
        serialized = json.dumps(result)
        for hidden in ['must-not-be-public', 'private.example', '材料内容', 'Keep evidence']:
            self.assertNotIn(hidden, serialized)
        self.assertEqual(before, (self.path.read_bytes(), self.store.read(self.sid)))

    def test_context_pressure_includes_saved_schemas_and_visible_output_bound(self):
        from src.agent_driver import AdaptiveDriver, AGENT_STEP_MAX_TOKENS
        self.state['tool_schemas'] = [{'type': 'function', 'function': {
            'name': 'file.read', 'description': 'Read evidence ' * 100,
            'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}}]
        write_json(self.path, self.state)
        before = self.path.read_bytes(), self.store.read(self.sid)
        with mock.patch('src.model_registry.resolve', side_effect=AssertionError('live configuration')):
            context = self.status()['context']
        self.assertIn('schema_tokens', context)
        self.assertEqual(AdaptiveDriver._estimate_tokens(self.state['messages']), context['tokens'])
        self.assertGreater(context['schema_tokens'], 0)
        self.assertEqual(context['tokens'] + context['schema_tokens'], context['input_tokens'])
        self.assertEqual(min(AGENT_STEP_MAX_TOKENS, 970), context['output_reserve_tokens'])
        self.assertEqual(context['input_tokens'] + context['output_reserve_tokens'], context['pressure_tokens'])
        self.assertEqual(before, (self.path.read_bytes(), self.store.read(self.sid)))

    def test_missing_schema_or_budget_remains_unknown_not_zero(self):
        self.state.pop('token_budget')
        self.state.pop('tokens')
        write_json(self.path, self.state)
        context = self.status()['context']
        self.assertIn('schema_tokens', context)
        for key in ('schema_tokens', 'input_tokens', 'output_reserve_tokens', 'pressure_tokens'):
            self.assertIsNone(context[key])
        self.state['tool_schemas'] = []
        write_json(self.path, self.state)
        context = self.status()['context']
        self.assertEqual(0, context['schema_tokens'])
        self.assertEqual(context['tokens'], context['input_tokens'])
        self.assertIsNone(context['pressure_tokens'])

    def test_unchanged_driver_observation_uses_bounded_derived_cache(self):
        self.status()
        with mock.patch.object(controls, '_load_state', side_effect=AssertionError('reread')):
            result = self.status()
        result['model']['model'] = 'poison'
        self.assertEqual('recorded-model', self.status()['model']['model'])

    def test_unknown_model_and_rules_do_not_guess_live_settings(self):
        self.path.unlink()
        result = self.status()
        self.assertIsNone(result['tokens'])
        self.assertIsNone(result['context'])
        self.assertIsNone(result['budget'])
        self.assertIsNone(result['model']['model'])
        self.assertFalse(result['can_compact'])

    def test_old_usage_completeness_remains_unknown_not_incomplete(self):
        self.state.pop('usage_complete')
        self.state['turn_usage'] = [{'tokens': 50, 'model_calls': 3}]
        write_json(self.path, self.state)
        result = self.status()
        self.assertIsNone(result['usage_complete'])
        self.assertIsNone(result['session_usage']['usage_complete'])
        self.state['turn_usage'][0]['usage_complete'] = False
        write_json(self.path, self.state)
        self.assertFalse(self.status()['session_usage']['usage_complete'])

    def test_malformed_driver_is_reported_without_exposing_content(self):
        self.path.write_text('private credential broken-json', encoding='utf-8')
        result = self.status()
        self.assertIsNone(result['context'])
        self.assertFalse(result['can_compact'])
        self.assertNotIn('private credential', json.dumps(result))

    def test_manual_compaction_retains_history_and_recent_messages_without_llm(self):
        events = self.store.read(self.sid)
        with mock.patch('src.llm.chat', side_effect=AssertionError('must not call a model')):
            result = self.compact()
        state = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertTrue(result['changed'])
        self.assertLess(result['after_tokens'], result['before_tokens'])
        self.assertEqual(self.state['messages'][:2], state['messages'][:2])
        self.assertEqual(self.state['messages'][-8:], state['messages'][-8:])
        self.assertEqual(self.state['messages'][2:-8], state['context_archive'][-1])
        self.assertEqual(30, state['tokens'])
        self.assertEqual(2, state['model_calls'])
        self.assertEqual(0, result['model_calls'])
        self.assertEqual(events, self.store.read(self.sid)[:-1])
        self.assertEqual('context_compressed', self.store.read(self.sid)[-1]['type'])
        self.assertEqual('done', self.status()['status'])
        self.assertEqual(1, self.status()['context']['compressions'])

    def test_repeat_and_short_history_are_honest_noops(self):
        self.compact()
        before = self.path.read_bytes(), self.store.read(self.sid)
        result = self.compact()
        self.assertFalse(result['changed'])
        self.assertEqual(before, (self.path.read_bytes(), self.store.read(self.sid)))

    def test_tool_pair_is_never_split_at_retained_boundary(self):
        messages = copy.deepcopy(self.state['messages'])
        messages[-8] = {'role': 'tool', 'tool_call_id': 'call1', 'content': '{"ok":true}'}
        messages[-9] = {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'call1', 'type': 'function', 'function': {'name': 'file.read', 'arguments': '{}'}}]}
        self.state['messages'] = messages
        write_json(self.path, self.state)
        self.assertTrue(self.compact()['changed'])
        state = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(messages[-9:], state['messages'][-9:])

    def test_running_and_pending_work_fail_before_locking(self):
        for flag, value in [('queue', [{'name': 'file.write'}]), ('pending', {'id': 'p'}),
                            ('active_calls', [{'id': 'a'}]), ('model_inflight', 'step')]:
            with self.subTest(flag=flag):
                write_json(self.path, {**self.state, flag: value})
                with mock.patch.object(controls, 'session_lock', side_effect=AssertionError('must not wait')):
                    with self.assertRaises(ValueError):
                        self.compact()
        write_json(self.path, self.state)
        self.store.append(self.sid, 'user_message', {'text': 'active', 'mode': 'agent'})
        with mock.patch.object(controls, 'session_lock', side_effect=AssertionError('must not wait')):
            with self.assertRaises(ValueError):
                self.compact()

    def test_write_failure_leaves_original_history_and_driver(self):
        before = self.path.read_bytes(), self.store.read(self.sid)
        with mock.patch.object(controls, 'write_json', side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError):
                self.compact()
        self.assertEqual(before, (self.path.read_bytes(), self.store.read(self.sid)))

    def test_invalid_ids_do_not_read_outside_the_store(self):
        for sid in ('../private', '', 'x' * 129):
            with self.assertRaises(ValueError):
                controls.session_status(sid, store=self.store)

    def test_rewind_marker_prevents_stale_driver_observation_and_mutation(self):
        self.store.append(self.sid, 'completed', {'status': 'done', 'reason': 'rewound'})
        result = self.status()
        self.assertIsNone(result['context'])
        self.assertFalse(result['can_compact'])
        with self.assertRaises(ValueError):
            self.compact()

    def test_version_change_while_acquiring_lock_rejects_newer_turn(self):
        from contextlib import contextmanager
        @contextmanager
        def changed(_path):
            self.store.append(self.sid, 'completed', {'status': 'done', 'reason': 'another_turn'})
            yield
        before = self.path.read_bytes()
        with mock.patch.object(controls, 'session_lock', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '会话已变化'):
                self.compact()
        self.assertEqual(before, self.path.read_bytes())

    def test_new_turn_finishing_during_status_read_is_not_compacted(self):
        real_status = controls.session_status
        def advance(sid, **kwargs):
            result = real_status(sid, **kwargs)
            newer = copy.deepcopy(self.state)
            newer['messages'].extend([{'role': 'user', 'content': '新一轮'},
                                      {'role': 'assistant', 'content': '已完成'}])
            write_json(self.path, newer)
            self.store.append(sid, 'user_message', {'text': '新一轮', 'mode': 'agent'})
            self.store.append(sid, 'completed', {'status': 'done'})
            return result
        with mock.patch.object(controls, 'session_status', side_effect=advance):
            with self.assertRaisesRegex(ValueError, '会话已变化'):
                self.compact()
        self.assertEqual(0, json.loads(self.path.read_text(encoding='utf-8'))['compressions'])

    def test_committed_compaction_reports_journal_failure_honestly(self):
        with mock.patch.object(self.store, 'append', side_effect=OSError('log failure')):
            result = self.compact()
        self.assertTrue(result['changed'])
        self.assertIn('记录写入失败', result['message'])
        self.assertEqual(1, json.loads(self.path.read_text(encoding='utf-8'))['compressions'])

    def test_bridge_contract_and_remote_permissions(self):
        from types import SimpleNamespace
        from src import bridge, remote_control
        with mock.patch.object(bridge, '_runtime', return_value=SimpleNamespace(sessions=self.store)):
            read = bridge.handle({'cmd': 'session_status', 'session_id': self.sid})
            compact = bridge.handle({'cmd': 'session_compact', 'session_id': self.sid})
        self.assertTrue(read['ok'])
        self.assertTrue(compact['result']['changed'])
        self.assertEqual(1, compact['status']['context']['compressions'])
        self.assertIn('session_status', remote_control._READ_ONLY)
        self.assertNotIn('session_compact', remote_control._READ_ONLY)
        for command in ['session_status', 'session_compact']:
            self.assertEqual({'cmd': command, 'session_id': self.sid},
                             remote_control._validated({'cmd': command, 'session_id': self.sid}))
            with self.assertRaises(ValueError):
                remote_control._validated({'cmd': command, 'session_id': self.sid, 'path': 'private'})
