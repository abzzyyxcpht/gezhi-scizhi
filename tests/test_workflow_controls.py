"""Workflow controls use isolated checkpoints and deterministic local actions."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src import service
from src.orchestrator import engine
from src.orchestrator.engine import TaskContext


class WorkflowControlsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workflow = {'nodes': [
            {'id': 'work', 'type': 'tool', 'action': 'control_fixture', 'next': None},
        ]}
        self.ctx = TaskContext('controls-fixture', 'fixture', {})
        self.ctx.status, self.ctx.pointer = 'paused', 'work'
        for patcher in (
            mock.patch.object(engine, 'CHECKPOINT_DIR', Path(directory.name)),
            mock.patch.object(service, 'CHECKPOINT_DIR', Path(directory.name)),
            mock.patch.object(engine, 'load_workflow', return_value=self.workflow),
            mock.patch.object(service, 'load_workflow', return_value=self.workflow),
            mock.patch.object(service, '_ensure_agents'),
            mock.patch.object(service, '_tasks', {self.ctx.task_id: self.ctx}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cancel_during_last_node_wins_over_success_and_keeps_artifact(self):
        def action(ctx, params):
            service.cancel(ctx.task_id)
            return {'saved': 'completed artifact'}

        with mock.patch.dict(engine.ACTIONS, {'control_fixture': action}):
            engine.run(self.ctx)
        self.assertEqual(self.ctx.status, 'cancelled')
        self.assertEqual(self.ctx.artifacts['work'], {'saved': 'completed artifact'})
        self.assertEqual(self.ctx.events[-1]['status'], 'cancelled')
        self.assertEqual(TaskContext.load(self.ctx.task_id).status, 'cancelled')

    def test_resume_during_running_node_does_not_execute_it_twice(self):
        entered, release = threading.Event(), threading.Event()
        calls, workers = [], []
        thread_type = threading.Thread

        def action(ctx, params):
            calls.append(threading.get_ident())
            entered.set()
            release.wait(5)
            return {'count': len(calls)}

        def thread_factory(*args, **kwargs):
            worker = thread_type(*args, **kwargs)
            workers.append(worker)
            return worker

        with mock.patch.dict(engine.ACTIONS, {'control_fixture': action}), \
                mock.patch.object(service.threading, 'Thread', side_effect=thread_factory):
            try:
                service.resume(self.ctx.task_id)
                self.assertTrue(entered.wait(3))
                service.resume(self.ctx.task_id)
                self.assertEqual(len(workers), 1)
                self.assertEqual(len(calls), 1)
                service.cancel(self.ctx.task_id)
                self.assertTrue(self.ctx.cancel_requested)
            finally:
                release.set()
                for worker in workers:
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
        self.assertEqual(self.ctx.status, 'cancelled')

    def test_duplicate_resume_before_worker_starts_keeps_one_worker(self):
        with mock.patch.object(service.threading, 'Thread') as thread:
            service.resume(self.ctx.task_id)
            service.resume(self.ctx.task_id)
        self.assertEqual(thread.call_count, 1)

    def test_resume_rejects_completed_and_cancelled_tasks(self):
        for status in ('done', 'cancelled'):
            with self.subTest(status=status), mock.patch.object(service.threading, 'Thread') as thread:
                self.ctx.status, self.ctx.pointer = status, None
                with self.assertRaises(ValueError):
                    service.resume(self.ctx.task_id)
                thread.assert_not_called()
                self.assertEqual(self.ctx.status, status)

    def test_restart_during_startup_cannot_reset_inflight_context(self):
        self.ctx.artifacts = {'earlier': {'saved': True}}
        with mock.patch.object(service.threading, 'Thread') as thread:
            service.resume(self.ctx.task_id)
            with self.assertRaises(ValueError):
                service.restart(self.ctx.task_id)
        self.assertEqual(thread.call_count, 1)
        self.assertEqual(self.ctx.artifacts, {'earlier': {'saved': True}})

    def test_thread_start_failure_does_not_strand_resume(self):
        with mock.patch.object(service.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = OSError('cannot start worker')
            with self.assertRaises(OSError):
                service.resume(self.ctx.task_id)
            thread.return_value.start.side_effect = None
            service.resume(self.ctx.task_id)
        self.assertEqual(thread.call_count, 2)

    def test_failed_approval_worker_start_can_retry_without_skipping_work(self):
        self.workflow['nodes'].insert(0, {'id': 'review', 'type': 'human_approval', 'next': 'work'})
        self.ctx.status, self.ctx.pointer = 'waiting_human', 'review'
        action = mock.Mock(return_value={'saved': True})
        with mock.patch.dict(engine.ACTIONS, {'control_fixture': action}), \
                mock.patch.object(service.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = OSError('cannot start worker')
            with self.assertRaises(OSError):
                service.resume(self.ctx.task_id)
            self.assertEqual(self.ctx.pointer, 'review')
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            service.resume(self.ctx.task_id)
        action.assert_called_once()
        self.assertEqual(self.ctx.status, 'done')

    def test_approval_never_skips_a_non_approval_node(self):
        self.ctx.status = 'waiting_human'
        with mock.patch.object(service.threading, 'Thread') as thread:
            with self.assertRaises(ValueError):
                service.resume(self.ctx.task_id)
        thread.assert_not_called()
        self.assertEqual(self.ctx.pointer, 'work')

    def test_approval_at_workflow_end_does_not_restart_completed_nodes(self):
        self.workflow['nodes'][0]['next'] = 'review'
        self.workflow['nodes'].append({'id': 'review', 'type': 'human_approval', 'next': None})
        self.ctx.status, self.ctx.pointer = 'waiting_human', 'review'
        self.ctx.artifacts = {'work': {'saved': True}}
        action = mock.Mock(return_value={'saved': False})
        with mock.patch.dict(engine.ACTIONS, {'control_fixture': action}), \
                mock.patch.object(service.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            service.resume(self.ctx.task_id)
        self.assertEqual(self.ctx.status, 'done')
        self.assertEqual(self.ctx.artifacts, {'work': {'saved': True}})
        action.assert_not_called()

    def test_reloaded_checkpoint_and_finished_worker_allow_explicit_restart(self):
        self.ctx.save()
        self.ctx = TaskContext.load(self.ctx.task_id)
        service._tasks[self.ctx.task_id] = self.ctx
        action = mock.Mock(return_value={'saved': True})
        with mock.patch.dict(engine.ACTIONS, {'control_fixture': action}), \
                mock.patch.object(service.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            service.resume(self.ctx.task_id)
            self.assertEqual(self.ctx.status, 'done')
            service.restart(self.ctx.task_id)
        self.assertEqual(self.ctx.status, 'done')
        self.assertEqual(action.call_count, 2)

    def test_cancel_in_cooperative_node_is_not_downgraded_to_pause(self):
        def action(ctx, params):
            ctx.artifacts['work'] = {'saved': 'partial'}
            service.cancel(ctx.task_id)
            raise engine.PauseRequested()

        with mock.patch.dict(engine.ACTIONS, {'control_fixture': action}):
            engine.run(self.ctx)
        self.assertEqual(self.ctx.status, 'cancelled')
        self.assertEqual(self.ctx.pointer, 'work')
        self.assertEqual(self.ctx.artifacts['work'], {'saved': 'partial'})
        self.assertEqual(TaskContext.load(self.ctx.task_id).status, 'cancelled')

    def test_pending_checkpoint_restored_after_start_failure_runs_first_node(self):
        with mock.patch.object(service.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = OSError('cannot start worker')
            with self.assertRaises(OSError):
                service.create_task('Recovery fixture', workflow='fixture', offline=True)
        task_id = next(sid for sid in service._tasks if sid != self.ctx.task_id)
        saved = TaskContext.load(task_id)
        self.assertEqual(saved.status, 'pending')
        self.assertIsNone(saved.pointer)
        self.assertIsNone(saved.started_at)
        service._tasks.clear()
        service.restore_tasks()
        restored = service.get(task_id)
        self.assertEqual(restored.status, 'paused')
        action = mock.Mock(return_value={'saved': True})
        with mock.patch.dict(engine.ACTIONS, {'control_fixture': action}), \
                mock.patch.object(service.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            service.resume(task_id)
        action.assert_called_once()
        self.assertEqual(restored.status, 'done')
        self.assertEqual(restored.artifacts['work'], {'saved': True})

    def test_restored_end_cursor_does_not_repeat_completed_work(self):
        self.ctx.status, self.ctx.pointer = 'running', None
        self.ctx.started_at = 123.0
        self.ctx.artifacts = {'work': {'saved': True}}
        self.ctx.save()
        service._tasks.clear()
        service.restore_tasks()
        restored = service.get(self.ctx.task_id)
        action = mock.Mock(return_value={'saved': False})
        with mock.patch.dict(engine.ACTIONS, {'control_fixture': action}), \
                mock.patch.object(service.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            service.resume(self.ctx.task_id)
        action.assert_not_called()
        self.assertEqual(restored.status, 'done')
        self.assertEqual(restored.artifacts['work'], {'saved': True})


if __name__ == '__main__':
    unittest.main()
