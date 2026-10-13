from __future__ import annotations

import copy
import tempfile
import threading
import unittest
from pathlib import Path
import json
from unittest import mock

from src import llm, settings
from src.agent_runtime import AgentRuntime
from tests.test_adaptive_driver import response


class SessionSteeringTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(settings, '_PATH', self.root / 'settings.json')
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(llm, 'available', return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        settings.save({'experimental_inflight_steering': True, 'agent_stream_enabled': False})
        self.runtime = AgentRuntime(session_root=self.root, offline=False, llm_enabled=True)

    def api(self, cmd, **fields):
        from src.session_steering import handle
        return handle({'cmd': cmd, **fields}, store=self.runtime.sessions)

    def add(self, sid, text='优先核对反例', key='correction-1', turn=None):
        status = self.api('steer_status', session_id=sid)['steering']
        return self.api('steer_add', session_id=sid, expected_turn_id=turn or status['turn_id'],
                        steer_request_id=key, request=text)

    def test_steer_during_model_call_enters_same_turn_without_resetting_budget(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        seen, result = [], {}
        def model(messages, **kwargs):
            seen.append(copy.deepcopy(messages))
            if len(seen) == 1:
                entered.set()
                release.wait(3)
                return response('原来的回答')
            return response('已核对反例')
        with mock.patch.object(llm, 'chat', side_effect=model):
            worker = threading.Thread(target=lambda: result.update(self.runtime.start('综述证据')))
            worker.start()
            self.addCleanup(lambda: (release.set(), worker.join(5)))
            self.assertTrue(entered.wait(2))
            sid = self.runtime.sessions.list()[0]['session_id']
            first = self.add(sid)
            self.assertTrue(first['ok'], first)
            self.assertEqual(first['ticket']['id'], self.add(sid)['ticket']['id'])
            self.assertFalse(self.add(sid, turn='stale-turn')['ok'])
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual('done', result['status'])
        self.assertEqual(2, result['steps'])
        self.assertEqual(30, result['tokens'])
        self.assertEqual('优先核对反例', seen[-1][-1]['content'])
        events = self.runtime.sessions.read(sid)
        self.assertEqual(1, sum(e['type'] == 'user_message' for e in events))
        self.assertEqual(1, sum(e['type'] == 'steer_applied' for e in events))
        self.assertFalse(self.add(sid, key='late')['ok'])

    def test_pending_approval_is_replanned_without_granting_old_permission(self):
        writer = mock.Mock(return_value={'ok': True})
        self.runtime._register('test.write', 'write', 'Write', writer, self.runtime._obj_schema({}))
        with mock.patch.object(llm, 'chat', return_value=response(tool='test.write')):
            result = self.runtime.start('先写入')
        sid = result['session_id']
        self.assertEqual('waiting_approval', result['status'])
        self.assertTrue(self.add(sid, text='不要写入，只解释')['ok'])
        with self.assertRaises(ValueError):
            self.runtime.approve(sid, 'call-1', 'allow')
        with mock.patch.object(llm, 'chat', return_value=response('只解释')) as model:
            finished = self.runtime.continue_session(sid)
        self.assertEqual('done', finished['status'])
        writer.assert_not_called()
        messages = model.call_args.args[0]
        self.assertEqual('不要写入，只解释', messages[-1]['content'])
        self.assertTrue(any(m.get('tool_call_id') == 'call-1' and 'steered' in m.get('content', '') for m in messages))
        self.assertFalse(any(e['type'] == 'permission_granted' for e in self.runtime.sessions.read(sid)))

    def test_next_tool_is_skipped_after_running_tool_finishes(self):
        writer = mock.Mock(return_value={'ok': True})
        def first(args):
            result = self.add(args['__session_id'])
            self.assertTrue(result['ok'], result)
            return {'ok': True, 'observed': 'finished first tool'}
        self.runtime._register('test.first', 'read', 'Read', first, self.runtime._obj_schema({}))
        self.runtime._register('test.second', 'write', 'Write', writer, self.runtime._obj_schema({}))
        batch = response(tool='test.first')
        extra = response(tool='test.second', call_id='second')
        batch['tool_calls'] += extra['tool_calls']
        batch['assistant_message']['tool_calls'] += extra['assistant_message']['tool_calls']
        with mock.patch.object(llm, 'chat', side_effect=[batch, response('调整后结果')]) as model:
            outcome = self.runtime.start('读完再写')
        writer.assert_not_called()
        self.assertEqual('done', outcome['status'])
        messages = model.call_args.args[0]
        self.assertEqual(['call-1', 'second'], [m['tool_call_id'] for m in messages if m['role'] == 'tool'])
        skipped = [e for e in self.runtime.sessions.read(outcome['session_id']) if e['type'] == 'tool_skipped']
        self.assertEqual('second', skipped[0]['payload']['id'])

    def test_steering_does_not_create_new_step_budget(self):
        settings.save({'agent_max_steps': 1})
        def model(*args, **kwargs):
            sid = self.runtime.sessions.list()[0]['session_id']
            self.assertTrue(self.add(sid)['ok'])
            return response('原有结果')
        with mock.patch.object(llm, 'chat', side_effect=model) as model:
            outcome = self.runtime.start('受限研究')
        self.assertEqual('step_budget', outcome['reason'])
        self.assertEqual(1, model.call_count)

    def test_cancel_expires_received_input_and_never_calls_model_again(self):
        def model(*args, **kwargs):
            sid = self.runtime.sessions.list()[0]['session_id']
            self.assertTrue(self.add(sid)['ok'])
            self.runtime.cancel_session(sid)
            return response('过时回答')
        with mock.patch.object(llm, 'chat', side_effect=model) as model:
            outcome = self.runtime.start('可取消研究')
        self.assertEqual('cancelled', outcome['status'])
        self.assertEqual(1, model.call_count)
        status = self.api('steer_status', session_id=outcome['session_id'])['steering']
        self.assertEqual('expired', status['items'][0]['state'])

    def test_disabled_before_boundary_expires_message(self):
        def model(*args, **kwargs):
            sid = self.runtime.sessions.list()[0]['session_id']
            self.assertTrue(self.add(sid)['ok'])
            settings.save({'experimental_inflight_steering': False})
            return response('原有结果')
        with mock.patch.object(llm, 'chat', side_effect=model) as model:
            outcome = self.runtime.start('研究')
        self.assertEqual(1, model.call_count)
        status = self.api('steer_status', session_id=outcome['session_id'])['steering']
        self.assertEqual('expired', status['items'][0]['state'])

    def test_disable_then_reenable_before_boundary_does_not_revive_old_input(self):
        def model(*args, **kwargs):
            sid = self.runtime.sessions.list()[0]['session_id']
            self.assertTrue(self.add(sid)['ok'])
            settings.save({'experimental_inflight_steering': False})
            settings.save({'experimental_inflight_steering': True})
            return response('原有结果')
        with mock.patch.object(llm, 'chat', side_effect=model) as model:
            outcome = self.runtime.start('研究')
        self.assertEqual(1, model.call_count)
        status = self.api('steer_status', session_id=outcome['session_id'])['steering']
        self.assertEqual('expired', status['items'][0]['state'])

    def test_new_turn_rejects_old_expected_identity(self):
        with mock.patch.object(llm, 'chat', return_value=response('完成')):
            first = self.runtime.start('第一轮')
        sid = first['session_id']
        old = self.api('steer_status', session_id=sid)['steering']['turn_id']
        def next_model(*args, **kwargs):
            self.assertFalse(self.add(sid, turn=old)['ok'])
            return response('第二轮完成')
        with mock.patch.object(llm, 'chat', side_effect=next_model):
            self.runtime.send(sid, '第二轮')
        self.assertNotEqual(old, self.api('steer_status', session_id=sid)['steering']['turn_id'])

    def test_state_saved_before_missing_ack_deduplicates_on_explicit_resume(self):
        from src import session_steering
        self.runtime._register('test.write', 'write', 'Write', lambda _: {'ok': True}, self.runtime._obj_schema({}))
        with mock.patch.object(llm, 'chat', return_value=response(tool='test.write')):
            pending = self.runtime.start('暂停供恢复')
        sid = pending['session_id']
        self.assertTrue(self.add(sid)['ok'])
        original = session_steering._write
        def fail_ack(path, row):
            if any(item['state'] == 'applied' for item in row['items']):
                raise OSError('fixture interrupted receipt save')
            original(path, row)
        with mock.patch.object(session_steering, '_write', side_effect=fail_ack):
            with self.assertRaises(OSError):
                self.runtime.continue_session(sid)
        with mock.patch.object(llm, 'chat', return_value=response('恢复后完成')) as model:
            self.runtime.continue_session(sid)
        self.assertEqual(1, sum(m['role'] == 'user' and m.get('content') == '优先核对反例' for m in model.call_args.args[0]))
        state = json.loads((self.root / f'{sid}.driver.json').read_text(encoding='utf-8'))
        self.assertEqual(1, len(state['applied_steer_ids']))
        self.assertEqual(1, sum(e['type'] == 'steer_applied' for e in self.runtime.sessions.read(sid)))

    def test_rewind_rehydration_and_visible_transcript_keep_applied_correction(self):
        from src import session_workspace
        def first_model(*args, **kwargs):
            sid = self.runtime.sessions.list()[0]['session_id']
            self.assertTrue(self.add(sid)['ok'])
            return response('原始回答')
        count = [0]
        def model(*args, **kwargs):
            count[0] += 1
            return first_model(*args, **kwargs) if count[0] == 1 else response('更正回答')
        with mock.patch.object(llm, 'chat', side_effect=model):
            result = self.runtime.start('初始请求')
        sid = result['session_id']
        with mock.patch.object(session_workspace, '_store', return_value=self.runtime.sessions):
            self.assertTrue(any(row['role'] == 'user' and row['content'] == '优先核对反例'
                                for row in session_workspace.transcript(sid)))
        self.runtime.sessions.append(sid, 'completed', {'status': 'done', 'reason': 'rewound'})
        with mock.patch.object(llm, 'chat', return_value=response('继续')) as chat:
            self.runtime.send(sid, '回滚后继续')
        self.assertTrue(any(row['role'] == 'user' and row.get('content') == '优先核对反例'
                            for row in chat.call_args.args[0]))
