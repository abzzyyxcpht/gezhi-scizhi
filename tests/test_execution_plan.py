from __future__ import annotations

import importlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src.agent_runtime import AgentRuntime, SessionStore


class ExecutionPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(Path(self.tmp.name))
        self.sid = 'session-one'
        self.turn(self.sid, 'turn-one')

    def api(self):
        try:
            return importlib.import_module('src.execution_plan')
        except ModuleNotFoundError:
            self.fail('The durable execution-plan API is not implemented')

    def turn(self, sid, turn_id=None):
        self.store.append(sid, 'user_message', {'text': '核查材料并整理结论', 'mode': 'agent'})
        if turn_id:
            self.store.append(sid, 'turn_started', {'turn_id': turn_id})

    @staticmethod
    def step(identifier='read', **changes):
        return dict(id=identifier, title='读取材料', criterion='返回材料内容',
                    kind='execution', status='pending', evidence=[], reason='', **changes)

    def update(self, steps, revision=0, call_id='plan-call', sid=None):
        sid = sid or self.sid
        arguments = {'expected_revision': revision, 'steps': steps,
                     'expected_turn_id': self.api().read_plan(self.store, sid)['turn_id']}
        events = self.store.read(sid)
        start = max(index for index, event in enumerate(events) if event['type'] == 'user_message')
        if not any(event['type'] == 'tool_call' and event['payload'].get('id') == call_id
                   for event in events[start:]):
            self.store.append(sid, 'tool_call', {'id': call_id, 'name': 'plan.update', 'arguments': arguments})
        return self.api().update_plan(self.store, sid, arguments, call_id)

    def receipt(self, call_id='read-call', tool='file.read', result=None, sid=None):
        sid = sid or self.sid
        self.store.append(sid, 'tool_call', {'id': call_id, 'name': tool, 'arguments': {}})
        self.store.append(sid, 'tool_result', {'id': call_id, 'name': tool,
                          'result': {'ok': True, 'executed': True, 'content': '材料原文'}
                          if result is None else result})

    def test_initial_plan_has_stable_identity_and_recovers_from_new_store(self):
        result = self.update([self.step()])
        self.assertTrue(result['ok'], result)
        plan = result['plan']
        self.assertEqual((plan['turn_id'], plan['revision'], plan['total']), ('turn-one', 1, 1))
        self.assertFalse(plan['ready'])
        self.assertEqual(plan, self.api().read_plan(SessionStore(self.store.root), self.sid))
        self.assertTrue(self.api().completion_issue(self.store, self.sid))

    def test_execution_completion_requires_paired_successful_receipt(self):
        self.receipt()
        step = {**self.step(), 'status': 'completed', 'evidence': ['read-call']}
        result = self.update([step])
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['plan']['ready'])
        self.assertEqual(result['plan']['steps'][0]['evidence_kind'], 'tool_receipt')
        self.assertEqual(result['plan']['steps'][0]['evidence_details'][0]['tool'], 'file.read')
        self.assertEqual(self.api().completion_issue(self.store, self.sid), '')

    def test_reasoning_completion_is_explicit_model_statement(self):
        step = {**self.step(), 'kind': 'reasoning', 'status': 'completed'}
        self.assertFalse(self.update([step])['ok'])
        step['reason'] = '已说明材料范围与局限，未执行实验。'
        result = self.update([step])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['plan']['steps'][0]['evidence_kind'], 'model_statement')

    def test_bad_receipts_cannot_support_completion(self):
        bad = [{'ok': False, 'executed': True}, {'ok': True, 'executed': False},
               {'ok': True, 'exit_code': 1}, {'ok': True, 'timed_out': True},
               {'ok': True, 'cancelled': True}, {'ok': True, 'status': 'running'},
               {'ok': True, 'status': 'waiting_children'}, {'ok': True, 'status': 'pending'},
               {'ok': True, 'status': 'paused'}, {'ok': True, 'output_unavailable': True},
               {'ok': 'true'}, {'ok': True, 'control_state_error': True},
               {'ok': True, 'skipped': True}, {'ok': True, 'status': 'partial'},
               {'ok': True, 'status': ['done']}]
        for index, result in enumerate(bad):
            with self.subTest(result=result):
                call = f'bad-{index}'
                self.receipt(call, result=result)
                self.assertFalse(self.update([{**self.step(), 'status': 'completed',
                                              'evidence': [call]}], call_id=f'p-{index}')['ok'])
        self.assertFalse(self.api().read_plan(self.store, self.sid)['has_plan'])

    def test_missing_unpaired_mismatched_and_plan_receipts_are_rejected(self):
        self.receipt('plan-evidence', 'plan.update')
        self.store.append(self.sid, 'tool_result', {'id': 'orphan', 'name': 'file.read', 'result': {'ok': True}})
        self.store.append(self.sid, 'tool_call', {'id': 'mismatch', 'name': 'file.read'})
        self.store.append(self.sid, 'tool_result', {'id': 'mismatch', 'name': 'file.write', 'result': {'ok': True}})
        for call in ('unknown', 'plan-evidence', 'orphan', 'mismatch'):
            with self.subTest(call=call):
                self.assertFalse(self.update([{**self.step(), 'status': 'completed', 'evidence': [call]}])['ok'])

    def test_cross_turn_and_cross_session_evidence_is_not_reused(self):
        self.receipt('prior')
        self.turn(self.sid, 'turn-two')
        self.turn('other', 'other-turn')
        self.receipt('foreign', sid='other')
        for call in ('prior', 'foreign'):
            self.assertFalse(self.update([{**self.step(), 'status': 'completed', 'evidence': [call]}])['ok'])

    def test_new_turn_does_not_inherit_plan_and_rewind_restores_previous_turn(self):
        original = self.update([self.step()])['plan']
        path = self.store._path(self.sid)
        before = path.read_bytes()
        self.turn(self.sid, 'turn-two')
        self.assertFalse(self.api().read_plan(self.store, self.sid)['has_plan'])
        path.write_bytes(before)
        self.assertEqual(self.api().read_plan(self.store, self.sid), original)

    def test_legacy_turn_identity_stable_and_separate_for_same_text(self):
        self.turn('legacy')
        first = self.api().read_plan(self.store, 'legacy')['turn_id']
        self.assertTrue(first)
        self.assertEqual(first, self.api().read_plan(SessionStore(self.store.root), 'legacy')['turn_id'])
        self.turn('legacy')
        self.assertNotEqual(first, self.api().read_plan(self.store, 'legacy')['turn_id'])

    def test_revision_conflict_does_not_append(self):
        self.update([self.step()])
        before = [event for event in self.store.read(self.sid) if event['type'] == 'plan_updated']
        result = self.update([self.step()], call_id='stale')
        self.assertFalse(result['ok'])
        self.assertEqual([event for event in self.store.read(self.sid) if event['type'] == 'plan_updated'], before)

    def test_tool_call_id_is_idempotent_but_cannot_change_arguments(self):
        first = self.update([self.step()])
        before = self.store.read(self.sid)
        again = self.update([self.step()])
        self.assertTrue(again['ok'], again)
        self.assertEqual(again['plan'], first['plan'])
        self.assertEqual(before, self.store.read(self.sid))
        different = self.update([{**self.step(), 'title': '不同事项'}])
        self.assertFalse(different['ok'])

    def test_old_steps_cannot_be_deleted_or_changed_to_reasoning(self):
        self.update([self.step(), self.step('report')])
        self.assertFalse(self.update([self.step()], 1, 'delete')['ok'])
        self.assertFalse(self.update([{**self.step(), 'kind': 'reasoning'}, self.step('report')], 1, 'kind')['ok'])

    def test_blocked_and_cancelled_need_reason_and_never_mean_ready(self):
        for status in ('blocked', 'cancelled'):
            with self.subTest(status=status):
                step = {**self.step(), 'status': status}
                self.assertFalse(self.update([step])['ok'])
                step['reason'] = '材料尚未提供'
                result = self.update([step], call_id=f'valid-{status}',
                                     revision=self.api().read_plan(self.store, self.sid)['revision'])
                self.assertTrue(result['ok'], result)
                self.assertFalse(result['plan']['ready'])

    def test_multiple_in_progress_and_invalid_payloads_are_rejected(self):
        invalid = [[], [self.step()] * 13, [self.step(), self.step()],
                   [{**self.step(), 'status': 'in_progress'}, {**self.step('b'), 'status': 'in_progress'}],
                   [{**self.step(), 'title': 'a' * 500}], [{**self.step(), 'unknown': True}]]
        for steps in invalid:
            with self.subTest(steps=steps):
                self.assertFalse(self.update(steps)['ok'])

    def test_terminal_or_cancelled_turn_rejects_new_update(self):
        for kind, payload in [('completed', {'status': 'done'}), ('error', {}), ('cancel_requested', {})]:
            sid = f'session-{kind}'
            self.turn(sid)
            self.store.append(sid, kind, payload)
            self.assertFalse(self.update([self.step()], sid=sid)['ok'])
        self.store.root.joinpath(f'{self.sid}.cancel').touch()
        self.assertFalse(self.update([self.step()])['ok'])

    def test_revision_compare_and_swap_serializes_parallel_writers(self):
        barrier = threading.Barrier(2)
        results = []
        def write(index):
            barrier.wait()
            results.append(self.update([self.step()], call_id=f'parallel-{index}'))
        workers = [threading.Thread(target=write, args=(index,)) for index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertEqual(sum(bool(result['ok']) for result in results), 1)
        self.assertEqual(self.api().read_plan(self.store, self.sid)['revision'], 1)

    def test_child_plan_and_receipts_are_isolated(self):
        self.store.register_sidechain('child', self.sid)
        self.turn('child', 'child-turn')
        self.receipt('parent-receipt')
        self.assertFalse(self.update([{**self.step(), 'status': 'completed',
                                      'evidence': ['parent-receipt']}], sid='child')['ok'])
        self.assertTrue(self.update([self.step()], sid='child')['ok'])
        self.assertFalse(self.api().read_plan(self.store, self.sid)['has_plan'])

    def test_read_revalidates_receipts_instead_of_trusting_plan_fields(self):
        self.receipt()
        self.update([{**self.step(), 'status': 'completed', 'evidence': ['read-call']}])
        rows = self.store.read(self.sid)
        for row in rows:
            if row['type'] == 'tool_result':
                row['payload']['result']['ok'] = False
        self.store._path(self.sid).write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        self.assertFalse(self.api().read_plan(self.store, self.sid)['ready'])
        self.assertTrue(self.api().completion_issue(self.store, self.sid))

    def test_plan_and_context_are_bounded_and_redacted(self):
        result = self.update([{**self.step(), 'title': 'API_KEY=secret-not-for-public'}])
        self.assertTrue(result['ok'], result)
        self.assertNotIn('secret-not-for-public', json.dumps(result))
        text = self.api().context_text(self.store, self.sid)
        self.assertLessEqual(len(text), 6000)
        self.assertIn('read', text)
        plan_events = [event for event in self.store.read(self.sid) if event['type'] == 'plan_updated']
        self.assertNotIn('secret-not-for-public', json.dumps(plan_events))

    def test_tools_have_scoped_metadata_only_contract(self):
        runtime = AgentRuntime(offline=True, llm_enabled=False, session_root=self.store.root)
        self.api().register_tools(runtime)
        self.assertEqual(runtime.tools['plan.update'].capability, 'write')
        self.assertEqual(runtime.tools['plan.read'].capability, 'read')
        self.assertTrue(runtime.tools['plan.update'].retry_safe)
        for index in range(23):
            self.receipt(f'candidate-{index}')
        read = runtime.tools['plan.read'].handler({'__session_id': self.sid, '__tool_call_id': 'read-plan'})
        self.assertTrue(read['ok'])
        self.assertEqual(len(read['receipt_candidates']), 20)
        self.assertEqual(read['receipt_candidates'][-1]['call_id'], 'candidate-22')
        args = {'__session_id': self.sid, '__tool_call_id': 'update-tool', 'expected_revision': 0,
                'expected_turn_id': 'turn-one',
                'steps': [self.step()], 'session_id': 'other'}
        self.assertFalse(runtime.tools['plan.update'].handler(args)['ok'])

    def test_old_turn_cannot_write_revision_zero_into_new_turn(self):
        self.turn(self.sid, 'turn-two')
        arguments = {'expected_turn_id': 'turn-one', 'expected_revision': 0, 'steps': [self.step()]}
        self.store.append(self.sid, 'tool_call', {'id': 'late', 'name': 'plan.update', 'arguments': arguments})
        result = self.api().update_plan(self.store, self.sid, arguments, 'late')
        self.assertFalse(result['ok'])
        self.assertFalse(self.api().read_plan(self.store, self.sid)['has_plan'])

    def test_update_requires_real_current_plan_call(self):
        arguments = {'expected_turn_id': 'turn-one', 'expected_revision': 0, 'steps': [self.step()]}
        self.assertFalse(self.api().update_plan(self.store, self.sid, arguments, 'invented')['ok'])
        self.receipt('read-call')
        self.assertFalse(self.api().update_plan(self.store, self.sid, arguments, 'read-call')['ok'])

    def test_corrupt_plan_cannot_disappear_as_no_plan(self):
        self.store.append(self.sid, 'plan_updated', {'revision': 1, 'steps': []})
        with self.assertRaises(ValueError):
            self.api().read_plan(self.store, self.sid)

    def test_cached_views_are_detached_and_replaced_journal_is_revalidated(self):
        original = self.update([self.step()])['plan']
        original['steps'][0]['title'] = 'changed by UI'
        self.assertEqual(self.api().read_plan(self.store, self.sid)['steps'][0]['title'], '读取材料')
        self.store._path(self.sid).write_text('', encoding='utf-8')
        self.assertFalse(self.api().read_plan(self.store, self.sid)['has_plan'])

    def test_unknown_session_is_not_a_valid_empty_plan(self):
        with self.assertRaises(ValueError):
            self.api().read_plan(self.store, 'missing')

    def test_receipt_candidates_remain_bounded_and_cache_avoids_repeat_full_reads(self):
        self.update([self.step()])
        api = self.api()
        with mock.patch.object(api, '_read_journal', wraps=getattr(api, '_read_journal', None), create=True) as read:
            self.api().read_plan(self.store, self.sid)
            self.api().read_plan(self.store, self.sid)
            read.assert_not_called()
            self.receipt('new-read')
            self.api().read_plan(self.store, self.sid)
            read.assert_called_once()

    def test_append_failure_does_not_claim_no_effect_and_idempotent_retry_recovers(self):
        api = self.api()
        arguments = {'expected_turn_id': 'turn-one', 'expected_revision': 0, 'steps': [self.step()]}
        self.store.append(self.sid, 'tool_call', {'id': 'commit-failure', 'name': 'plan.update', 'arguments': arguments})
        append = self.store.append
        def committed_then_failed(sid, kind, payload):
            append(sid, kind, payload)
            raise OSError('simulated after-commit fault')
        with mock.patch.object(self.store, 'append', side_effect=committed_then_failed):
            result = api.update_plan(self.store, self.sid, arguments, 'commit-failure')
        self.assertFalse(result['ok'])
        self.assertTrue(result['executed'])
        recovered = api.update_plan(self.store, self.sid, arguments, 'commit-failure')
        self.assertTrue(recovered['ok'], recovered)
        self.assertEqual(recovered['plan']['revision'], 1)

    def test_duplicate_receipt_cannot_make_an_ambiguous_execution_successful(self):
        self.receipt()
        self.receipt()
        self.assertFalse(self.update([{**self.step(), 'status': 'completed', 'evidence': ['read-call']}])['ok'])

    def test_invalid_journal_tail_never_reuses_cached_completion(self):
        self.receipt()
        self.assertTrue(self.update([{**self.step(), 'status': 'completed', 'evidence': ['read-call']}])['plan']['ready'])
        path = self.store._path(self.sid)
        valid = path.read_bytes()
        bad_tails = [b'{"type":"plan_updated","payload":', b'\xff\xfe\n', b'[]\n',
                     b'{"type":"plan_updated","payload":[]}\n',
                     json.dumps({'type': 'plan_updated', 'payload': {}, 'session_id': 'other'}).encode() + b'\n',
                     json.dumps({'type': 3, 'payload': {}, 'session_id': self.sid}).encode() + b'\n',
                     json.dumps({'type': 'completed', 'payload': {}, 'session_id': self.sid}).encode()]
        for tail in bad_tails:
            with self.subTest(tail=tail):
                path.write_bytes(valid + tail)
                with self.assertRaises(ValueError):
                    self.api().read_plan(self.store, self.sid)
                with self.assertRaises(ValueError):
                    self.api().completion_issue(self.store, self.sid)
                path.write_bytes(valid)
                self.assertTrue(self.api().read_plan(self.store, self.sid)['ready'])

    def test_journal_changed_during_read_is_temporarily_unavailable(self):
        self.receipt()
        self.update([{**self.step(), 'status': 'completed', 'evidence': ['read-call']}])
        api = self.api()
        self.store.append(self.sid, 'assistant_message', {'content': 'trigger an uncached read'})
        reader = getattr(api, '_read_journal', None)
        def read_then_append(path, sid):
            events = reader(path, sid)
            self.store.append(sid, 'assistant_message', {'content': 'concurrent event'})
            return events
        with mock.patch.object(api, '_read_journal', side_effect=read_then_append, create=True):
            with self.assertRaises(ValueError):
                api.read_plan(self.store, self.sid)

    def test_unicode_line_separators_inside_valid_json_text_remain_data(self):
        self.store.append(self.sid, 'assistant_message', {'content': '甲\u2028乙\u2029丙'})
        result = self.update([self.step()])
        self.assertTrue(result['ok'], result)

