import unittest
from unittest import mock

from src.ui.terminal_panel import TerminalPanel


class TerminalOutputScopeTest(unittest.TestCase):
    def test_delayed_execution_uses_submitted_session_and_runtime(self):
        page = mock.MagicMock()
        jobs = []
        page.run_thread.side_effect = jobs.append
        selected = ['a']
        runtime_a, runtime_b = object(), object()
        runtime = [runtime_a]
        ui = TerminalPanel(page, lambda: runtime[0], lambda: selected[0])
        ui.input.value = 'fixture command'
        ui.execute()
        selected[0], runtime[0] = 'b', runtime_b
        with mock.patch('src.tools_workspace._shell_run', return_value={'ok': True, 'stdout': 'result'}) as run:
            jobs[0]()
        self.assertEqual('a', run.call_args.args[0]['__session_id'])
        self.assertIs(runtime_a, run.call_args.args[0]['__runtime'])

    def test_full_output_button_opens_the_original_output_session(self):
        page = mock.MagicMock()
        jobs = []
        page.run_thread.side_effect = jobs.append
        ui = TerminalPanel(page, lambda: object(), lambda: 'a')
        ui.input.value = 'fixture command'
        ui.execute()
        with mock.patch('src.tools_workspace._shell_run', return_value={
                'ok': True,'truncated':True,'stdout':'short','output_refs': {'stdout':{'output_id':'out_'+'a'*32}}}):
            jobs[0]()
        with mock.patch('src.ui.tool_outputs.ToolOutputsDialog') as dialog:
            ui.output_button.on_click(None)
        self.assertEqual('a',dialog.call_args.args[1])
        dialog.return_value.open.assert_called_once()

    def test_open_output_dialog_keeps_its_session_after_next_command_finishes(self):
        page = mock.MagicMock()
        jobs = []
        page.run_thread.side_effect = jobs.append
        selected = ['a']
        ui = TerminalPanel(page, lambda: object(), lambda: selected[0])
        ui.input.value = 'first'
        ui.execute()
        with mock.patch('src.tools_workspace._shell_run', return_value={
                'ok': True, 'output_refs': {'stdout': {'output_id': 'first'}}}):
            jobs[0]()
        with mock.patch('src.ui.tool_outputs.ToolOutputsDialog') as dialog:
            ui.open_outputs()
        bound_session = dialog.call_args.args[2]
        selected[0] = 'b'
        ui.input.value = 'second'
        ui.execute()
        with mock.patch('src.tools_workspace._shell_run', return_value={
                'ok': True, 'output_refs': {'stdout': {'output_id': 'second'}}}):
            jobs[1]()
        self.assertEqual('b', ui.last_output_session)
        self.assertEqual('a', bound_session())
