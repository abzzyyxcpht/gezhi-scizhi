"""A durable next-turn queue must not become steering or replay side effects."""
from __future__ import annotations

import importlib
import importlib.util
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from src import config, settings, session_workspace
from src.agent_runtime import SessionStore


def _process_dispatch(root, sid, expected, entered=None):
    from src.session_queue import SessionQueue
    config.DATA_DIR = Path(root)
    settings._PATH = Path(root) / 'settings.json'
    store = SessionStore(Path(root) / 'sessions')

    def dispatch(req):
        with (Path(root) / 'effects.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(req['queue_ticket'] + '\n')
        if entered is not None:
            entered.set()
            threading.Event().wait(30)
        store.append(sid, 'user_message', {'text': req['request'], 'agent': req['agent'], 'mode': req['mode'],
                                          'offline': not req['online'], 'queue_ticket': req['queue_ticket']})
        store.append(sid, 'completed', {'status': 'done'})
        return {'ok': True, 'result': {'status': 'done'}}

    queue = SessionQueue(Path(root), dispatch=dispatch, poll_interval=.01)
    try:
        result = queue.handle({'cmd': 'queue_resume', 'session_id': sid})
        if not result['ok']:
            raise AssertionError(result)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if queue.handle({'cmd': 'queue_list', 'session_id': sid})['queue']['counts']['delivered'] >= expected:
                return
            time.sleep(.01)
        raise AssertionError('child dispatcher did not drain')
    finally:
        queue.close()


class SessionQueueTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('src.session_queue'), 'The shared session queue is not implemented')
        self.module = importlib.import_module('src.session_queue')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for obj, name, value in ((config, 'DATA_DIR', self.root), (settings, '_PATH', self.root / 'settings.json')):
            patcher = mock.patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        settings.save({'experimental_message_queue': True, 'default_offline': True})
        self.store = SessionStore(self.root / 'sessions')
        self.sid = 'queue-session'
        self.store.append(self.sid, 'user_message', {'text': 'Original', 'mode': 'agent', 'agent': 'research', 'offline': True})
        self.calls = []
        self.queue = self.manager()

    def manager(self, dispatch=None):
        queue = self.module.SessionQueue(self.root, dispatch=dispatch or self.dispatch, poll_interval=.01)
        self.addCleanup(queue.close)
        return queue

    def dispatch(self, request):
        self.calls.append(request['request'])
        self.store.append(request['session_id'], 'user_message', {'text': request['request'],
            'mode': request['mode'], 'agent': request['agent'], 'offline': not request['online'],
            'queue_ticket': request['queue_ticket']})
        self.store.append(request['session_id'], 'completed', {'status': 'done'})
        return {'ok': True, 'result': {'status': 'done'}}

    def api(self, cmd, **values):
        return self.queue.handle({'cmd': cmd, 'session_id': self.sid, **values})

    def add(self, request, key=None):
        result = self.api('queue_add', request=request, queue_request_id=key or request.replace(' ', '-'))
        self.assertTrue(result['ok'], result)
        return result['ticket']

    def wait(self, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.01)
        self.fail('queue condition did not become true')

    def finish_current(self):
        self.store.append(self.sid, 'completed', {'status': 'done'})

    def test_busy_messages_wait_then_automatically_deliver_fifo(self):
        first = self.add('First')
        second = self.add('Second')
        self.assertEqual([], self.calls)
        self.assertEqual(2, self.api('queue_list')['queue']['counts']['queued'])
        self.finish_current()
        self.wait(lambda: len(self.calls) == 2)
        self.assertEqual(['First', 'Second'], self.calls)
        self.wait(lambda: self.api('queue_list')['queue']['counts']['delivered'] == 2)
        tickets = [e['payload']['queue_ticket'] for e in self.store.read(self.sid)
                   if e['type'] == 'user_message' and e['payload'].get('queue_ticket')]
        self.assertEqual([first['id'], second['id']], tickets)

    def test_duplicate_request_key_is_idempotent_and_different_body_is_rejected(self):
        first = self.add('First', 'stable-id')
        again = self.add('First', 'stable-id')
        self.assertEqual(first['id'], again['id'])
        rejected = self.api('queue_add', request='different', queue_request_id='stable-id')
        self.assertFalse(rejected['ok'])
        self.assertEqual(1, self.api('queue_list')['queue']['counts']['queued'])

    def test_reorder_requires_full_permutation_and_remove_only_pending(self):
        one, two, three = [self.add(text) for text in ('one', 'two', 'three')]
        self.assertFalse(self.api('queue_reorder', ordered_ids=[one['id'], one['id'], two['id']])['ok'])
        self.assertTrue(self.api('queue_remove', ticket_id=two['id'])['ok'])
        self.assertTrue(self.api('queue_reorder', ordered_ids=[three['id'], one['id']])['ok'])
        self.finish_current()
        self.wait(lambda: self.calls == ['three', 'one'])
        self.assertFalse(self.api('queue_remove', ticket_id=one['id'])['ok'])

    def test_pause_preserves_active_turn_and_requires_resume_for_remaining(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def blocked(request):
            self.calls.append(request['request'])
            self.store.append(self.sid, 'user_message', {'text': request['request'], 'mode': 'agent',
                'agent': 'research', 'offline': True, 'queue_ticket': request['queue_ticket']})
            if request['request'] == 'first':
                entered.set()
                release.wait(4)
            self.store.append(self.sid, 'completed', {'status': 'done'})
            return {'ok': True, 'result': {'status': 'done'}}

        self.queue.close()
        self.queue = self.manager(blocked)
        self.finish_current()
        self.add('first')
        self.assertTrue(entered.wait(2))
        self.add('second')
        self.assertTrue(self.api('queue_pause')['queue']['paused'])
        self.assertFalse((self.store.root / f'{self.sid}.cancel').exists())
        release.set()
        self.wait(lambda: self.api('queue_list')['queue']['counts']['delivered'] == 1)
        self.assertEqual(['first'], self.calls)
        self.assertTrue(self.api('queue_resume')['ok'])
        self.wait(lambda: self.calls == ['first', 'second'])

    def test_waiting_approval_and_children_never_start_another_turn(self):
        self.add('later')
        self.store.append(self.sid, 'permission_requested', {'tool_call_id': 'ask', 'tool': 'file.write'})
        self.wait(lambda: self.api('queue_list')['queue'].get('waiting_for') == 'waiting_approval')
        self.assertEqual([], self.calls)
        self.store.append(self.sid, 'permission_granted', {'tool_call_id': 'ask'})
        self.store.append(self.sid, 'driver_paused', {'status': 'waiting_children'})
        self.wait(lambda: self.api('queue_list')['queue'].get('waiting_for') == 'waiting_children')
        self.assertEqual([], self.calls)
        self.finish_current()
        self.wait(lambda: self.calls == ['later'])

    def test_cancel_pauses_remaining_work(self):
        self.add('later')
        self.store.append(self.sid, 'completed', {'status': 'cancelled'})
        self.wait(lambda: self.api('queue_list')['queue']['paused'])
        self.assertEqual([], self.calls)
        self.assertTrue(self.api('queue_list')['queue']['resume_required'])

    def test_disabled_feature_preserves_items_and_list_does_not_start_work(self):
        self.add('later')
        settings.save({'experimental_message_queue': False})
        self.finish_current()
        self.wait(lambda: self.api('queue_list')['queue']['paused'])
        result = self.api('queue_list')
        self.assertTrue(result['ok'])
        self.assertFalse(result['queue']['enabled'])
        self.assertFalse(self.api('queue_resume')['ok'])
        self.assertEqual([], self.calls)

    def test_closed_owner_preserves_queue_without_automatic_restart(self):
        self.add('saved')
        self.queue.close()
        self.queue = self.manager()
        result = self.api('queue_list')
        self.assertTrue(result['queue']['paused'])
        self.assertTrue(result['queue']['resume_required'])
        self.finish_current()
        self.add('new request')
        self.assertEqual([], self.calls)
        self.assertTrue(self.api('queue_resume')['ok'])
        self.wait(lambda: self.calls == ['saved', 'new request'])

    def test_list_before_thread_start_does_not_misclassify_live_owner_as_crashed(self):
        observer = self.manager()
        start = threading.Thread.start
        observed = []

        def before_start(thread):
            if thread.name.startswith('scizhi-queue-'):
                observed.append(observer.handle({'cmd': 'queue_list', 'session_id': self.sid}))
            return start(thread)

        with mock.patch.object(threading.Thread, 'start', new=before_start):
            self.add('starting')
        self.assertTrue(observed)
        self.assertFalse(observed[0]['queue']['paused'])
        self.assertFalse(observed[0]['queue']['resume_required'])

    def test_limits_and_public_projection_are_safe(self):
        for index in range(20):
            self.add(f'item-{index}')
        self.assertFalse(self.api('queue_add', request='overflow', queue_request_id='overflow')['ok'])
        self.assertFalse(self.api('queue_add', request='x' * 32001)['ok'])
        self.assertFalse(self.queue.handle({'cmd': 'queue_add', 'session_id': '../escape', 'request': 'bad'})['ok'])
        public = json.dumps(self.api('queue_list'))
        self.assertNotIn('model_signature', public)
        self.assertNotIn('credential_fingerprint', public)

    def test_default_route_uses_saved_offline_mode_after_global_preference_changes(self):
        self.add('still offline')
        settings.save({'default_offline': False})
        self.finish_current()
        self.wait(lambda: self.calls == ['still offline'])
        event = next(e for e in reversed(self.store.read(self.sid)) if e['type'] == 'user_message')
        self.assertTrue(event['payload']['offline'])

    def test_add_during_idle_worker_exit_is_not_lost(self):
        idle, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.module._Lease.close
        held_once = []

        def close(lease):
            if (not held_once and lease.stream is not None
                    and Path(lease.stream.name) == self.queue._run_path(self.sid)
                    and threading.current_thread().name.startswith('scizhi-queue-')):
                held_once.append(True)
                idle.set()
                release.wait(3)
            return original(lease)

        self.finish_current()
        with mock.patch.object(self.module._Lease, 'close', new=close):
            self.add('first')
            self.assertTrue(idle.wait(2))
            self.add('second')
            release.set()
            self.wait(lambda: self.calls == ['first', 'second'])

    def test_queued_submission_does_not_wait_behind_manual_turn_lock(self):
        from src.session_lock import session_lock
        self.queue.close()
        observed, responded = [], threading.Event()

        def dispatch(req):
            result = session_workspace.handle(req)
            observed.append(result)
            responded.set()
            return result

        self.queue = self.manager(dispatch)
        self.finish_current()
        with session_lock(self.root / 'sessions' / f'{self.sid}.chat-turn'):
            self.add('later')
            self.assertTrue(responded.wait(1), 'queued submission must defer instead of blocking behind manual work')
            self.assertEqual('queue_busy', observed[0].get('queue_code'))
            self.assertFalse(any(e.get('payload', {}).get('queue_ticket') for e in self.store.read(self.sid)))
            self.api('queue_pause')

    def test_cancel_then_manual_completion_between_polls_still_pauses_queue(self):
        from src.agent_runtime import AgentRuntime
        waiting, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def held_wait(timeout=None):
            waiting.set()
            release.wait(3)
            return False

        with mock.patch.object(self.queue._wake, 'wait', side_effect=held_wait):
            self.add('must stay paused')
            self.assertTrue(waiting.wait(2))
            runtime = AgentRuntime(session_root=self.store.root, offline=True)
            runtime.cancel_session(self.sid)
            self.store.append(self.sid, 'completed', {'status': 'cancelled'})
            self.store.append(self.sid, 'user_message', {'text': 'manually repaired', 'mode': 'agent',
                                                       'agent': 'research', 'offline': True})
            self.finish_current()
            try:
                self.assertTrue(self.api('queue_list')['queue']['paused'])
                self.assertEqual([], self.calls)
            finally:
                release.set()

    def test_cancel_after_claim_before_acceptance_never_starts_the_old_claim(self):
        from src.agent_runtime import AgentRuntime
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def dispatch(req):
            entered.set()
            release.wait(3)
            return session_workspace.handle(req)

        self.queue.close()
        self.queue = self.manager(dispatch)
        self.finish_current()
        with mock.patch('src.bridge._runtime') as factory:
            ticket = self.add('late claim')
            self.assertTrue(entered.wait(2))
            AgentRuntime(session_root=self.store.root, offline=True).cancel_session(self.sid)
            release.set()
            self.wait(lambda: self.api('queue_list')['queue']['counts']['dispatching'] == 0)
            factory.assert_not_called()
        self.assertTrue(self.api('queue_list')['queue']['paused'])
        self.assertFalse(any(e.get('payload', {}).get('queue_ticket') == ticket['id'] for e in self.store.read(self.sid)))

    def test_real_chat_turn_keeps_runtime_approval_and_logs_ticket_once(self):
        from src.agent_runtime import AgentRuntime
        self.queue.close()
        self.queue = self.manager(session_workspace.handle)
        self.finish_current()
        runtime = AgentRuntime(session_root=self.store.root, offline=True)
        writer = mock.Mock(return_value={'ok': True})
        runtime._register('test.write', 'write', 'Write fixture', writer)
        plan = runtime._plan_payload('agent', 'write', [{'tool': 'test.write', 'arguments': {}}], 'research')
        with mock.patch('src.bridge._runtime', return_value=runtime), mock.patch.object(runtime, 'plan', return_value=plan):
            first, second = self.add('first'), self.add('second')
            self.wait(lambda: self.api('queue_list')['queue']['counts']['delivered'] == 1)
            writer.assert_not_called()
            pending = runtime.sessions.snapshot(self.sid)['pending']
            runtime.approve(self.sid, pending['id'], 'allow')
            self.wait(lambda: self.api('queue_list')['queue']['counts']['delivered'] == 2)
            self.assertEqual(1, writer.call_count)
            runtime.cancel_session(self.sid)
        tickets = [e['payload']['queue_ticket'] for e in self.store.read(self.sid)
                   if e['type'] == 'user_message' and e['payload'].get('queue_ticket')]
        self.assertEqual([first['id'], second['id']], tickets)

    def test_adaptive_followup_records_ticket_only_in_accepted_user_message(self):
        from src import llm
        from src.agent_runtime import AgentRuntime
        from tests.test_adaptive_driver import response
        runtime = AgentRuntime(session_root=self.store.root, offline=False, llm_enabled=True)
        self.queue.close()
        self.queue = self.manager(session_workspace.handle)
        with mock.patch.object(llm, 'available', return_value=True), \
                mock.patch.object(llm, 'chat', return_value=response('Mock model answer')), \
                mock.patch('src.bridge._runtime', return_value=runtime):
            self.sid = runtime.start('Existing adaptive discussion')['session_id']
            ticket = self.add('Follow up with evidence')
            self.wait(lambda: self.api('queue_list')['queue']['counts']['delivered'] == 1
                      and self.store.snapshot(self.sid)['status'] == 'done')
        messages = [e for e in self.store.read(self.sid) if e['type'] == 'user_message']
        self.assertNotIn('queue_ticket', messages[0]['payload'])
        self.assertEqual(ticket['id'], messages[1]['payload']['queue_ticket'])
        self.assertEqual(2, len(messages))

    def test_real_attachment_change_requires_discard_not_implicit_resume(self):
        from src import project_workspace
        project = self.root / 'project'
        project.mkdir()
        note = project / 'notes.md'
        note.write_text('Original evidence', encoding='utf-8')
        pid = project_workspace.handle({'cmd': 'project_open', 'path': str(project)})['project']['id']
        self.sid = session_workspace.handle({'cmd': 'chat_new', 'project_id': pid})['session_id']
        self.assertTrue(session_workspace.handle({'cmd': 'chat_attach', 'session_id': self.sid, 'path': 'notes.md'})['ok'])
        self.queue.close()
        self.queue = self.manager(session_workspace.handle)
        self.api('queue_pause')
        ticket = self.api('queue_add', request='Summarize evidence', mode='plan', queue_request_id='materials')['ticket']
        note.write_text('Changed evidence', encoding='utf-8')
        with mock.patch('src.bridge._runtime') as factory:
            self.assertTrue(self.api('queue_resume')['ok'])
            self.wait(lambda: self.api('queue_list')['queue']['counts']['needs_review'] == 1)
            factory.assert_not_called()
        self.assertFalse(self.api('queue_resume')['ok'])
        self.assertFalse(self.api('queue_remove', ticket_id=ticket['id'])['ok'])
        self.assertTrue(self.api('queue_discard', ticket_id=ticket['id'])['ok'])
        self.assertFalse(any(e['type'] == 'user_message' for e in self.store.read(self.sid)))

    def test_real_model_profile_change_is_not_silently_adopted(self):
        from src.agent_driver import AdaptiveDriver
        self.queue.close()
        self.queue = self.manager(session_workspace.handle)
        self.store.append(self.sid, 'user_message', {'text': 'Online turn', 'mode': 'agent', 'agent': 'research', 'offline': False})
        self.api('queue_pause')
        identity = {'model': 'original'}
        with mock.patch.object(AdaptiveDriver, '_identity', side_effect=lambda: dict(identity)), \
                mock.patch('src.bridge._runtime') as factory:
            self.add('same topic')
            identity['model'] = 'changed'
            self.finish_current()
            self.api('queue_resume')
            self.wait(lambda: self.api('queue_list')['queue']['counts']['needs_review'] == 1)
            factory.assert_not_called()
        self.assertFalse(self.api('queue_resume')['ok'])

    def test_public_history_is_bounded_but_old_idempotent_ticket_survives(self):
        self.api('queue_pause')
        first = None
        for index in range(61):
            ticket = self.add(f'item-{index}', f'key-{index}')
            first = first or ticket
            self.api('queue_remove', ticket_id=ticket['id'])
        self.add('active')
        result = self.api('queue_list')['queue']
        self.assertEqual(51, len(result['items']))
        self.assertEqual(62, result['total_items'])
        self.assertTrue(result['history_truncated'])
        retry = self.api('queue_add', request='item-0', queue_request_id='key-0')
        self.assertTrue(retry['ok'], retry)
        self.assertEqual(first['id'], retry['ticket']['id'])
        self.assertEqual('removed', retry['ticket']['state'])

    def test_two_processes_cannot_deliver_one_ticket_twice(self):
        self.api('queue_pause')
        tickets = [self.add(f'item-{index}')['id'] for index in range(4)]
        self.finish_current()
        ctx = multiprocessing.get_context('spawn')
        processes = [ctx.Process(target=_process_dispatch, args=(str(self.root), self.sid, 4)) for _ in range(2)]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(16)
                self.assertEqual(0, process.exitcode)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(3)
        self.assertEqual(tickets, (self.root / 'effects.jsonl').read_text(encoding='utf-8').splitlines())

    def test_crashed_dispatch_is_reviewed_and_never_replayed(self):
        self.api('queue_pause')
        ticket = self.add('uncertain effect')
        self.finish_current()
        ctx = multiprocessing.get_context('spawn')
        entered = ctx.Event()
        process = ctx.Process(target=_process_dispatch, args=(str(self.root), self.sid, 1, entered))
        process.start()
        try:
            self.assertTrue(entered.wait(6))
        finally:
            process.terminate()
            process.join(3)
        result = self.api('queue_list')['queue']
        self.assertTrue(result['paused'])
        self.assertEqual(1, result['counts']['needs_review'])
        self.assertFalse(self.api('queue_resume')['ok'])
        self.assertEqual([ticket['id']], (self.root / 'effects.jsonl').read_text(encoding='utf-8').splitlines())

    def test_corrupt_optional_queue_never_prevents_cancelling_running_session(self):
        from src.agent_runtime import AgentRuntime
        runtime = AgentRuntime(session_root=self.store.root, offline=True)
        path = self.queue._path(self.sid)
        valid = self.queue._load(self.sid)
        invalid = [[], {**valid, 'audit': None}, {**valid, 'items': [None]},
                   {**valid, 'revision': 'invalid'}, {**valid, 'cancel_epoch': []}]
        for state in invalid:
            with self.subTest(state=state):
                body = json.dumps(state).encode('utf-8')
                path.write_bytes(body)
                result = runtime.cancel_session(self.sid)
                self.assertIn(result['status'], {'cancelling', 'cancelled'})
                self.assertTrue((self.store.root / f'{self.sid}.cancel').is_file())
                self.assertEqual(path.read_bytes(), body)
                self.assertTrue(any(event['type'] == 'cancel_requested' for event in self.store.read(self.sid)))

    def test_closing_corrupt_owned_queue_releases_lifetime_lease(self):
        self.add('pending')
        self.api('queue_pause')
        self.wait(lambda: not self.queue._workers)
        self.queue._path(self.sid).write_bytes(b'[]')
        self.queue.close()
        self.assertIsNone(self.queue._owner.stream)
        self.assertEqual(self.queue._path(self.sid).read_bytes(), b'[]')
