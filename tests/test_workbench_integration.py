"""Workbench UI and dispatcher integration, without network or external processes."""
from __future__ import annotations

import unittest
from unittest import mock

import flet as ft

from src import bridge, command_center, plugins
from src.ui.workbench_panel import WorkbenchPanel
from tests.test_desktop_theme import walk


class WorkbenchPanelIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.page = mock.MagicMock(name="Page")
        self.workers = []
        self.page.run_thread.side_effect = self.workers.append
        self.current = {"task_id": "task-a"}
        self.open_task, self.open_session = mock.Mock(), mock.Mock()
        self.panel = WorkbenchPanel(
            self.page, ft.Text("Research details"), self.open_task, self.open_session,
            lambda: self.current["task_id"], lambda: "研究课题", mock.Mock())

    def test_workflow_button_passes_selected_template_topic_and_offline_mode(self):
        self.panel.topic.value = "证据可靠性"
        self.panel.offline.value = False
        self.panel.render("workflows", {"workflows": [{
            "id": "literature_scan", "title": "文献快筛", "description": "检索与筛选",
            "stages": [{"id": "lit_filter", "name": "文献筛选"}],
        }]})
        button = next(c for c in walk(self.panel.body)
                      if isinstance(c, ft.OutlinedButton) and c.content == "使用此工作流")
        with mock.patch.object(bridge, "handle", return_value={"ok": True, "task_id": "new-task"}) as dispatch:
            button.on_click(None)
            self.workers.pop(0)()
            dispatch.assert_called_once_with({"cmd": "workflow_run", "workflow_id": "literature_scan",
                                               "topic": "证据可靠性", "offline": False})
            self.open_task.assert_called_once_with("new-task")

    def test_suggestion_searches_chinese_description_and_only_prepares_command(self):
        self.panel.command.value = '模型连接'
        with mock.patch.object(plugins, 'commands', return_value=[{
                'name': 'fixture-plugin', 'plugin': 'fixture', 'help': '配置模型连接材料'}]), \
                mock.patch.object(command_center, 'execute') as execute:
            self.panel.suggest()
            plugin_button = next((button for button in self.panel.suggestions.controls
                                  if isinstance(button, ft.TextButton) and '/fixture-plugin' in button.content.value), None)
            self.assertIsNotNone(plugin_button)
            plugin_button.on_click(None)
        self.assertEqual(self.panel.command.value, '/fixture-plugin ')
        self.assertEqual(self.workers, [])
        execute.assert_not_called()

    def test_plugin_suggestion_follows_enabled_catalog(self):
        self.panel.command.value = '/fixture'
        with mock.patch.object(plugins, 'commands', return_value=[{
                'name': 'fixture-plugin', 'plugin': 'fixture', 'help': '材料能力'}]):
            self.panel.suggest()
            buttons = [item for item in self.panel.suggestions.controls if isinstance(item, ft.TextButton)]
            self.assertEqual(len(buttons), 1)
            self.assertIn('/fixture-plugin', buttons[0].content.value)
            self.assertTrue(any(isinstance(item, ft.Text) and item.value == '技能与扩展'
                                for item in self.panel.suggestions.controls))
        with mock.patch.object(plugins, 'commands', return_value=[]):
            self.panel.suggest()
            self.assertEqual(self.panel.suggestions.controls, [])

    def test_adopted_command_with_tail_space_closes_suggestions(self):
        self.panel.command.value = '/model '
        self.panel.suggest()
        self.assertEqual(self.panel.suggestions.controls, [])

    def test_loop_button_passes_bounded_schedule_and_selected_workflow(self):
        self.panel.topic.value = "证据可靠性"
        self.panel.workflow.value = "evidence_review"
        self.panel.interval.value, self.panel.iterations.value = "45", "2"
        self.panel.offline.value = True
        with mock.patch.object(bridge, "handle", return_value={"ok": True, "loop": {}}) as dispatch:
            self.panel.start_loop()
            self.workers.pop(0)()
            dispatch.assert_called_once_with({"cmd": "loop_start", "workflow_id": "evidence_review",
                                               "topic": "证据可靠性", "interval_seconds": 45.0,
                                               "max_iterations": 2, "offline": True})

    def test_late_action_does_not_navigate_or_overwrite_a_different_panel(self):
        self.panel.section = "workflows"
        with mock.patch.object(bridge, "handle", return_value={"ok": True, "task_id": "new-task"}) as dispatch:
            self.panel.action({"cmd": "workflow_run", "workflow_id": "research_pipeline",
                               "topic": "Original request", "offline": True}, open_created=True)
            self.panel.select("commands")
            message = self.panel.status.value
            controls = list(self.panel.body.controls)
            self.workers.pop(0)()
            dispatch.assert_called_once()
            self.open_task.assert_not_called()
            self.assertEqual(self.panel.section, "commands")
            self.assertEqual(self.panel.status.value, message)
            self.assertEqual(self.panel.body.controls, controls)
            self.assertFalse(self.panel.running)

    def test_queued_command_keeps_the_task_selected_when_it_was_submitted(self):
        self.panel.select("commands")
        self.panel.command.value = "/pause"
        with mock.patch.object(command_center, "execute", return_value={"ok": True, "text": "Paused"}) as execute:
            self.panel.run_command()
            self.current["task_id"] = "task-b"
            self.workers.pop(0)()
            execute.assert_called_once_with("/pause", "task-a")

    def test_late_command_does_not_overwrite_a_different_panel(self):
        self.panel.select("commands")
        self.panel.command.value = "/open task-a"
        with mock.patch.object(command_center, "execute", return_value={
                "ok": True, "task_id": "task-a", "text": "Late old response"}) as execute:
            self.panel.run_command()
            self.panel.select("overview")
            output, status = self.panel.output.value, self.panel.status.value
            self.workers.pop(0)()
            execute.assert_called_once()
            self.open_task.assert_not_called()
            self.assertEqual(self.panel.section, "overview")
            self.assertEqual(self.panel.output.value, output)
            self.assertEqual(self.panel.status.value, status)
            self.assertFalse(self.panel.running)

    def test_busy_command_is_disabled_and_direct_submission_keeps_draft(self):
        self.panel.command_context = lambda: {'busy': True}
        self.panel.command.value = '/plan'
        self.panel.suggest()
        button = next(item for item in self.panel.suggestions.controls if isinstance(item, ft.TextButton))
        self.assertTrue(button.disabled)
        self.assertIn('仍在执行', button.tooltip)
        self.panel.command.value = '/plan 保留原始输入'
        with mock.patch.object(command_center, 'execute') as execute:
            self.panel.run_command()
        execute.assert_not_called()
        self.assertEqual([], self.workers)
        self.assertEqual('/plan 保留原始输入', self.panel.command.value)
        self.assertIn('仍在执行', self.panel.status.value)

    def test_control_command_runs_while_another_panel_command_is_pending(self):
        self.panel.select('commands')
        self.panel.command.value = '/agent 检查项目'
        with mock.patch.object(command_center, 'execute', return_value={'ok': True, 'text': '完成'}) as execute:
            self.panel.run_command()
            self.panel.command.value = '/cancel-agent saved-session'
            self.panel.run_command()
            self.assertEqual(2, len(self.workers))
            self.workers.pop(1)()
            execute.assert_called_once_with('/cancel-agent saved-session', 'task-a')
            self.assertTrue(self.panel.running, 'control completion must not clear the running primary command')
            self.workers.pop(0)()
        self.assertFalse(self.panel.running)

    def test_pending_panel_command_disables_new_primary_command_and_keeps_input(self):
        self.panel.command.value = '/agent 检查项目'
        self.panel.run_command()
        self.panel.command.value = '/plan 新请求'
        self.panel.suggest()
        with mock.patch.object(command_center, 'execute') as execute:
            self.panel.run_command()
        execute.assert_not_called()
        self.assertEqual(1, len(self.workers))
        self.assertEqual('/plan 新请求', self.panel.command.value)
        self.assertIn('仍在执行', self.panel.status.value)


if __name__ == "__main__":
    unittest.main()
