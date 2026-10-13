"""Async desktop regressions: consume picker results and keep task identities."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import flet as ft

from src.ui import desktop
from tests import test_desktop_navigation as navigation
from tests.test_desktop_theme import walk


class DesktopAsyncTest(unittest.TestCase):
    def setUp(self):
        fixture = navigation.DesktopNavigationTest("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        self.ui, self.page = fixture.ui, fixture.page

    def run_scheduled_picker(self):
        call = self.page.run_task.call_args
        self.assertIsNotNone(call)
        asyncio.run(call.args[0](*call.args[1:], **call.kwargs))

    def test_idle_selected_agent_observes_changes_from_another_client(self):
        self.ui.agent_state['session_id'] = 'session-b'
        self.ui.agent_busy.visible = False
        self.ui.nav_index['i'] = 4
        with mock.patch.object(self.ui.composer, 'consume_events') as consume:
            self.fixture.poll_once()
        consume.assert_called_once()

    def test_observed_remote_session_state_updates_available_actions(self):
        self.ui.agent_state['session_id'] = 'session-b'
        self.ui.agent_busy.visible = False
        callback = self.ui.session_status.on_status
        callback({'session_id': 'session-b', 'status': 'waiting_approval', 'driver': 'adaptive'})
        self.assertFalse(self.ui.agent_cancel_btn.disabled)
        self.assertTrue(self.ui.agent_run_btn.disabled)
        callback({'session_id': 'session-b', 'status': 'done', 'driver': 'adaptive'})
        self.assertTrue(self.ui.agent_cancel_btn.disabled)
        self.assertFalse(self.ui.agent_run_btn.disabled)

    def test_operation_log_is_bounded_and_unchanged_page_keeps_its_controls(self):
        self.ui.agent_state['session_id'] = 'session-b'
        events = [{'type': 'user_message', 'timestamp': '', 'payload': {'text': f'问题 {i}'}}
                  for i in range(1000)]
        self.ui.render_events_log(events)
        self.assertEqual(200, len(self.ui.agent_log_col.controls))
        first = self.ui.agent_log_col.controls[0]
        self.assertEqual('问题 800', first.content.controls[1].controls[-1].value)
        self.ui.render_events_log(events)
        self.assertIs(first, self.ui.agent_log_col.controls[0])

    def test_long_session_history_scrolls_above_the_docked_input_and_execution_controls(self):
        controls = self.ui.agent_column.controls
        history = controls.index(self.ui.agent_activity)
        dock = next(control for control in controls
                    if any(child is self.ui.agent_request for child in walk(control)))
        self.assertGreater(controls.index(dock), history)
        self.assertTrue(any(child is self.ui.agent_run_btn for child in walk(dock)))
        self.assertTrue(any(child is self.ui.agent_cancel_btn for child in walk(dock)))
        self.assertFalse(any(child is self.ui.agent_request for child in walk(self.ui.agent_activity)))
        self.assertIsNone(self.ui.agent_column.scroll)
        self.assertEqual(ft.ScrollMode.AUTO, self.ui.agent_activity.scroll)
        self.assertTrue(self.ui.agent_activity.expand)
        self.assertIn(self.ui.composer.transcript, self.ui.agent_activity.controls)
        self.assertIn(self.ui.agent_session_label, list(walk(self.ui.agent_activity)))
        self.assertIn(self.ui.reasoning_control.content, self.ui.agent_options.controls)

    def test_busy_feedback_respects_environment_reduced_motion(self):
        self.ui.agent_busy.visible = True
        self.ui.feature_switches['ui_animations'].value = True
        self.page.update.reset_mock()

        async def stop_after_one_frame(_):
            self.ui.state['poll'] = False

        with mock.patch.dict('os.environ', {'GEZHI_REDUCED_MOTION': '1'}), \
                mock.patch.object(desktop.asyncio, 'sleep', side_effect=stop_after_one_frame):
            asyncio.run(self.ui.thinking_motion())
        self.assertTrue(all(stroke.animate_opacity is None for stroke in self.ui.thinking_strokes))
        self.page.update.assert_not_called()

    def test_poller_does_not_read_full_log_again_after_composer(self):
        self.ui.agent_state['session_id'] = 'session-b'
        self.ui.agent_busy.visible = False
        self.ui.nav_index['i'] = 4
        for sid in self.fixture.session_events:
            self.fixture.session_store.snapshot(sid)
        with mock.patch.object(self.ui.composer, 'consume_events') as consume, \
                mock.patch.object(self.fixture.session_store, 'read', wraps=self.fixture.session_store.read) as read:
            self.fixture.poll_once()
        consume.assert_called_once()
        read.assert_not_called()

    def test_visible_event_backlog_catches_up_without_waiting_for_idle_heartbeat(self):
        self.ui.agent_state['session_id'] = 'session-b'
        self.ui.agent_busy.visible = False
        self.ui.nav_index['i'] = 4
        self.ui.composer.has_more_events = True
        delays = []
        class EndPoll(Exception):
            pass
        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 2:
                raise EndPoll
        with mock.patch.object(self.ui.composer, 'consume_events'), \
                mock.patch.object(desktop.asyncio, 'sleep', side_effect=sleep):
            with self.assertRaises(EndPoll):
                asyncio.run(self.ui.poller())
        self.assertLessEqual(delays[1], 0.3)

    def finish_queued_agent_request(self, replacement):
        queued = []
        self.page.run_thread.side_effect = queued.append
        self.ui.agent_state['session_id'] = 'session-b'
        self.ui.agent_busy.visible = False
        self.ui.agent_request.value = '  提交 A  '
        self.ui.agent_go('agent')
        self.assertEqual(1, len(queued))
        if replacement is not None:
            self.ui.agent_request.value = replacement
        def backend(request):
            if request['cmd'] == 'chat_turn':
                self.assertEqual('提交 A', request['request'])
                return {'ok': True, 'result': {'session_id': 'session-b', 'status': 'done', 'content': '已完成 A'}}
            if request['cmd'] == 'chat_get':
                return {'ok': True, 'session': {'title': '当前研究', 'archived': False, 'attachments': []}}
            raise AssertionError(request)
        with mock.patch('src.session_workspace.handle', side_effect=backend) as handler:
            queued.pop()()
        self.assertTrue(any(call.args[0]['cmd'] == 'chat_turn' for call in handler.call_args_list))
        self.assertEqual('done', self.ui.agent_state['status'])
        return self.ui.agent_request.value

    def test_agent_completion_preserves_new_draft_typed_during_execution(self):
        self.assertEqual('继续编写 B', self.finish_queued_agent_request('继续编写 B'))

    def test_agent_completion_clears_only_the_unchanged_submitted_text(self):
        self.assertEqual('', self.finish_queued_agent_request(None))

    def test_queued_agent_turn_keeps_the_session_selected_when_submitted(self):
        queued = []
        self.page.run_thread.side_effect = queued.append
        self.ui.agent_state['session_id'] = 'session-b'
        self.ui.agent_busy.visible = False
        self.ui.agent_request.value = '发送给会话 B 的请求'
        self.ui.agent_go('run')
        work = queued.pop(0)
        self.ui.select_agent_session('session-a')
        with mock.patch('src.session_workspace.handle', return_value={'ok': True,
                'result': {'session_id': 'session-b', 'status': 'done', 'content': 'B 的迟到结果'}}) as backend:
            work()
        turns = [call.args[0] for call in backend.call_args_list if call.args[0]['cmd'] == 'chat_turn']
        self.assertEqual(1, len(turns))
        self.assertEqual('session-b', turns[0]['session_id'])
        self.assertEqual('发送给会话 B 的请求', turns[0]['request'])
        self.assertEqual('session-a', self.ui.agent_state['session_id'])
        self.assertNotEqual('B 的迟到结果', self.ui.agent_answer.value)

    def test_queued_new_chat_keeps_the_project_selected_when_submitted(self):
        queued = []
        self.page.run_thread.side_effect = queued.append
        self.ui.agent_state['session_id'] = None
        self.ui.agent_busy.visible = False
        self.ui.state['project_id'] = 'project-a'
        self.ui.agent_request.value = '项目 A 的新会话请求'
        self.ui.agent_go('run')
        work = queued.pop(0)
        self.ui.state['project_id'] = 'project-b'
        self.ui.select_agent_session('session-a')
        def response(request):
            if request['cmd'] == 'chat_new':
                return {'ok': True, 'session_id': 'new-in-project-a'}
            return {'ok': True, 'result': {'session_id': 'new-in-project-a', 'status': 'done'}}
        with mock.patch('src.session_workspace.handle', side_effect=response) as backend:
            work()
        requests = [call.args[0] for call in backend.call_args_list]
        self.assertEqual(['chat_new', 'chat_turn'], [request['cmd'] for request in requests])
        self.assertEqual('project-a', requests[0]['project_id'])
        self.assertEqual('new-in-project-a', requests[1]['session_id'])
        self.assertEqual('session-a', self.ui.agent_state['session_id'])

    def test_opening_history_refreshes_shared_status_with_its_saved_model(self):
        from tests.test_session_status import status
        assert hasattr(self.ui, 'session_status'), 'current-session status panel is not connected'
        self.ui.session_status.api = mock.Mock(return_value={'ok': True, 'status': status('session-b')})
        self.ui.select_agent_session('session-b')
        self.ui.session_status.api.assert_called_once_with({'cmd': 'session_status', 'session_id': 'session-b'})
        self.assertIn('saved-model', self.ui.session_status.summary.value)
        self.assertIn('session-b', self.ui.agent_session_label.value)
        self.assertTrue(any(control is self.ui.agent_request for control in walk(self.ui.agent_column)))
        self.assertIn(self.ui.session_status.content, list(walk(self.ui.session_details)))
        self.assertFalse(self.ui.session_details.expanded)

    def test_compact_keeps_new_draft_and_does_not_rebuild_transcript(self):
        from tests.test_session_status import status
        assert hasattr(self.ui, 'session_status'), 'current-session status panel is not connected'
        queued = []
        self.page.run_thread.side_effect = queued.append
        self.ui.agent_state['session_id'] = 'session-b'
        self.ui.agent_busy.visible = False
        self.ui.agent_request.value = '正在编写的新草稿'
        self.ui.session_status.api = mock.Mock(side_effect=[{'ok': True, 'status': status('session-b')},
            {'ok': True, 'status': status('session-b'), 'result': {'changed': False, 'message': '无需整理'}}])
        self.ui.session_status.refresh(force=True)
        queued.pop()()
        with mock.patch.object(self.ui.composer, 'refresh') as refresh:
            self.ui.session_status.compact_button.on_click(None)
            queued.pop()()
        refresh.assert_not_called()
        self.assertEqual('正在编写的新草稿', self.ui.agent_request.value)
        self.assertEqual('session-b', self.ui.agent_state['session_id'])

    def test_desktop_displays_the_shared_release_version(self):
        from src.version import APP_VERSION
        self.assertEqual(APP_VERSION, desktop.APP_VERSION)

    def test_new_empty_session_uses_current_model_configuration_for_its_first_turn(self):
        saved, online = mock.Mock(), mock.Mock()
        saved.sessions.snapshot.return_value = {'status': 'draft'}
        with mock.patch('src.bridge._session_runtime', return_value=saved), \
                mock.patch('src.bridge._runtime', return_value=online) as runtime, \
                mock.patch.object(desktop.llm, 'available', return_value=True):
            self.assertIs(online, self.ui.get_runtime('new-draft'))
        runtime.assert_called_once_with(offline=False)

    def test_started_offline_session_keeps_its_saved_route_after_model_setup(self):
        saved = mock.Mock()
        saved.offline = True
        saved.sessions.snapshot.return_value = {'status': 'done'}
        with mock.patch('src.bridge._session_runtime', return_value=saved), \
                mock.patch('src.bridge._runtime') as runtime, \
                mock.patch.object(desktop.llm, 'available', return_value=True):
            self.assertIs(saved, self.ui.get_runtime('saved-offline'))
        runtime.assert_not_called()

    def test_onboarding_model_button_opens_model_category_without_replacing_draft(self):
        assert hasattr(self.ui, 'onboarding'), 'first-run actions are not connected'
        self.ui.agent_request.value = '待完善的目标'
        self.ui.onboarding.settings_button.on_click(None)
        self.assertEqual(5, self.ui.nav_index['i'])
        self.assertEqual('models', self.ui.settings_center.section)
        self.assertEqual('待完善的目标', self.ui.agent_request.value)

    def test_plan_selection_labels_the_main_action_as_read_only(self):
        self.assertTrue(callable(self.ui.agent_mode.on_select))
        self.ui.agent_mode.value = 'plan'
        self.ui.agent_mode.on_select(None)
        self.assertEqual('生成只读计划', self.ui.agent_run_btn.content)
        self.assertIn('不执行工具', self.ui.agent_mode_help.value)

    def test_home_offline_demo_uses_explicit_command_and_preserves_online_default_and_draft(self):
        from tests.test_onboarding import readiness
        assert hasattr(self.ui, 'onboarding'), 'first-run actions are not connected'
        self.ui.onboarding.api = mock.Mock(side_effect=[{'ok': True, 'readiness': readiness()},
            {'ok': True, 'task_id': 'task-a', 'workflow_id': 'literature_scan', 'offline': True, 'notice': '合成样例'}])
        self.ui.onboarding.refresh()
        self.ui.home_offline.value = False
        self.ui.agent_request.value = '我的新草稿'
        queued = []
        self.page.run_thread.side_effect = queued.append
        self.ui.home_demo_button.on_click(None)
        self.ui.home_demo_button.on_click(None)
        self.assertEqual(1, len(queued))
        queued.pop()()
        self.ui.onboarding.api.assert_called_with({'cmd': 'product_demo'})
        self.assertFalse(self.ui.home_offline.value)
        self.assertEqual('我的新草稿', self.ui.agent_request.value)
        self.assertEqual('task-a', self.ui.state['task_id'])
        self.assertEqual(1, self.ui.nav_index['i'])

    def test_secondary_tools_are_collapsed_and_do_not_scroll_the_docked_input(self):
        assert hasattr(self.ui, 'agent_toolbox'), 'secondary controls need a compact disclosure'
        assert hasattr(self.ui, 'session_queue'), 'follow-up queue controls are not connected'
        self.assertNotIn(self.ui.agent_request, list(walk(self.ui.agent_activity)))
        self.assertIn(self.ui.agent_request, list(walk(self.ui.agent_composer)))
        self.assertIn(self.ui.agent_toolbox, self.ui.agent_activity.controls)
        self.assertFalse(self.ui.agent_toolbox.expanded)
        self.assertFalse(self.ui.agent_options.expanded)
        self.assertIn(self.ui.reasoning_control.content, self.ui.agent_options.controls)
        self.assertIn(self.ui.session_queue.toggle, self.ui.agent_options.controls)

    def test_evidence_comparison_opens_from_real_secondary_action_without_sending(self):
        from tests.test_evidence_brief_ui import brief
        assert hasattr(self.ui, 'evidence_panel'), 'evidence comparison is not connected'
        self.ui.state['task_id'] = 'task-a'
        self.ui.agent_request.value = '尚未发送的草稿'
        self.ui.evidence_panel.api = mock.Mock(return_value={'ok': True, 'brief': brief('task-a')})
        self.ui.evidence_button.on_click(None)
        self.ui.evidence_panel.api.assert_called_once_with({'cmd': 'evidence_brief', 'task_id': 'task-a'})
        self.assertEqual('尚未发送的草稿', self.ui.agent_request.value)

    def test_only_application_disconnect_stops_new_queue_dispatch(self):
        with mock.patch('src.session_queue.close') as close:
            self.ui.nav_to(0)
            self.ui.nav_to(4)
            close.assert_not_called()
            self.assertTrue(callable(self.page.on_disconnect))
            asyncio.run(self.page.on_disconnect(None))
            close.assert_called_once()

    def test_export_consumes_selected_directory_for_the_original_task(self):
        self.ui.open_task("task-a")
        destination = self.fixture.session_root.name
        with mock.patch.object(self.ui.dir_picker, "get_directory_path",
                               new=mock.AsyncMock(return_value=destination)), \
                mock.patch.object(desktop.service, "export_artifacts", return_value=[]) as export:
            self.ui.export_all(None)
            # Selecting another task while the native dialog is open must not
            # redirect the original export request to that task's artifacts.
            self.ui.open_task("task-b")
            self.run_scheduled_picker()
            export.assert_called_once_with("task-a", destination)

    def test_upload_consumes_selected_file_and_starts_analysis(self):
        source = Path(self.fixture.session_root.name) / "measurements.csv"
        source.write_text("x,y\n1,2\n", encoding="utf-8")
        upload = next(c for c in walk(self.ui.page_stats)
                      if isinstance(c, ft.FilledButton) and c.content == "上传实验数据")
        result = {"shape": (1, 2), "missing": {}, "outliers": {},
                  "correlations": [], "chart_advice": [], "charts": [], "describe": {}}
        with mock.patch.object(self.ui.file_picker, "pick_files", new=mock.AsyncMock(
                return_value=[SimpleNamespace(path=str(source))])), \
                mock.patch.object(desktop.stats_agent, "analyze", return_value=result) as analyze:
            upload.on_click(None)
            self.run_scheduled_picker()
            analyze.assert_called_once_with(source)
            summary = " ".join(str(getattr(c, "value", ""))
                               for c in self.ui.stats_area.controls)
            self.assertIn(source.name, summary)

    def test_late_graph_result_cannot_replace_the_selected_tasks_graph(self):
        queued = []
        self.page.run_thread.side_effect = queued.append
        graph_paths = {}
        for tid in ("task-a", "task-b"):
            path = Path(self.fixture.session_root.name) / f"{tid}.png"
            path.write_bytes(b"graph fixture")
            graph_paths[tid] = str(path)
            self.fixture.artifacts[tid]["pool"] = [
                {"id": tid, "title": f"Paper for {tid}", "authors": [], "year": 2026}]

        with mock.patch.object(desktop.service, "evidence_graph", side_effect=lambda tid, **kw: {
                "file": graph_paths[tid], "pool_total": 1,
                "nodes": 1, "edges": 0, "isolated": 1}):
            self.ui.open_task("task-a")
            self.ui.nav_to(2)
            self.ui.open_task("task-b")
            self.ui.nav_to(2)
            self.assertEqual(len(queued), 2)
            queued[1]()
            self.assertEqual(self.ui.net_image.src, graph_paths["task-b"])
            queued[0]()
            self.assertEqual(self.ui.state["task_id"], "task-b")
            self.assertEqual(self.ui.net_image.src, graph_paths["task-b"],
                             "An old task's graph worker must not mutate the current view")

    def test_statistics_table_rows_match_columns_and_include_statistic_names(self):
        table = self.ui.describe_table({"describe": {
            "height": {"count": 4, "mean": 1.5, "std": 0.5, "min": 1, "max": 2},
            "weight": {"count": 4, "mean": 3.5, "std": 0.5, "min": 3, "max": 4},
        }})
        self.assertIsNotNone(table)
        self.assertEqual(len(table.rows), 8)
        for row in table.rows:
            self.assertEqual(len(row.cells), len(table.columns),
                             "Flutter requires a DataCell for every DataColumn")
        self.assertEqual(table.rows[0].cells[0].content.value, "样本数")
        self.assertEqual(table.rows[1].cells[0].content.value, "均值")


if __name__ == "__main__":
    unittest.main()
