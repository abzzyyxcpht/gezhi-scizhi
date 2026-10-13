"""Exercise actual native control callbacks against a deterministic bridge fixture."""
from __future__ import annotations

import asyncio
import copy
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import flet as ft

from src.ui import project_studio as ui


def controls(root):
    yield root
    for attr in ("controls", "content", "actions"):
        value = getattr(root, attr, None)
        if isinstance(value, (list, tuple)):
            for item in value:
                yield from controls(item)
        elif isinstance(value, ft.Control):
            yield from controls(value)


class BridgeFixture:
    def __init__(self):
        self.calls = []
        self.projects = [{"id": "project1", "name": "课题一", "path": str(Path.cwd()), "view": "split"},
                         {"id": "project2", "name": "课题二", "path": str(Path.cwd() / "other"), "view": "files"}]
        self.active = "project1"
        self.todos = [{"id": "todo1", "project_id": "project1", "title": "核对实验数据", "status": "todo", "priority": "high"}]
        self.schedules = [{"id": "schedule1", "project_id": "project1", "title": "每日检索", "topic": "城市热岛",
                           "workflow_id": "literature_scan", "run_at": "2026-09-20T09:00:00+08:00",
                           "interval_seconds": 86400, "max_runs": 3, "run_count": 1,
                           "enabled": False, "offline": True, "status": "interrupted", "requires_resume": True}]

    def __call__(self, request):
        self.calls.append(copy.deepcopy(request))
        cmd = request["cmd"]
        result = {"ok": True}
        if cmd == "project_list":
            result.update(projects=self.projects, active_project_id=self.active)
        elif cmd in {"project_select", "project_update", "project_open", "project_create"}:
            self.active = request.get("project_id", "project1")
            project = next(p for p in self.projects if p["id"] == self.active)
            if "view" in request:
                project["view"] = request["view"]
            result["project"] = project
        elif cmd == "project_files":
            result["entries"] = [{"name": "results", "path": "results", "kind": "directory"},
                                  {"name": "notes.md", "path": "notes.md", "kind": "file"}]
        elif cmd == "project_preview":
            result.update(kind="markdown", content="# 实验笔记", size=30, truncated=False)
        elif cmd == "project_git_status":
            result.update(is_repository=True, branch="main", remotes=[{"name": "origin", "fetch_url": "https://unit.invalid/repo", "push_url": "https://unit.invalid/repo"}],
                          status=[{"path": "notes.md", "status": " M", "index": " ", "worktree": "M"}])
        elif cmd == "project_git_diff":
            result["diff"] = "+new evidence"
        elif cmd == "board_snapshot":
            todos = [t for t in self.todos if t["project_id"] == request["project_id"]]
            done = sum(t["status"] == "done" for t in todos)
            result.update(todos=todos, counts={"done": done, "total": len(todos)}, progress=done / len(todos) if todos else 0, tasks=[])
        elif cmd == "todo_save":
            record = request["todo"]
            if record.get("id"):
                next(t for t in self.todos if t["id"] == record["id"]).update(record)
            else:
                self.todos.append({**record, "id": "new-todo"})
        elif cmd == "todo_delete":
            self.todos = [t for t in self.todos if t["id"] != request["id"]]
        elif cmd == "schedule_list":
            result["schedules"] = self.schedules
        elif cmd == "schedule_save":
            record = request["schedule"]
            if record.get("id"):
                next(s for s in self.schedules if s["id"] == record["id"]).update(record)
            else:
                self.schedules.append({**record, "id": "new-schedule"})
        elif cmd == "schedule_delete":
            self.schedules = [s for s in self.schedules if s["id"] != request["id"]]
        elif cmd == "notifications_list":
            result["notifications"] = [{"id": "notice1", "message": "计划已中断，请检查后恢复。", "read": False}]
        elif cmd == "research_handoff":
            result["path"] = str(Path.cwd() / "exports" / "handoff.zip")
        return copy.deepcopy(result)


class ProjectStudioTest(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeFixture()
        patch = mock.patch.object(ui, "handle", side_effect=self.bridge)
        patch.start()
        self.addCleanup(patch.stop)
        self.page = mock.MagicMock(name="Page")
        self.page.run_thread.side_effect = lambda fn: fn()
        self.changed, self.attached, self.opened = mock.Mock(), mock.Mock(), mock.Mock()
        self.panel = ui.ProjectStudio(self.page, self.changed, self.opened, self.attached)

    def request(self, command):
        return [r for r in self.bridge.calls if r["cmd"] == command][-1]

    def click_label(self, label, root=None):
        matches = [c for c in controls(root or self.panel.content) if isinstance(c, (ft.TextButton, ft.OutlinedButton, ft.FilledButton)) and c.content == label]
        self.assertTrue(matches, label)
        matches[0].on_click(None)

    def test_construct_all_views_without_io_and_switch_native_visibility(self):
        self.assertFalse(self.bridge.calls)
        self.assertIsInstance(self.panel.content, ft.Column)
        self.panel.refresh()
        self.assertEqual(self.panel.project_id, "project1")
        self.changed.assert_called_with(self.bridge.projects[0])
        for section in ui.SECTIONS:
            self.panel.view.value = section
            self.panel.view.on_select(None)
            self.assertEqual([k for k, v in self.panel.views.items() if v.visible], [section])

    def test_workspace_picker_does_not_consume_vertical_flex(self):
        self.assertFalse(self.panel.view.expand)
        self.assertFalse(self.panel.view.visible)
        self.panel.resize(816)
        self.assertEqual(self.panel.view.width, 804)
        self.assertTrue(self.panel.body.expand)

    def test_text_tabs_switch_content_and_toolbar_contains_short_project_path(self):
        self.panel.refresh()
        for section, label in ui.SECTIONS.items():
            self.click_label(label, self.panel.tab_bar)
            self.assertEqual(self.panel.section, section)
            self.assertEqual(self.panel.tab_items[section].border.bottom.color, ui.palette.ACCENT)
        self.assertNotIn(self.panel.project_path, self.panel.content.controls)
        self.assertIn(self.panel.project_path, self.panel.project_toolbar.controls)
        self.assertEqual(self.panel.project_path.tooltip, self.bridge.projects[0]["path"])
        self.assertEqual(self.panel.project_path.value, Path(self.bridge.projects[0]["path"]).name)

    def test_todo_uses_natural_checkbox_and_separate_bounded_title(self):
        self.panel.refresh()
        self.panel.select("planning")
        self.panel.resize(816)
        card = self.panel.todos.controls[0]
        checkbox = next(c for c in controls(card) if isinstance(c, ft.Checkbox))
        self.assertFalse(checkbox.expand)
        self.assertIsNone(checkbox.width)
        self.assertIsNone(checkbox.label)
        title = next(c for c in controls(card) if isinstance(c, ft.Text) and c.value == "核对实验数据")
        self.assertIsInstance(title.width, (int, float))
        self.assertIsNone(card.bgcolor)
        self.assertEqual(card.border.bottom.color, ui.palette.BORDER)
        for row in controls(self.panel.content):
            if isinstance(row, ft.Row) and row.wrap:
                self.assertFalse(any(child.expand for child in row.controls))
        self.panel.resize(390)
        self.assertIsNone(checkbox.width)
        self.assertLess(title.width, card.width)
        self.assertEqual(title.value, "核对实验数据")

    def test_planning_forms_start_collapsed_and_edit_expands_the_form(self):
        self.panel.refresh()
        self.panel.select("planning")
        self.assertIsInstance(self.panel.todo_editor, ft.ExpansionTile)
        self.assertIsInstance(self.panel.schedule_editor, ft.ExpansionTile)
        self.assertFalse(self.panel.todo_editor.expanded)
        self.assertFalse(self.panel.schedule_editor.expanded)
        self.assertIn(self.panel.todos, self.panel.views["planning"].controls)
        self.assertIn(self.panel.schedules, self.panel.views["planning"].controls)
        self.assertNotIn(self.panel.todo_title, self.panel.views["planning"].controls)
        self.assertNotIn(self.panel.schedule_title, self.panel.views["planning"].controls)
        self.click_label("编辑", self.panel.todos)
        self.assertTrue(self.panel.todo_editor.expanded)
        self.click_label("编辑", self.panel.schedules)
        self.assertTrue(self.panel.schedule_editor.expanded)

    def test_project_selection_and_layout_are_persisted(self):
        self.panel.refresh()
        self.panel.project_picker.value = "project2"
        self.panel.project_picker.on_select(None)
        self.assertEqual(self.request("project_select")["project_id"], "project2")
        self.assertEqual(self.panel.layout.value, "files")
        self.panel.layout.value = "chat"
        self.panel.layout.on_select(None)
        self.assertEqual(self.request("project_update"), {"cmd": "project_update", "project_id": "project2", "view": "chat"})
        self.assertEqual(self.changed.call_args.args[0]["view"], "chat")

    def test_file_layout_changes_geometry_and_reflows_preview_on_resize(self):
        self.panel.refresh()
        self.panel.resize(1000)
        self.assertFalse(self.panel.file_layout.wrap)
        self.assertEqual(self.panel.file_tree.width, 240)
        self.assertGreater(self.panel.preview_pane.width, 600)
        self.assertTrue(self.panel.preview_pane.visible)
        self.panel.layout.value = "files"
        self.panel.layout.on_select(None)
        self.assertTrue(self.panel.file_layout.wrap)
        self.assertEqual(self.panel.file_tree.width, self.panel.file_layout.width)
        self.assertFalse(self.panel.preview_pane.visible)
        self.click_label("notes.md")
        self.assertTrue(self.panel.preview_pane.visible)
        self.assertEqual(self.panel.preview_pane.width, self.panel.file_tree.width)
        self.panel.layout.value = "split"
        self.panel.layout.on_select(None)
        self.assertFalse(self.panel.file_layout.wrap)
        self.assertLess(self.panel.preview.width, 750)
        self.panel.resize(700)
        self.assertTrue(self.panel.file_layout.wrap)
        self.assertEqual(self.panel.file_tree.width, self.panel.preview_pane.width)
        self.assertEqual(self.panel.preview.controls[0].width, self.panel.preview.width)
        self.panel.resize(849)
        self.assertTrue(self.panel.file_layout.wrap)
        self.panel.resize(850)
        self.assertFalse(self.panel.file_layout.wrap)

    def test_image_preview_width_tracks_actual_preview_pane(self):
        self.panel.refresh()
        self.panel.resize(1000)
        with mock.patch.object(ui, "handle", return_value={"ok": True, "kind": "image", "image_path": "C:/research/chart.png"}):
            self.panel.open_preview("chart.png")
        image = self.panel.preview.controls[0]
        self.assertEqual(image.width, self.panel.preview.width)
        self.assertLess(image.width, self.panel.width - self.panel.file_tree.width)
        self.panel.resize(700)
        self.assertEqual(image.width, self.panel.preview.width)

    def test_file_navigation_preview_and_attachment_keep_cwd(self):
        before = os.getcwd()
        self.panel.refresh()
        self.click_label("results")
        self.assertEqual(self.request("project_files")["path"], "results")
        self.panel.up_button.on_click(None)
        self.assertEqual(self.request("project_files")["path"], "")
        self.click_label("notes.md")
        self.assertIsInstance(self.panel.preview.controls[0], ft.Markdown)
        self.panel.attach_button.on_click(None)
        self.attached.assert_called_with(str(Path(before) / "notes.md"))
        self.assertEqual(os.getcwd(), before)

    def test_image_preview_uses_backend_path_and_failed_preview_disables_attach(self):
        self.panel.refresh()
        with mock.patch.object(ui, "handle", return_value={"ok": True, "kind": "image", "image_path": "C:/research/figure.png"}):
            self.panel.open_preview("figure.png")
        self.assertIsInstance(self.panel.preview.controls[0], ft.Image)
        self.assertEqual(self.panel.preview.controls[0].src, "C:/research/figure.png")
        self.panel.attach_button.on_click(None)
        self.attached.assert_not_called()
        with mock.patch.object(ui, "handle", return_value={"ok": False, "error": "文件不存在"}):
            self.panel.open_preview("gone.png")
        self.assertTrue(self.panel.attach_button.disabled)
        self.assertEqual(self.panel.status.value, "文件不存在")

    def test_git_commits_only_explicit_paths_and_push_requires_dialog_click(self):
        self.panel.refresh()
        self.panel.select("git")
        self.panel.git_paths.value = "notes.md\nresults/data.csv\nnotes.md\n"
        self.panel.commit_message.value = "补充数据"
        self.panel.commit_button.on_click(None)
        self.assertEqual(self.request("project_git_commit")["paths"], ["notes.md", "results/data.csv"])
        self.panel.push_button.on_click(None)
        self.assertFalse(any(r["cmd"] == "project_git_push" for r in self.bridge.calls))
        dialog = self.page.show_dialog.call_args.args[0]
        dialog_text = " ".join(c.value for c in controls(dialog) if isinstance(c, ft.Text))
        self.assertIn("https://unit.invalid/repo", dialog_text)
        self.assertIn("main", dialog_text)
        self.click_label("确认推送", dialog)
        self.assertEqual(self.request("project_git_push"), {"cmd": "project_git_push", "project_id": "project1", "remote": "origin", "branch": "main"})

    def test_todo_create_edit_complete_delete_callbacks(self):
        self.panel.refresh()
        self.panel.select("planning")
        self.click_label("编辑", self.panel.todos)
        self.panel.todo_title.value = "核对三组实验"
        self.panel.todo_save_button.on_click(None)
        self.assertEqual(self.request("todo_save")["todo"]["id"], "todo1")
        checkbox = next(c for c in controls(self.panel.todos) if isinstance(c, ft.Checkbox))
        checkbox.on_change(SimpleNamespace(control=SimpleNamespace(value=True)))
        self.assertEqual(self.panel.progress.value, 1)
        self.click_label("删除", self.panel.todos)
        self.assertFalse(self.bridge.todos)
        self.panel.todo_title.value = "整理文献"
        self.panel.todo_save_button.on_click(None)
        self.assertEqual(self.bridge.todos[0]["title"], "整理文献")

    def test_schedule_validation_save_resume_run_delete_and_notifications(self):
        self.panel.refresh()
        self.panel.select("planning")
        self.panel.schedule_at.value = "2026-09-20T09:00"
        self.panel.schedule_save_button.on_click(None)
        self.assertFalse(any(r["cmd"] == "schedule_save" for r in self.bridge.calls))
        self.assertIn("时区", self.panel.status.value)
        self.panel.schedule_title.value = "每周快筛"
        self.panel.schedule_topic.value = "城市热岛"
        self.panel.schedule_at.value = "2026-09-20T09:00:00+08:00"
        self.panel.schedule_interval.value = "3600"
        self.panel.schedule_runs.value = "3"
        self.panel.schedule_save_button.on_click(None)
        request = self.request("schedule_save")["schedule"]
        self.assertEqual(request["interval_seconds"], 3600)
        self.assertTrue(request["run_at"].endswith("+08:00"))
        self.click_label("检查后恢复计划", self.panel.schedules)
        self.assertEqual(self.request("schedule_save")["schedule"], {"id": "schedule1", "enabled": True})
        self.click_label("立即运行", self.panel.schedules)
        self.assertEqual(self.request("schedule_run_now")["id"], "schedule1")
        self.click_label("删除", self.panel.schedules)
        self.assertEqual(self.request("schedule_delete")["id"], "schedule1")
        self.click_label("标记已读", self.panel.notifications)
        self.assertEqual(self.request("notification_read")["id"], "notice1")

    def test_export_reports_actual_backend_path(self):
        self.panel.refresh()
        self.panel.select("handoff")
        self.panel.export_button.on_click(None)
        expected_path = str(Path.cwd() / "exports" / "handoff.zip")
        self.assertEqual(self.panel.handoff_result.value, expected_path)
        self.page.run_thread.assert_called()

    def test_handoff_accepts_markdown_and_manifest_contract(self):
        self.panel.refresh()
        result = {"ok": True, "markdown_path": "C:/research/handoff.md", "json_path": "C:/research/handoff.json",
                  "paths": ["C:/research/handoff.md", "C:/research/handoff.json"]}
        with mock.patch.object(ui, "handle", return_value=result):
            self.panel.export_button.on_click(None)
        self.assertEqual(self.panel.handoff_result.value, "C:/research/handoff.md\nC:/research/handoff.json")

    def test_nonrepository_and_push_failures_are_visible(self):
        self.panel.refresh()
        self.panel._render_git({"ok": True, "is_repository": False})
        self.assertTrue(self.panel.commit_button.disabled)
        self.assertIn("尚未建立 Git", self.panel.git_output.value)
        self.panel.select("git")
        self.panel.push_button.on_click(None)
        dialog = self.page.show_dialog.call_args.args[0]
        with mock.patch.object(ui, "handle", return_value={"ok": False, "output": "remote rejected: stale branch"}):
            self.click_label("确认推送", dialog)
        self.assertIn("remote rejected", self.panel.status.value)

    def test_directory_picker_and_create_dialog_use_async_file_picker(self):
        with mock.patch.object(ft.FilePicker, "get_directory_path", new=mock.AsyncMock(return_value="C:/research")):
            asyncio.run(self.panel.pick_folder())
            self.assertEqual(self.request("project_open")["path"], "C:/research")
            asyncio.run(self.panel.pick_parent())
        dialog = self.page.show_dialog.call_args.args[0]
        name = next(c for c in controls(dialog) if isinstance(c, ft.TextField))
        name.value = "新实验"
        self.click_label("创建", dialog)
        self.assertEqual(self.request("project_create"), {"cmd": "project_create", "parent": "C:/research", "name": "新实验"})

    def test_background_results_cannot_replace_new_project_state(self):
        self.panel.refresh()
        jobs = []
        self.page.run_thread.side_effect = lambda fn: jobs.append(fn)
        self.panel.select("git")
        self.panel.set_project("project2")
        jobs[0]()
        self.assertEqual(self.panel.project_id, "project2")
        self.assertNotIn("notes.md", self.panel.git_output.value)
        self.assertEqual(len(jobs), 2)
        jobs[1]()
        self.assertEqual(self.request("project_git_status")["project_id"], "project2")


if __name__ == "__main__":
    unittest.main()
