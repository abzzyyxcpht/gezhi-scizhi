"""Control-level tests; shell callbacks are simulated, not native GUI/process QA."""
import threading
import unittest
from unittest import mock

import flet as ft

from src.ui import brand as b
from src.ui.terminal_panel import OUTPUT_LINE_LIMIT, TerminalPanel


class TerminalStreamUITest(unittest.TestCase):
    def setUp(self):
        self.page = mock.MagicMock()
        self.jobs = []
        self.page.run_thread.side_effect = self.jobs.append
        self.ui = TerminalPanel(self.page, lambda: object(), lambda: 'session-a')

    def start(self, command='fixture command'):
        self.ui.input.value = command
        self.ui.execute()
        return self.jobs[-1]

    def values(self):
        return [line.value for line in self.ui.lines]

    @staticmethod
    def result(**changes):
        return {'ok': True, 'stdout': '', 'stderr': '', 'exit_code': 0,
                'cancelled': False, 'timed_out': False, 'capture_complete': True, **changes}

    def test_live_output_arrives_before_result_without_duplicate_preview(self):
        work = self.start()
        observations = []

        def shell(args):
            callback = args.get('__on_output')
            self.assertTrue(callable(callback), 'terminal must pass a live output callback')
            callback('stdout', 'early\nsecond\n')
            observations.append((self.values(), self.ui.running, self.ui.input.disabled))
            callback('stderr', 'diagnostic\n')
            return self.result(stdout='early\nsecond\n', stderr='diagnostic\n')

        with mock.patch('src.tools_workspace._shell_run', side_effect=shell):
            work()
        self.assertTrue(observations, 'output must be observable during shell execution')
        self.assertIn('early', observations[0][0])
        self.assertEqual((True, True), observations[0][1:])
        self.assertEqual(1, self.values().count('early'))
        self.assertEqual(1, self.values().count('diagnostic'))
        self.assertEqual(b.DANGER, next(line.color for line in self.ui.lines if line.value == 'diagnostic'))
        self.assertFalse(self.ui.running)

    def test_cancel_receipt_without_tree_exit_does_not_claim_stopped(self):
        work = self.start()
        with mock.patch('src.tools_workspace._shell_run', return_value=self.result(ok=False,cancelled=True,
                tree_empty=False,error='进程树尚未确认全部退出')):
            work()
        self.assertEqual('停止未确认',self.ui.status.value)

    def test_stop_waits_for_real_result_and_repeated_click_is_idempotent(self):
        work = self.start()
        observations = []

        def shell(args):
            event = args.get('__cancel_event')
            self.assertIsInstance(event, threading.Event)
            self.assertFalse(self.ui.stop_button.disabled)
            self.ui.stop_button.on_click(None)
            self.ui.stop_button.on_click(None)
            observations.append((event.is_set(), self.ui.running, self.ui.status.value,
                                 self.ui.input.disabled, self.ui.stop_button.disabled, self.values()))
            return {'ok': False, 'cancelled': True, 'capture_complete': True, 'exit_code': -1}

        with mock.patch('src.tools_workspace._shell_run', side_effect=shell):
            work()
        self.assertTrue(observations, 'stop must receive the command event')
        requested, running, status, disabled, stop_disabled, values = observations[0]
        self.assertTrue(requested and running and disabled and stop_disabled)
        self.assertIn('正在停止', status)
        self.assertNotIn('[已停止]', values)
        self.assertEqual('已停止', self.ui.status.value)
        self.assertFalse(self.ui.running)
        self.assertFalse(self.ui.input.disabled)
        self.assertTrue(self.ui.stop_button.disabled)

    def test_hiding_terminal_does_not_cancel(self):
        work = self.start()
        observations = []

        def shell(args):
            self.ui.toggle()
            self.ui.toggle()
            observations.append(args['__cancel_event'].is_set())
            return self.result()

        with mock.patch('src.tools_workspace._shell_run', side_effect=shell):
            work()
        self.assertEqual([False], observations)

    def test_close_cancels_and_ignores_late_callback_and_result(self):
        work = self.start()
        observations = []

        def shell(args):
            self.ui.close()
            before = (self.page.update.call_count, self.values())
            args['__on_output']('stdout', 'late output\n')
            observations.append((args['__cancel_event'].is_set(), before))
            return self.result(stdout='late final', output_refs={'stdout': {'output_id': 'late'}})

        with mock.patch('src.tools_workspace._shell_run', side_effect=shell):
            work()
        self.assertTrue(observations, 'close must be available during execution')
        cancelled, before = observations[0]
        self.assertTrue(cancelled)
        self.assertEqual(before, (self.page.update.call_count, self.values()))
        self.assertIsNone(self.ui.last_output_session)
        self.ui.toggle()
        self.start('after close')
        self.assertEqual(before[0], self.page.update.call_count)
        self.assertEqual(1, len(self.jobs))

    def test_delayed_callback_from_previous_run_cannot_mutate_current_run(self):
        callbacks = []
        work = self.start('first')

        def shell(args):
            callbacks.append(args.get('__on_output'))
            return self.result()

        with mock.patch('src.tools_workspace._shell_run', side_effect=shell):
            work()
        self.assertTrue(callable(callbacks[0]))
        self.start('second')
        before = (self.values(), self.page.update.call_count, self.ui.status.value)
        callbacks[0]('stdout', 'obsolete\n')
        self.assertEqual(before, (self.values(), self.page.update.call_count, self.ui.status.value))
        self.assertTrue(self.ui.running)

    def test_superseded_result_cannot_finish_new_run_or_replace_its_archive(self):
        work = self.start('first')

        def shell(args):
            # Simulate a superseded generation while its worker still owns a reply.
            self.ui.running = False
            self.start('second')
            return self.result(stdout='obsolete result', output_refs={'stdout': {'output_id': 'old'}})

        with mock.patch('src.tools_workspace._shell_run', side_effect=shell):
            work()
        self.assertTrue(self.ui.running)
        self.assertTrue(self.ui.input.disabled)
        self.assertNotIn('obsolete result', self.values())
        self.assertIsNone(self.ui.last_output_session)

    def test_partial_chunks_keep_streams_separate_and_lines_bounded(self):
        work = self.start()

        def shell(args):
            callback = args['__on_output']
            callback('stdout', 'hel')
            callback('stderr', 'warn')
            callback('stdout', 'lo\nnext\n')
            callback('stderr', 'ing\n')
            callback('stdout', ''.join(f'line {i}\n' for i in range(OUTPUT_LINE_LIMIT + 5)))
            return self.result()

        with mock.patch('src.tools_workspace._shell_run', side_effect=shell):
            work()
        self.assertEqual(OUTPUT_LINE_LIMIT, len(self.ui.lines))
        self.assertEqual('line 5', self.values()[0])
        self.assertEqual('line 204', self.values()[-1])
        self.assertNotIn('', self.values())

    def test_interleaved_unfinished_lines_are_combined_without_repeating_final(self):
        work = self.start()

        def shell(args):
            callback = args['__on_output']
            callback('stdout', 'hel')
            callback('stderr', 'warn')
            callback('stdout', 'lo\nnext\n')
            callback('stderr', 'ing\n')
            return self.result(stdout='hello\nnext\n', stderr='warning\n')

        with mock.patch('src.tools_workspace._shell_run', side_effect=shell):
            work()
        self.assertEqual(['$ fixture command', 'hello', 'warning', 'next'], self.values())

    def test_legacy_result_preview_and_long_line_remain_bounded(self):
        work = self.start()
        with mock.patch('src.tools_workspace._shell_run', return_value=self.result(stdout='x' * 100000)):
            work()
        self.assertLess(len(self.values()[-1]), 2000)
        self.assertIn('长行已折叠', self.values()[-1])
        self.assertFalse(self.ui.lines[-1].no_wrap)
        self.assertEqual(ft.CrossAxisAlignment.STRETCH, self.ui.output.horizontal_alignment)

    def test_command_echo_is_redacted_but_submitted_command_is_preserved(self):
        command = 'echo api_key=fixture_secret_12345'
        work = self.start(command)
        self.assertNotIn('fixture_secret_12345', '\n'.join(self.values()))
        with mock.patch('src.tools_workspace._shell_run', return_value=self.result()) as shell:
            work()
        self.assertEqual(command, shell.call_args.args[0]['command'])

    def test_incomplete_capture_remains_readable_and_clearly_marked(self):
        work = self.start()
        with mock.patch('src.tools_workspace._shell_run', return_value={
                'ok': False, 'capture_complete': False, 'stdout': 'partial',
                'output_refs': {'stdout': {'output_id': 'partial'}}}):
            work()
        self.assertIn('已保存部分输出', '\n'.join(self.values()))
        self.assertTrue(self.ui.output_button.visible)
        self.assertEqual('session-a', self.ui.last_output_session)

    def test_timeout_and_nonzero_exit_are_distinct_failures(self):
        for reply, expected in [({'ok': False, 'timed_out': True}, '已超时'),
                                ({'ok': True, 'exit_code': 7}, '退出码 7')]:
            with self.subTest(reply=reply):
                work = self.start()
                with mock.patch('src.tools_workspace._shell_run', return_value=reply):
                    work()
                self.assertEqual(expected, self.ui.status.value)
                self.assertEqual(b.DANGER, self.ui.status.color)

    def test_normal_page_update_errors_are_not_silently_swallowed(self):
        work = self.start()
        self.page.update.side_effect = RuntimeError('ordinary render failure')
        with mock.patch('src.tools_workspace._shell_run', return_value=self.result()):
            with self.assertRaisesRegex(RuntimeError, 'ordinary render failure'):
                work()

    def test_confirmed_close_during_update_suppresses_only_shutdown_error(self):
        work = self.start()

        def shutdown_during_update():
            self.ui.close()
            raise RuntimeError('page was closed')

        self.page.update.side_effect = shutdown_during_update
        with mock.patch('src.tools_workspace._shell_run', return_value=self.result()):
            work()
        self.assertTrue(self.ui.closed)

    def test_close_before_worker_starts_prevents_command_launch(self):
        work = self.start()
        self.ui.close()
        with mock.patch('src.tools_workspace._shell_run') as shell:
            work()
        shell.assert_not_called()

    def test_success_with_incomplete_archive_does_not_claim_command_failure(self):
        work = self.start()
        with mock.patch('src.tools_workspace._shell_run', return_value=self.result(
                capture_complete=False, output_refs={'stdout': {'output_id': 'partial'}})):
            work()
        self.assertEqual('已完成 · 部分输出', self.ui.status.value)
        self.assertEqual('已保存输出', self.ui.output_button.content)

    def test_stop_racing_success_does_not_claim_a_completed_command_was_cancelled(self):
        work = self.start()
        self.ui.stop()
        with mock.patch('src.tools_workspace._shell_run', return_value=self.result()):
            work()
        self.assertEqual('已完成', self.ui.status.value)
        self.assertNotIn('[已停止]', self.values())

    def test_exception_restores_controls_and_redacts_its_message(self):
        work = self.start()
        with mock.patch('src.tools_workspace._shell_run', side_effect=ValueError('api_key=fixture_private')):
            work()
        self.assertEqual('执行失败', self.ui.status.value)
        self.assertFalse(self.ui.running)
        self.assertFalse(self.ui.input.disabled)
        self.assertTrue(self.ui.stop_button.disabled)
        self.assertNotIn('fixture_private', '\n'.join(self.values()))

    def test_callback_can_run_on_a_worker_thread(self):
        work = self.start()
        errors = []
        observations = []

        def shell(args):
            def emit():
                try:
                    args['__on_output']('stdout', 'worker output\n')
                    observations.append('worker output' in self.values())
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=emit)
            thread.start()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            return self.result(stdout='worker output\n')

        with mock.patch('src.tools_workspace._shell_run', side_effect=shell):
            work()
        self.assertEqual([], errors)
        self.assertEqual([True], observations)


if __name__ == '__main__':
    unittest.main()
