"""Native project files, Git, planning and handoff controls for the research desk."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import flet as ft

from ..bridge import handle
from .theme import palette, style_inputs
from .code_preview import CodePreview


SECTIONS = {"files": "项目文件", "git": "Git版本", "planning": "任务计划", "handoff": "研究交接"}
TODO_STATUS = {"todo": "待办", "doing": "进行中", "done": "已完成", "blocked": "待解决"}
RUN_STATUS = {"ready": "等待运行", "running": "运行中", "waiting_human": "等待确认",
              "paused": "已暂停", "completed": "已完成", "failed": "失败", "cancelled": "已取消",
              "missed": "已错过", "uncertain": "待检查", "interrupted": "已中断", "disabled": "未启用",
              "done": "已完成", "pending": "排队中", "todo": "待办", "doing": "进行中", "blocked": "待解决",
              "error": "失败", "waiting_approval": "等待授权", "claiming": "准备运行", "scheduled": "等待运行"}


class ProjectStudio:
    def __init__(self, page, on_project_changed: Callable | None = None,
                 on_session_open: Callable | None = None, on_attach: Callable | None = None):
        self.page = page
        self.on_project_changed, self.on_session_open, self.on_attach = on_project_changed, on_session_open, on_attach
        # FilePicker 必须注册为 page service（CODELY.md 硬约定）：未注册时
        # 目录选择对话框不弹出，"打开文件夹 / 新建项目"按钮表现为假死
        self.folder_picker = ft.FilePicker()
        page.services.append(self.folder_picker)
        self.project_id = ""
        self.project: dict[str, Any] = {}
        self.projects: list[dict[str, Any]] = []
        self.section, self.directory, self.preview_path = "files", "", ""
        self.width, self.epoch, self.busy = 700, 0, False
        self.todo_edit_id = self.schedule_edit_id = ""
        self._git_refresh_pending = False
        self.git_remotes: dict[str, str] = {}
        self.status = self.text("打开文件夹，建立当前研究项目。")
        self.project_path = self.text("")
        self.project_path.max_lines = 1
        self.project_path.overflow = ft.TextOverflow.ELLIPSIS
        self.project_picker = ft.Dropdown(label="项目", options=[], expand=True,
                                           on_select=lambda e: self.set_project(self.project_picker.value or ""))
        self.folder_button = ft.TextButton("打开文件夹", icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                                           on_click=lambda e: self.page.run_task(self.pick_folder))
        self.create_button = ft.TextButton("新建项目", icon=ft.Icons.ADD,
                                           on_click=lambda e: self.page.run_task(self.pick_parent))
        self.layout = ft.Dropdown(label="布局", width=135, value="split", options=[
            ft.dropdown.Option("chat", "对话"), ft.dropdown.Option("files", "文件"),
            ft.dropdown.Option("split", "分栏")], on_select=self.change_layout,
            tooltip="分栏布局在宽屏并排显示目录与预览；窄屏自动上下排列。")
        self.view = ft.Dropdown(label="工作区", value="files", width=self.width - 12, visible=False,
                               options=[ft.dropdown.Option(k, v) for k, v in SECTIONS.items()],
                               on_select=lambda e: self.select(self.view.value or "files"))
        self.tab_items = {}
        for name, label in SECTIONS.items():
            button = ft.TextButton(label, on_click=lambda e, key=name: self.select(key),
                style=ft.ButtonStyle(padding=ft.Padding(4, 10, 4, 10), color=palette.TEXT_2,
                                     shape=ft.RoundedRectangleBorder(radius=0)))
            self.tab_items[name] = ft.Container(button, padding=ft.Padding(0, 0, 0, 1))
        self.tab_bar = ft.Row(list(self.tab_items.values()), spacing=20, wrap=True, run_spacing=0)
        self._style_tabs()
        self.views = {name: ft.Column(visible=name == "files", spacing=12) for name in SECTIONS}
        self._build_files()
        self._build_git()
        self._build_planning()
        self._build_handoff()
        self.body = ft.Column(list(self.views.values()), expand=True, scroll=ft.ScrollMode.AUTO, spacing=0)
        self.project_toolbar = ft.Row([self.folder_button, self.create_button, self.project_path,
            ft.IconButton(ft.Icons.REFRESH, icon_size=18, tooltip="刷新项目", on_click=lambda e: self.refresh())],
            spacing=8, wrap=True, run_spacing=0)
        self.content = ft.Column([
            ft.Row([self.project_picker, self.layout], spacing=10),
            self.project_toolbar, self.tab_bar, self.view,
            ft.Divider(height=1, color=palette.BORDER), self.status, self.body,
        ], expand=True, spacing=8)
        self.resize(self.width)
        style_inputs(self.content)
        self._enable_project(False)

    @staticmethod
    def text(value: Any, *, strong=False, mono=False, max_lines=None):
        # max_lines 默认 None（预览等长内容不受限）；调用方可按需收紧。
        # 始终把 max_lines 作为显式 kwarg 传入，lint_ui 的长文本检查据此放行。
        return ft.Text(str(value), size=12, color=palette.TEXT if strong else palette.TEXT_2, selectable=True,
                       weight=ft.FontWeight.W_500 if strong else ft.FontWeight.W_400,
                       font_family="Consolas" if mono else None,
                       max_lines=max_lines,
                       overflow=ft.TextOverflow.ELLIPSIS if max_lines else None)

    @staticmethod
    def button(label, action, primary=False):
        return (ft.FilledButton if primary else ft.OutlinedButton)(label, on_click=lambda e: action())

    @staticmethod
    def _request(command: str, **args) -> dict:
        result = handle({"cmd": command, **args})
        if not isinstance(result, dict) or result.get("ok") is False:
            raise ValueError((result.get("error") or result.get("output") or "操作失败") if isinstance(result, dict) else "返回数据不完整")
        return result

    def note(self, message: str, error=False):
        self.status.value, self.status.color = message, palette.DANGER if error else palette.TEXT_2
        self.status.visible = bool(message)

    def _style_tabs(self):
        for name, item in self.tab_items.items():
            selected = name == self.section
            item.border = ft.Border(bottom=ft.BorderSide(2, palette.ACCENT if selected else "transparent"))
            item.content.style.color = palette.ACCENT if selected else palette.TEXT_2
            item.content.style.text_style = ft.TextStyle(size=13,
                weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_400)

    def _update(self):
        style_inputs(self.content)
        self.page.update()

    def _enable_project(self, enabled):
        self.layout.disabled = self.view.disabled = not enabled
        self.tab_bar.disabled = not enabled
        self.body.visible = enabled
        for panel in self.views.values():
            panel.disabled = not enabled

    def _background(self, message, operation, completed):
        if self.busy:
            self.note("上一项操作仍在进行，请稍候。")
            self._update()
            return
        self.busy = True
        epoch, project_id = self.epoch, self.project_id
        self.note(message)
        self._update()

        def work():
            try:
                result = operation()
                if self.epoch == epoch and self.project_id == project_id:
                    completed(result)
            except Exception as exc:
                if self.epoch == epoch and self.project_id == project_id:
                    self.note(str(exc), error=True)
            finally:
                self.busy = False
                self._update()
                if self._git_refresh_pending:
                    self._git_refresh_pending = False
                    if self.project_id and self.section == "git":
                        self.refresh_git()

        self.page.run_thread(work)

    def refresh(self):
        try:
            result = self._request("project_list")
            self.projects = result.get("projects", [])
            self.project_picker.options = [ft.dropdown.Option(str(p["id"]), p.get("name", p["id"])) for p in self.projects]
            active = self.project_id or result.get("active_project_id", "")
            project = next((p for p in self.projects if p["id"] == active), None)
            if not project:
                self.project_id, self.project = "", {}
                self.project_picker.value = None
                self.project_path.value = ""
                self._enable_project(False)
                self.note("打开文件夹，或新建一个研究项目。")
            else:
                changed = self.project != project
                self._adopt_project(project)
                self._refresh_section()
                if changed and self.on_project_changed:
                    self.on_project_changed(dict(self.project))
        except Exception as exc:
            self.note(str(exc), error=True)
        self._update()

    def _adopt_project(self, project):
        changed = self.project_id != project["id"]
        self.project_id, self.project = str(project["id"]), dict(project)
        self.project_picker.value = self.project_id
        full_path = project.get("path", "")
        self.project_path.value = Path(full_path).name or full_path
        self.project_path.tooltip = full_path
        self.layout.value = project.get("view", "split")
        self._enable_project(True)
        if changed:
            self.epoch += 1
            self.directory, self.preview_path = "", ""
            self.preview_title.value = "文件预览"
            self.preview.controls = [self.text("选择文件，查看研究材料或代码。")]
            self.attach_button.disabled = True
            self.git_output.value = self.git_diff.value = self.handoff_result.value = ""
            self.git_remotes = {}
            self.git_remote.options, self.git_remote.value = [], None
            self.git_branch.value = self.git_paths.value = self.commit_message.value = ""
            self.reset_todo(update=False)
            self.reset_schedule(update=False)
        self._layout_files()

    def set_project(self, project_id: str):
        if not project_id:
            return
        try:
            result = self._request("project_select", project_id=project_id)
            project = result.get("project") or next((p for p in self.projects if p["id"] == project_id), None)
            if project:
                self._adopt_project(project)
            else:
                self.project_id = project_id
            self.refresh()
            if self.on_project_changed:
                self.on_project_changed(dict(self.project))
        except Exception as exc:
            self.note(str(exc), error=True)
            self._update()

    def change_layout(self, _=None):
        try:
            result = self._request("project_update", project_id=self.project_id, view=self.layout.value)
            self.project = result.get("project", {**self.project, "view": self.layout.value})
            if self.on_project_changed:
                self.on_project_changed(dict(self.project))
            self.note("已保存项目布局。")
        except Exception as exc:
            self.layout.value = self.project.get("view", "split")
            self.note(str(exc), error=True)
        self._layout_files()
        self._update()

    async def pick_folder(self):
        try:
            path = await self.folder_picker.get_directory_path(dialog_title="选择研究项目文件夹")
            if path:
                result = self._request("project_open", path=path)
                if result.get("project"):
                    self._adopt_project(result["project"])
                self.refresh()
                if self.on_project_changed:
                    self.on_project_changed(dict(self.project))
        except Exception as exc:
            self.note(str(exc), error=True)
            self._update()

    async def pick_parent(self):
        try:
            parent = await self.folder_picker.get_directory_path(dialog_title="选择新项目的上级文件夹")
            if not parent:
                return
            name = ft.TextField(label="项目名称", autofocus=True, hint_text="例如：城市热岛文献综述")
            error = self.text("")

            def create():
                try:
                    result = self._request("project_create", parent=parent, name=(name.value or "").strip())
                    self.page.pop_dialog()
                    if result.get("project"):
                        self._adopt_project(result["project"])
                    self.refresh()
                    if self.on_project_changed:
                        self.on_project_changed(dict(self.project))
                except Exception as exc:
                    error.value, error.color = str(exc), palette.DANGER
                    self._update()

            dialog = ft.AlertDialog(title=self.text("新建研究项目", strong=True),
                                    content=ft.Column([self.text(parent), name, error], tight=True),
                                    actions=[self.button("取消", self.page.pop_dialog), self.button("创建", create, True)])
            style_inputs(dialog)
            self.page.show_dialog(dialog)
        except Exception as exc:
            self.note(str(exc), error=True)
            self._update()

    def select(self, section: str):
        self.section = section if section in SECTIONS else "files"
        self.view.value = self.section
        self._style_tabs()
        for name, view in self.views.items():
            view.visible = name == self.section
        if self.project_id:
            self._refresh_section()
        self._update()

    def _refresh_section(self):
        if self.section == "files":
            self.load_files(self.directory)
        elif self.section == "git":
            self.refresh_git()
        elif self.section == "planning":
            self.refresh_planning()
        else:
            self.note("将当前研究的任务、材料清单与进展导出为交接包。")

    def resize(self, width: int):
        self.width = max(260, width)
        self.status.width = self.width - 12
        self.project_path.width = max(120, self.width - 320)
        self.project_toolbar.width = self.tab_bar.width = self.width - 12
        self.view.width = self.width - 12
        for field in (self.git_output, self.git_diff, self.handoff_result):
            field.width = self.width - 24
        self._layout_files()
        self._layout_planning()

    def _build_files(self):
        self.breadcrumb = self.text("项目根目录", strong=True)
        self.up_button = self.button("上一级", self.go_up)
        self.files = ft.Column(spacing=2)
        self.preview_title = self.text("文件预览", strong=True)
        self.preview = ft.Column([self.text("选择文件，查看研究材料或代码。")], spacing=8)
        self.attach_button = self.button("加入对话", self.attach_preview)
        self.attach_button.disabled = True
        self.file_tree = ft.Column([ft.Row([self.up_button, self.breadcrumb], wrap=True), self.files], spacing=10)
        self.preview_pane = ft.Container(content=ft.Column([
            ft.Row([self.preview_title, self.attach_button], wrap=True), self.preview], spacing=10))
        self.file_layout = ft.Row([self.file_tree, self.preview_pane], wrap=True, spacing=16,
                                  run_spacing=16, vertical_alignment=ft.CrossAxisAlignment.START)
        self.views["files"].controls = [self.file_layout]
        self._layout_files()

    def _layout_files(self):
        """Keep the same controls mounted while changing their actual geometry."""
        available = max(240, self.width - 12)
        side_by_side = self.layout.value == "split" and self.width >= 850
        self.file_layout.width = available
        self.file_layout.wrap = not side_by_side
        self.file_tree.width = 240 if side_by_side else available
        self.files.width = self.file_tree.width
        self.preview_pane.width = available - 256 if side_by_side else available
        self.preview_pane.visible = self.layout.value != "files" or bool(self.preview_path)
        self.preview_pane.padding = ft.Padding(16, 0, 0, 0) if side_by_side else ft.Padding(0, 14, 0, 0)
        self.preview_pane.border = (ft.Border(left=ft.BorderSide(1, palette.BORDER)) if side_by_side
                                    else ft.Border(top=ft.BorderSide(1, palette.BORDER)))
        preview_width = max(200, self.preview_pane.width - (17 if side_by_side else 0))
        self.preview.width = preview_width
        self.preview_title.width = min(preview_width, max(150, preview_width - 130))
        self.breadcrumb.width = max(140, self.file_tree.width - 104)
        for control in self.preview.controls:
            control.width = preview_width
        for row in self.files.controls:
            if isinstance(row, ft.Row):
                row.width = self.file_tree.width
                for child in row.controls:
                    child.width = self.file_tree.width

    def load_files(self, directory=""):
        try:
            result = self._request("project_files", project_id=self.project_id, path=directory)
            self.directory = directory
            self.breadcrumb.value = directory or "项目根目录"
            self.up_button.disabled = not directory
            self.files.controls = []
            for entry in result.get("entries", []):
                is_dir = entry.get("kind") in {"directory", "dir", "folder"}
                path = entry["path"]
                action = (lambda p=path: self.load_files(p)) if is_dir else (lambda p=path: self.open_preview(p))
                button = ft.TextButton(entry["name"], icon=ft.Icons.FOLDER_OUTLINED if is_dir else ft.Icons.DESCRIPTION_OUTLINED,
                                       on_click=lambda e, fn=action: fn(), tooltip=path)
                self.files.controls.append(ft.Row([button], spacing=4))
            if not self.files.controls:
                self.files.controls = [self.text("此文件夹还没有文件。可将研究材料放入该目录后刷新。")]
            self.note(f"{len(result.get('entries', []))} 项 · 点击文件预览，点击文件夹进入。")
        except Exception as exc:
            self.note(str(exc), error=True)
        self._layout_files()
        self._update()

    def go_up(self):
        parent = str(PurePosixPath(self.directory.replace("\\", "/")).parent)
        self.load_files("" if parent == "." else parent)

    def open_preview(self, path):
        try:
            result = self._request("project_preview", project_id=self.project_id, path=path)
            kind, content = result.get("kind", "text"), result.get("content", "")
            self.preview_title.value = path
            self.preview_path = result.get("absolute_path") or result.get("image_path") or str(Path(self.project.get("path", "")) / path)
            self.attach_button.disabled = self.on_attach is None or kind in {"image", "binary", "unsupported"}
            if kind == "image" and result.get("image_path"):
                self.preview.controls = [ft.Image(src=result["image_path"], fit=ft.BoxFit.CONTAIN,
                                                  width=max(220, self.width - 28), height=380,
                                                  error_content=self.text("无法显示此图片。"))]
            elif kind in {"markdown", "md"}:
                self.preview.controls = [ft.Markdown(content, selectable=True,
                                                     extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                                                     auto_follow_links=False)]
            elif kind in {"binary", "unsupported"}:
                self.preview.controls = [self.text("此文件不支持文字预览。当前对话附件支持文本文件。")]
            elif kind == "pdf":
                head = f"PDF 全文提取（{result.get('pages') or 0} 页 · {result.get('method') or '未知方式'}）"
                if content:
                    self.preview.controls = [self.text(head + "；加入对话后将带入提取的全文。", strong=True),
                                             self.text(content, mono=True)]
                else:
                    self.preview.controls = [self.text(
                        head + f"；{result.get('pdf_error') or '未能提取到文本'}。"
                        "扫描件或加密 PDF 暂不支持加入对话。")]
            else:
                language = Path(path).suffix.lstrip('.') or 'text'
                self.preview.controls = [CodePreview(content, language=language)]
            self.note(("PDF 文本过长，已展示前段内容。" if kind == "pdf" else "文件过长，已展示前段内容。")
                      if result.get("truncated") else
                      "图片已打开；当前对话附件支持文本文件。" if kind == "image" else f"已打开 {path}")
        except Exception as exc:
            self.preview_path = ""
            self.attach_button.disabled = True
            self.note(str(exc), error=True)
        self._layout_files()
        self._update()

    def attach_preview(self):
        if self.preview_path and self.on_attach and not self.attach_button.disabled:
            self.on_attach(self.preview_path)
            self.note("已将文件加入对话输入。")
            self._update()

    def _build_git(self):
        self.git_output, self.git_diff = self.text("点击刷新查看版本状态。", mono=True), CodePreview('', language='diff')
        self.git_paths = ft.TextField(label="本次提交的相对路径", hint_text="每行一个文件，例如 src/main.py", multiline=True, min_lines=2, max_lines=5)
        self.commit_message = ft.TextField(label="提交说明", hint_text="例如：补充实验结果与讨论")
        self.git_remote = ft.Dropdown(label="远程仓库", options=[], width=210)
        self.git_branch = ft.TextField(label="推送分支", width=210)
        self.commit_button = self.button("提交所选文件", self.commit, True)
        self.push_button = self.button("推送到远程…", self.confirm_push)
        self.views["git"].controls = [ft.Row([self.button("刷新 Git 状态", self.refresh_git),
                                                           self.button("查看差异", self.load_diff)], wrap=True),
                                      self.git_output, self.git_paths, self.commit_message, self.commit_button,
                                      ft.Divider(height=1, color=palette.BORDER),
                                      ft.Row([self.git_remote, self.git_branch], wrap=True), self.push_button,
                                      self.text("变更差异", strong=True), self.git_diff]

    def refresh_git(self):
        if self.busy:
            self._git_refresh_pending = True
            return
        project_id = self.project_id
        self._background("正在读取 Git 状态…", lambda: self._request("project_git_status", project_id=project_id), self._render_git)

    def _render_git(self, result):
        is_repository = result.get("is_repository", True)
        self.commit_button.disabled = self.push_button.disabled = not is_repository
        self.git_branch.value = result.get("branch", "")
        remotes = result.get("remotes", {})
        if isinstance(remotes, dict):
            self.git_remotes = {str(k): str(v) for k, v in remotes.items()}
        else:
            self.git_remotes = {str(r.get("name", "origin")): str(r.get("push_url") or r.get("url", "")) for r in remotes if isinstance(r, dict)}
        self.git_remote.options = [ft.dropdown.Option(name, name) for name in self.git_remotes]
        if self.git_remote.value not in self.git_remotes:
            self.git_remote.value = next(iter(self.git_remotes), None)
        status = result.get("status", "")
        if isinstance(status, list):
            status = "\n".join(f"{r.get('status', '')}  {r.get('path', '')}" if isinstance(r, dict) else str(r) for r in status)
        elif isinstance(status, dict):
            status = json.dumps(status, ensure_ascii=False, indent=2)
        self.git_output.value = f"分支：{result.get('branch') or '尚无提交'}\n{status or '工作目录干净'}"
        if not is_repository:
            self.git_output.value = "此项目尚未建立 Git 仓库。"
        self.note("选择相对路径后提交；推送前会显示目标仓库与分支。" if is_repository else "在项目目录初始化 Git 后刷新即可管理版本。")

    def load_diff(self):
        paths = self._selected_paths()
        args = {"project_id": self.project_id}
        if len(paths) == 1:
            args["path"] = paths[0]

        def done(result):
            self.git_diff.value = result.get("diff", result.get("content", "")) or "没有可显示的差异。"
            self.note("差异内容过长，已显示前段内容。" if result.get("truncated") else "已读取变更差异。")
        self._background("正在读取差异…", lambda: self._request("project_git_diff", **args), done)

    def _selected_paths(self):
        return list(dict.fromkeys(line.strip() for line in (self.git_paths.value or "").splitlines() if line.strip()))

    def commit(self):
        paths, message = self._selected_paths(), (self.commit_message.value or "").strip()
        if not paths or not message:
            self.note("请填写至少一个相对路径和提交说明。", error=True)
            self._update()
            return
        project_id = self.project_id

        def work():
            result = self._request("project_git_commit", project_id=project_id, paths=paths, message=message)
            return result, self._request("project_git_status", project_id=project_id)

        def done(results):
            self._render_git(results[1])
            self.commit_message.value = ""
            self.note("所选文件已提交。")
        self._background("正在提交所选文件…", work, done)

    def confirm_push(self):
        remote, branch = self.git_remote.value, (self.git_branch.value or "").strip()
        if not remote or not branch:
            self.note("请先读取 Git 状态并选择远程仓库和分支。", error=True)
            self._update()
            return
        project_id = self.project_id

        def push():
            self.page.pop_dialog()
            if self.project_id != project_id:
                self.note("当前项目已切换，请重新选择推送目标。", error=True)
                self._update()
                return
            self._background("正在推送…", lambda: self._request("project_git_push", project_id=project_id, remote=remote, branch=branch),
                             lambda result: self.note(result.get("message") or "已推送到远程仓库。"))

        self.page.show_dialog(ft.AlertDialog(title=self.text("确认推送", strong=True),
            content=ft.Column([self.text(self.project.get("name", "")),
                               self.text(f"远程：{remote}\n{self.git_remotes.get(remote, '')}"),
                               self.text(f"分支：{branch}"), self.text("将该分支的本地提交发送到此远程仓库。")], tight=True),
            actions=[self.button("取消", self.page.pop_dialog), self.button("确认推送", push, True)]))

    def _build_planning(self):
        self.progress_text = self.text("暂无待办", strong=True)
        self.progress = ft.ProgressBar(value=0, color=palette.ACCENT, bgcolor=palette.SURFACE_3, height=3)
        self.todos = ft.Column(spacing=0)
        self.todo_title = ft.TextField(label="待办事项")
        self.todo_status = ft.Dropdown(label="状态", value="todo", width=135,
                                       options=[ft.dropdown.Option(k, v) for k, v in TODO_STATUS.items()])
        self.todo_priority = ft.Dropdown(label="优先级", value="normal", width=135,
                                         options=[ft.dropdown.Option("low", "低"), ft.dropdown.Option("normal", "普通"), ft.dropdown.Option("high", "高")])
        self.todo_notes = ft.TextField(label="备注", multiline=True, min_lines=1, max_lines=3)
        self.todo_task = ft.TextField(label="关联任务 ID（可选）")
        self.todo_session = ft.TextField(label="关联会话 ID（可选）")
        self.todo_save_button = self.button("保存待办", self.save_todo, True)
        self.schedule_title = ft.TextField(label="计划名称")
        self.schedule_workflow = ft.Dropdown(label="工作流", value="research_pipeline", options=[
            ft.dropdown.Option("research_pipeline", "完整综述"), ft.dropdown.Option("literature_scan", "文献快筛"),
            ft.dropdown.Option("evidence_review", "证据审查")])
        self.schedule_topic = ft.TextField(label="研究课题", multiline=True, min_lines=2, max_lines=4)
        self.schedule_at = ft.TextField(label="首次运行时间（含时区）", hint_text="2026-09-20T09:00:00+08:00")
        self.schedule_interval = ft.TextField(label="间隔 / 秒（0 为单次）", value="0", width=220)
        self.schedule_runs = ft.TextField(label="最多运行次数", value="1", width=160)
        self.schedule_offline = ft.Switch(label="使用离线示例", value=True)
        self.schedule_enabled = ft.Switch(label="启用此计划", value=True)
        self.schedule_save_button = self.button("保存计划", self.save_schedule, True)
        self.schedules, self.notifications, self.linked_tasks = ft.Column(spacing=8), ft.Column(spacing=6), ft.Column(spacing=6)
        self.todo_form = ft.Column([
            self.todo_title, ft.Row([self.todo_status, self.todo_priority], wrap=True), self.todo_notes,
            self.todo_task, self.todo_session,
            ft.Row([self.todo_save_button, self.button("清空表单", self.reset_todo)], wrap=True)], spacing=12)
        self.schedule_form = ft.Column([
            self.schedule_title, self.schedule_workflow, self.schedule_topic, self.schedule_at,
            ft.Row([self.schedule_interval, self.schedule_runs], wrap=True),
            self.schedule_offline, self.schedule_enabled,
            ft.Row([self.schedule_save_button, self.button("新建计划", self.reset_schedule)], wrap=True)], spacing=12)
        self.todo_editor = self._form_tile("添加/编辑待办", self.todo_form)
        self.schedule_editor = self._form_tile("添加/编辑定时研究", self.schedule_form)
        self.views["planning"].controls = [self.progress_text, self.progress, self.todos, self.todo_editor,
            self.text("关联研究任务", strong=True), self.linked_tasks,
            ft.Divider(height=1, color=palette.BORDER), self.text("定时研究", strong=True),
            self.text("应用运行时执行计划；关闭后的未完成计划需检查后恢复。"), self.schedules,
            self.schedule_editor,
            ft.Divider(height=1, color=palette.BORDER),
            ft.Row([self.text("运行通知", strong=True), self.button("全部标记已读", lambda: self.read_notification(all_read=True))], wrap=True), self.notifications]
        self.reset_schedule(update=False)
        self._layout_planning()

    def _form_tile(self, title, form):
        return ft.ExpansionTile(title=self.text(title, strong=True), controls=[form], expanded=False,
            maintain_state=True, dense=True, min_tile_height=46,
            tile_padding=ft.Padding(10, 0, 10, 0), controls_padding=ft.Padding(10, 10, 10, 14),
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.START,
            collapsed_bgcolor=palette.SURFACE, text_color=palette.TEXT, collapsed_text_color=palette.TEXT,
            icon_color=palette.TEXT_2, collapsed_icon_color=palette.TEXT_2)

    def _layout_planning(self):
        available = max(240, self.width - 12)
        self.views["planning"].width = available
        self.todos.width = self.schedules.width = self.progress.width = available
        self.todo_editor.width = self.schedule_editor.width = available
        self.todo_form.width = self.schedule_form.width = available - 20
        for card in self.todos.controls:
            if isinstance(card, ft.Container):
                self._layout_todo_card(card)
        for card in self.schedules.controls:
            card.width = available

    def _layout_todo_card(self, card):
        card.width = max(240, self.width - 12)
        inner = card.width
        card.content.width = inner
        row = card.content.controls[0]
        row.width = inner
        title_row = row.controls[0]
        title_row.width = inner - 216 if self.width >= 600 else inner
        title_row.controls[1].width = max(130, title_row.width - 48)
        for child in card.content.controls[1:]:
            if isinstance(child, ft.Text):
                child.width = inner

    def refresh_planning(self):
        try:
            board = self._request("board_snapshot", project_id=self.project_id)
            todos = board.get("todos", [])
            counts = board.get("counts", {})
            done, total = counts.get("done", sum(t.get("status") == "done" for t in todos)), counts.get("total", len(todos))
            value = float(board.get("progress", done / total if total else 0))
            self.progress.value = min(1, max(0, value / 100 if value > 1 else value))
            self.progress_text.value = f"待办进展 · {done} / {total} 已完成"
            self.todos.controls = [self._todo_card(todo) for todo in todos] or [self.text("写下下一项研究工作。")]
            self.linked_tasks.controls = [self._task_card(task) for task in board.get("tasks", [])] or [self.text("在待办中填写任务 ID，将进度关联到此项目。")]
            self.schedules.controls = [self._schedule_card(s) for s in self._request("schedule_list", project_id=self.project_id).get("schedules", [])] or [self.text("尚未设置定时研究。")]
            notices = self._request("notifications_list", project_id=self.project_id).get("notifications", [])
            self.notifications.controls = [self._notification_card(n) for n in notices[:30]] or [self.text("暂无运行通知。")]
            self.note("")
        except Exception as exc:
            self.note(str(exc), error=True)
        self._layout_planning()
        self._update()

    def _todo_card(self, todo):
        identifier = todo["id"]
        checkbox = ft.Checkbox(value=todo.get("status") == "done", semantics_label=todo.get("title", ""),
                               on_change=lambda e: self.set_todo_status(todo, "done" if e.control.value else "todo"))
        title = self.text(todo.get("title", ""), strong=True)
        title.selectable = False
        title.size, title.max_lines, title.overflow = 13, 3, ft.TextOverflow.ELLIPSIS
        title_row = ft.Row([checkbox, title], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        status = self.text(TODO_STATUS.get(todo.get("status", "todo"), "待办"))
        status.width = 52
        actions = ft.Row([status, ft.TextButton("编辑", on_click=lambda e: self.edit_todo(todo)),
                          ft.TextButton("删除", on_click=lambda e: self.delete_todo(identifier))], spacing=2, tight=True)
        row = ft.Row([title_row, actions], wrap=True, spacing=8, run_spacing=0)
        details = [row]
        if todo.get("notes"):
            details.append(self.text(todo["notes"]))
        if todo.get("task_status"):
            details.append(self.text(f"关联任务 · {RUN_STATUS.get(todo['task_status'], '待检查')}"))
        if todo.get("session_id") and self.on_session_open:
            details.append(self.button("打开关联会话", lambda: self.on_session_open(todo["session_id"])))
        card = ft.Container(ft.Column(details, spacing=2), padding=ft.Padding(0, 5, 0, 7),
                            border=ft.Border(bottom=ft.BorderSide(1, palette.BORDER)))
        self._layout_todo_card(card)
        return card

    def _task_card(self, task):
        return self.text(f"{task.get('topic') or task.get('title') or task.get('id', '')} · {RUN_STATUS.get(task.get('status'), '待检查')}")

    def reset_todo(self, update=True):
        self.todo_edit_id = ""
        for field in (self.todo_title, self.todo_notes, self.todo_task, self.todo_session):
            field.value = ""
        self.todo_status.value, self.todo_priority.value = "todo", "normal"
        self.todo_save_button.content = "保存待办"
        self.todo_editor.expanded = update
        if update:
            self._update()

    def edit_todo(self, todo):
        self.todo_edit_id = todo["id"]
        for field, key in ((self.todo_title, "title"), (self.todo_notes, "notes"), (self.todo_task, "task_id"), (self.todo_session, "session_id")):
            field.value = todo.get(key, "")
        self.todo_status.value, self.todo_priority.value = todo.get("status", "todo"), todo.get("priority", "normal")
        self.todo_save_button.content = "更新待办"
        self.todo_editor.expanded = True
        self.note("正在编辑待办；修改后点击更新待办。")
        self._update()

    def save_todo(self):
        todo = {"project_id": self.project_id, "title": (self.todo_title.value or "").strip(),
                "status": self.todo_status.value, "priority": self.todo_priority.value,
                "notes": self.todo_notes.value or "", "task_id": (self.todo_task.value or "").strip(),
                "session_id": (self.todo_session.value or "").strip()}
        if self.todo_edit_id:
            todo["id"] = self.todo_edit_id
        try:
            self._request("todo_save", todo=todo)
            self.reset_todo(update=False)
            self.refresh_planning()
            self.note("已保存待办。")
        except Exception as exc:
            self.note(str(exc), error=True)
        self._update()

    def set_todo_status(self, todo, status):
        try:
            self._request("todo_save", todo={"id": todo["id"], "project_id": self.project_id, "status": status})
            self.refresh_planning()
        except Exception as exc:
            self.refresh_planning()
            self.note(str(exc), error=True)
            self._update()

    def delete_todo(self, identifier):
        try:
            self._request("todo_delete", id=identifier)
            if self.todo_edit_id == identifier:
                self.reset_todo(update=False)
            self.refresh_planning()
        except Exception as exc:
            self.note(str(exc), error=True)
            self._update()

    def reset_schedule(self, update=True):
        self.schedule_edit_id = ""
        self.schedule_title.value = self.schedule_topic.value = ""
        self.schedule_workflow.value = "research_pipeline"
        self.schedule_at.value = (datetime.now().astimezone() + timedelta(hours=1)).isoformat(timespec="minutes")
        self.schedule_interval.value, self.schedule_runs.value = "0", "1"
        self.schedule_offline.value = self.schedule_enabled.value = True
        self.schedule_save_button.content = "保存计划"
        self.schedule_editor.expanded = update
        if update:
            self._update()

    def edit_schedule(self, schedule):
        self.schedule_edit_id = schedule["id"]
        for field, key in ((self.schedule_title, "title"), (self.schedule_topic, "topic"), (self.schedule_at, "run_at"),
                           (self.schedule_interval, "interval_seconds"), (self.schedule_runs, "max_runs")):
            field.value = str(schedule.get(key, ""))
        self.schedule_workflow.value = schedule.get("workflow_id", "research_pipeline")
        self.schedule_offline.value, self.schedule_enabled.value = schedule.get("offline", True), schedule.get("enabled", True)
        self.schedule_save_button.content = "更新计划"
        self.schedule_editor.expanded = True
        self.note("正在编辑计划；修改后点击更新计划。")
        self._update()

    def save_schedule(self):
        try:
            at = datetime.fromisoformat((self.schedule_at.value or "").strip().replace("Z", "+00:00"))
            if at.tzinfo is None or at.utcoffset() is None:
                raise ValueError("首次运行时间需要包含时区，例如 +08:00。")
            interval, runs = int(self.schedule_interval.value or "0"), int(self.schedule_runs.value or "1")
            if interval != 0 and interval < 60:
                raise ValueError("间隔须为 0（单次）或至少 60 秒。")
            if not 1 <= runs <= 100:
                raise ValueError("最多运行次数须为 1–100。")
            if interval == 0 and runs != 1:
                raise ValueError("单次计划的最多运行次数须为 1。")
            schedule = {"project_id": self.project_id, "title": (self.schedule_title.value or "").strip(),
                        "workflow_id": self.schedule_workflow.value, "topic": (self.schedule_topic.value or "").strip(),
                        "run_at": at.isoformat(), "interval_seconds": interval, "max_runs": runs,
                        "offline": bool(self.schedule_offline.value), "enabled": bool(self.schedule_enabled.value)}
            if self.schedule_edit_id:
                schedule["id"] = self.schedule_edit_id
            self._request("schedule_save", schedule=schedule)
            self.reset_schedule(update=False)
            self.refresh_planning()
            self.note("已保存计划。")
        except Exception as exc:
            self.note(str(exc), error=True)
        self._update()

    def _schedule_card(self, schedule):
        identifier = schedule["id"]
        enabled = ft.Switch(label="启用", value=bool(schedule.get("enabled")),
                            on_change=lambda e: self.toggle_schedule(identifier, bool(e.control.value)))
        details = [self.text(schedule.get("title", "未命名计划"), strong=True),
                   self.text(f"{RUN_STATUS.get(schedule.get('status'), '等待运行')} · {schedule.get('run_at', '')}"),
                   self.text(f"已运行 {schedule.get('run_count', schedule.get('runs', 0))} / {schedule.get('max_runs', 1)} 次"),
                   ft.Row([enabled, self.button("编辑", lambda: self.edit_schedule(schedule)),
                           self.button("立即运行", lambda: self.run_schedule(identifier)),
                           self.button("删除", lambda: self.delete_schedule(identifier))], wrap=True)]
        if schedule.get("requires_resume"):
            details.append(self.button("检查后恢复计划", lambda: self.toggle_schedule(identifier, True)))
        session = schedule.get("session_id") or schedule.get("last_session_id")
        if session and self.on_session_open:
            details.append(self.button("打开运行会话", lambda: self.on_session_open(session)))
        if schedule.get("last_error"):
            details.append(self.text(schedule["last_error"]))
        return ft.Container(ft.Column(details, spacing=6), padding=10, bgcolor=palette.SURFACE_2, border_radius=6)

    def toggle_schedule(self, identifier, enabled):
        try:
            self._request("schedule_save", schedule={"id": identifier, "enabled": enabled})
            self.refresh_planning()
        except Exception as exc:
            self.refresh_planning()
            self.note(str(exc), error=True)
            self._update()

    def run_schedule(self, identifier):
        def done(result):
            self.refresh_planning()
            self.note("已发起计划运行。")
        self._background("正在发起计划运行…", lambda: self._request("schedule_run_now", id=identifier), done)

    def delete_schedule(self, identifier):
        try:
            self._request("schedule_delete", id=identifier)
            if self.schedule_edit_id == identifier:
                self.reset_schedule(update=False)
            self.refresh_planning()
        except Exception as exc:
            self.note(str(exc), error=True)
            self._update()

    def _notification_card(self, notice):
        title = notice.get("message") or notice.get("title", "运行通知")
        actions = [self.text(title)]
        if notice.get("body"):
            actions.append(self.text(notice["body"]))
        if not notice.get("read", notice.get("read_at", False)):
            actions.append(self.button("标记已读", lambda: self.read_notification(notice["id"])))
        return ft.Column(actions, spacing=4)

    def read_notification(self, identifier=None, all_read=False):
        try:
            args = {"all": True, "project_id": self.project_id} if all_read else {"id": identifier}
            self._request("notification_read", **args)
            self.refresh_planning()
        except Exception as exc:
            self.note(str(exc), error=True)
            self._update()

    def _build_handoff(self):
        self.handoff_title = ft.TextField(label="交接标题（可选）", hint_text="例如：实验阶段研究交接")
        self.handoff_result = self.text("导出后将在这里显示文件路径。", mono=True)
        self.export_button = self.button("导出研究交接包", self.export_handoff, True)
        self.views["handoff"].controls = [self.text("研究交接包", strong=True),
            self.text("汇总当前项目的研究进展、待办、运行记录与材料清单，便于下次继续研究。"),
            self.handoff_title, self.export_button, self.handoff_result]

    def export_handoff(self):
        project_id, title = self.project_id, (self.handoff_title.value or "").strip()

        def done(result):
            paths = result.get("paths") or [result.get("path") or result.get("markdown_path"), result.get("json_path")]
            paths = list(dict.fromkeys(str(path) for path in paths if path))
            if not paths:
                raise ValueError("交接包未返回保存路径。")
            self.handoff_result.value = "\n".join(paths)
            self.note("研究交接包已导出。")
        self._background("正在导出研究交接包…", lambda: self._request("research_handoff", project_id=project_id, title=title), done)
