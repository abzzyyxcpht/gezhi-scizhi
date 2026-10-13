"""Right-hand workbench tools. Views consume the same commands as the TUI."""
from __future__ import annotations

import re
import threading
from typing import Callable

import flet as ft

from .. import settings
from .theme import palette, style_inputs

SECTIONS = {"overview": "研究详情", "tasks": "任务进程", "workflows": "科研工作流",
            "loops": "Loop", "sessions": "会话与导出", "remote": "手机远控", "commands": "命令"}
STATUS = {"running": "运行中", "pending": "排队中", "done": "已完成", "completed": "已完成",
          "waiting_human": "等待文献确认", "waiting_approval": "等待工具确认", "paused": "已暂停",
          "scheduled": "等待下轮", "stopped": "已停止", "interrupted": "等待手动恢复",
          "failed": "失败", "error": "失败", "cancelled": "已取消", "planned": "计划就绪"}


class WorkbenchPanel:
    def __init__(self, page, overview, open_task: Callable, open_session: Callable,
                 current_task: Callable, topic: Callable, close: Callable, on_mode_changed=None, command_context=None):
        self.page, self.overview = page, overview
        self.open_task, self.open_session = open_task, open_session
        self.current_task, self.get_topic, self.close = current_task, topic, close
        self.on_mode_changed = on_mode_changed
        self.command_context = command_context or (lambda: {})
        self.section, self.epoch, self.running = "overview", 0, False
        self._operations, self._command_epoch = 0, 0
        self._operation_lock = threading.Lock()
        self.token = ""
        self.width = 320
        self.topic = ft.TextField(label="研究课题", multiline=True, min_lines=2, max_lines=3)
        self.offline = ft.Switch(label="使用离线示例", value=settings.load().get("default_offline", True))
        self.workflow = ft.Dropdown(label="工作流", value="research_pipeline", options=[
            ft.dropdown.Option("research_pipeline", "完整综述"),
            ft.dropdown.Option("literature_scan", "文献快筛"),
            ft.dropdown.Option("evidence_review", "证据审查")])
        self.interval = ft.TextField(label="间隔 / 秒", value="60", width=132)
        self.iterations = ft.TextField(label="最多轮数", value="3", width=132)
        self.port = ft.TextField(label="监听端口", value="8765", width=150)
        self.command = ft.TextField(label="工作台命令", hint_text="/help", on_submit=self.run_command)
        self.suggestions = ft.Column(spacing=2)
        self.command.on_change = lambda e: self.suggest()
        self.output = ft.Text("输入 /help 查看命令；支持任务、工作流、Loop 与会话管理。", size=11,
                              font_family="Consolas", color=palette.TEXT_2, selectable=True, width=280)
        self.status = ft.Text("", size=11, color=palette.TEXT_2, width=280, selectable=True)
        self.body = ft.Column(spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)
        self.picker = ft.Dropdown(value="overview", options=[ft.dropdown.Option(k, v) for k, v in SECTIONS.items()],
                                  dense=True, expand=True, on_select=lambda e: self.select(self.picker.value))
        self.content = ft.Column([
            ft.Row([self.picker,
                    ft.IconButton(ft.Icons.REFRESH, icon_size=17, tooltip="刷新面板", on_click=lambda e: self.refresh()),
                    ft.IconButton(ft.Icons.CLOSE, icon_size=17, tooltip="关闭面板", on_click=lambda e: self.close())], spacing=2),
            self.status, ft.Divider(color=palette.BORDER, height=1), self.body,
        ], spacing=12, expand=True)
        style_inputs(self.content)
        self.body.controls = [overview]

    def resize(self, width: int) -> None:
        self.width = max(250, width)
        self.topic.width = self.command.width = self.workflow.width = self.width - 28
        self.output.width = self.status.width = self.width - 28

    def select(self, section: str) -> None:
        self.section = section if section in SECTIONS else "overview"
        self.picker.value = self.section
        self.epoch += 1
        if not self.topic.value:
            self.topic.value = self.get_topic() or ""
        self.refresh()

    def note(self, text: str, error=False) -> None:
        self.status.value, self.status.color = text, palette.DANGER if error else palette.TEXT_2

    def _begin_operation(self):
        with self._operation_lock:
            self._operations += 1
            self.running = True

    def _finish_operation(self):
        with self._operation_lock:
            self._operations -= 1
            self.running = self._operations > 0

    def _command_context(self, text=''):
        context = dict(self.command_context())
        context['busy'] = self.running or context.get('busy') is True
        name, _, args = text.strip().lstrip('/').partition(' ')
        context['command'] = {'name': name.lower(), 'args': args}
        return context

    def button(self, label, action, primary=False, disabled=False):
        kind = ft.FilledButton if primary else ft.OutlinedButton
        return kind(label, on_click=lambda e: action(), disabled=disabled,
                    style=ft.ButtonStyle(color=palette.ON_ACCENT if primary else palette.TEXT_2,
                                         bgcolor=palette.ACCENT if primary else "transparent"))

    def text(self, value: str, bold=False):
        return ft.Text(value, size=12, color=palette.TEXT if bold else palette.TEXT_2,
                       weight=ft.FontWeight.W_500 if bold else ft.FontWeight.W_400,
                       width=self.width - 28, selectable=True)

    def refresh(self) -> None:
        self.epoch += 1
        epoch, section = self.epoch, self.section
        if section == "overview":
            self.body.controls = [self.overview]
            self.note("")
            self.page.update()
            return
        if section == "commands":
            self.body.controls = [self.command, self.suggestions, self.button("执行命令", self.run_command, True), self.output]
            self.suggest(update=False)
            style_inputs(self.body)
            self.note("命令操作当前工作台；终端由右上角按钮单独打开。")
            self.page.update()
            return
        cmd = {"tasks": "workbench_status", "workflows": "workflows", "loops": "loop_list",
               "sessions": "sessions", "remote": "remote_status"}[section]
        self.note("正在读取…")
        self.page.update()

        def work():
            from ..bridge import handle
            result = handle({"cmd": cmd})
            if self.epoch != epoch:
                return
            if not result.get("ok"):
                self.note(result.get("error", "无法读取"), True)
            else:
                self.note("")
                self.render(section, result)
            self.page.update()
        self.page.run_thread(work)

    def action(self, req: dict, *, open_created=False, refresh=True) -> None:
        if self.running:
            self.note("上一操作尚未结束")
            self.page.update()
            return
        self._begin_operation()
        epoch = self.epoch
        self.note("正在处理…")
        self.page.update()
        def work():
            from ..bridge import handle
            try:
                result = handle(req)
                if result.get("token"):
                    self.token = result["token"]
                if self.epoch != epoch:
                    return
                if not result.get("ok"):
                    self.note(result.get("error", "操作失败"), True)
                elif result.get("path"):
                    self.note(f"已导出：{result['path']}")
                else:
                    if open_created and result.get("task_id"):
                        self.open_task(result["task_id"])
                    if refresh:
                        self.refresh()
                    else:
                        self.note("已完成")
            finally:
                self._finish_operation()
                self.page.update()
        self.page.run_thread(work)

    def render(self, section: str, result: dict) -> None:
        controls = []
        if section == "tasks":
            rows = result.get("processes", [])
            controls.append(self.text("研究任务在当前引擎中运行；暂停在安全的步骤边界生效。"))
            for row in rows[:30]:
                tid, status = row["task_id"], row["status"]
                controls.extend([
                    self.text(row.get("topic") or tid, True),
                    self.text(f"{STATUS.get(status, status)} · {row.get('done_nodes', 0)}/{row.get('total_nodes', 0)} 步骤"),
                    ft.Row([
                        self.button("打开", lambda t=tid: self.open_task(t)),
                        self.button("暂停" if status == "running" else "继续",
                                    lambda t=tid, st=status: self.action({"cmd": "pause" if st == "running" else "resume", "task_id": t}),
                                    disabled=status not in {"running", "paused", "waiting_human"}),
                    ], spacing=8, wrap=True), ft.Divider(color=palette.BORDER, height=8)])
            if not rows:
                controls.append(self.text("暂无研究任务。到科研工作流选择模板开始。"))
        elif section == "workflows":
            controls.extend([self.topic, self.offline])
            for row in result.get("workflows", []):
                controls.extend([self.text(row["title"], True), self.text(row["description"]),
                    self.text(" → ".join(s.get("name", s.get("id", "")) if isinstance(s, dict) else str(s) for s in row.get("stages", []))),
                    self.button("使用此工作流", lambda wid=row["id"]: self.action({
                        "cmd": "workflow_run", "workflow_id": wid, "topic": self.topic.value or "",
                        "offline": bool(self.offline.value)}, open_created=True)),
                    ft.Divider(color=palette.BORDER, height=8)])
        elif section == "loops":
            controls.extend([self.text("每轮完成后等待间隔；人工确认与工具审批不会自动跳过。"),
                             self.topic, self.workflow, self.offline,
                             ft.Row([self.interval, self.iterations], spacing=8, wrap=True),
                             self.button("启动 Loop", self.start_loop, True)])
            for row in result.get("loops", []):
                lid, status = row["loop_id"], row["status"]
                controls.extend([ft.Divider(color=palette.BORDER, height=12),
                    self.text(row["topic"], True),
                    self.text(f"{STATUS.get(status, status)} · {row['iteration']}/{row['max_iterations']} 轮 · {lid}"),
                    ft.Row([
                        self.button("停止", lambda i=lid: self.action({"cmd": "loop_stop", "loop_id": i}),
                                    disabled=status in {"stopped", "completed", "failed", "cancelled"}),
                        self.button("恢复", lambda i=lid: self.action({"cmd": "loop_resume", "loop_id": i}),
                                    disabled=status not in {"stopped", "interrupted", "paused"}),
                    ], spacing=8, wrap=True)])
                if row.get("task_id"):
                    controls.append(self.button("打开本轮任务", lambda t=row["task_id"]: self.open_task(t)))
                if row.get("last_error"):
                    controls.append(self.text(row["last_error"]))
        elif section == "sessions":
            rows = result.get("sessions", [])
            for row in rows[:30]:
                sid = row["session_id"]
                controls.extend([self.text(row.get("summary") or sid, True),
                    self.text(f"{STATUS.get(row.get('status'), row.get('status', ''))} · {sid}"),
                    ft.Row([
                        self.button("打开", lambda s=sid: self.open_session(s)),
                        self.button("Markdown", lambda s=sid: self.action({"cmd": "session_export", "session_id": s, "format": "markdown"}, refresh=False)),
                        self.button("JSON", lambda s=sid: self.action({"cmd": "session_export", "session_id": s, "format": "json"}, refresh=False)),
                    ], spacing=6, wrap=True), ft.Divider(color=palette.BORDER, height=8)])
            if not rows:
                controls.append(self.text("暂无智能体会话。运行一次智能体后可在这里继续查看和导出。"))
        elif section == "remote":
            active = bool(result.get("running"))
            controls.extend([self.text("手机与电脑连接同一局域网，访问下方地址并输入口令。", True),
                             self.text("连接控制的是当前引擎。关闭工作台后共享停止。"), self.port,
                             self.button("开启局域网控制", self.start_remote, True, disabled=active),
                             self.button("停止共享", lambda: self.action({"cmd": "remote_stop"}), disabled=not active)])
            if active:
                urls = result.get("urls") or [f"http://127.0.0.1:{result.get('port')}"]
                controls.extend(self.text(url) for url in urls)
                if self.token:
                    token = ft.TextField(label="访问口令", value=self.token, read_only=True,
                                         password=True, can_reveal_password=True)
                    controls.extend([token, self.button("复制连接信息", lambda: self.page.run_task(
                        self.page.clipboard.set, "\n".join([*urls, f"访问口令：{self.token}"])))])
                else:
                    controls.append(self.text("访问口令见开启共享的终端或窗口。"))
                controls.append(self.text("手机无法连接时，请检查系统对当前端口的入站访问规则。"))
        self.body.controls = controls
        style_inputs(self.body)

    def suggest(self, update=True):
        from ..command_center import catalog, COMMAND_GROUPS
        value = self.command.value or "/"
        self.command.password = bool(re.search(r"(?i)(api[_-]?key|authorization|password|access[_-]?token|^/?model\s+key|^/?mcp-config\s+save|^/?(?:browser|computer)\s+call)", self.command.value or ""))
        self.suggestions.controls = []
        # The trailing space marks an adopted command; execution requires submission.
        matches = [] if value.startswith('/') and any(char.isspace() for char in value) else catalog(value, context=self._command_context(value))[:7]
        groups = {item['id']: item['label'] for item in COMMAND_GROUPS}
        last_group = None
        for command in matches:
            group = command.get('category')
            if group != last_group:
                self.suggestions.controls.append(ft.Text(groups.get(group, '命令'), size=10,
                    color=palette.TEXT_3, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
                last_group = group
            def choose(e, name=command["name"]):
                self.command.value = "/" + name + " "
                self.suggest()
            self.suggestions.controls.append(ft.TextButton(
                content=ft.Text(f"/{command['name']}  {command['description'] if command['available'] else command['disabled_reason']}", size=11,
                                color=palette.TEXT_2, width=self.width - 52,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS), on_click=choose,
                disabled=not command['available'], tooltip=command['disabled_reason'] or command['effect']))
        if update:
            self.page.update()

    def start_loop(self):
        try:
            interval, iterations = float(self.interval.value), int(self.iterations.value)
        except (ValueError, TypeError):
            self.note("间隔与轮数请输入数字", True)
            self.page.update()
            return
        self.action({"cmd": "loop_start", "workflow_id": self.workflow.value,
                     "topic": self.topic.value or "", "interval_seconds": interval,
                     "max_iterations": iterations, "offline": bool(self.offline.value)})

    def start_remote(self):
        try:
            port = int(self.port.value)
        except (ValueError, TypeError):
            self.note("端口请输入数字", True)
            self.page.update()
            return
        self.action({"cmd": "remote_start", "host": "0.0.0.0", "port": port})

    def run_command(self, e=None):
        text = self.command.value or ""
        from ..command_center import catalog
        name = text.strip().split(' ', 1)[0].lstrip('/').lower()
        context = self._command_context(text)
        row = next((item for item in catalog(context=context) if item['name'] == name), None)
        if row and not row['available']:
            self.note(row['disabled_reason'], True)
            self.page.update()
            return
        if context['busy'] and row is None:
            self.note('上一操作仍在执行；请先使用取消、补充或排队命令', True)
            self.page.update()
            return
        if self.command.password:
            self.command.value = ""
            self.command.password = False
        task_id, epoch = self.current_task(), self.epoch
        self._command_epoch += 1
        command_epoch = self._command_epoch
        self._begin_operation()
        self.note("正在执行命令…")
        self.page.update()
        def work():
            from ..command_center import execute
            try:
                result = execute(text, task_id)
                if result.get('ok') and text.strip().split(' ', 1)[0].lower() == '/mode' and self.on_mode_changed:
                    self.on_mode_changed(result.get('mode'))
                if result.get("token"):
                    self.token = result["token"]
                if self.epoch != epoch or command_epoch != self._command_epoch:
                    return
                self.output.value = result.get("text") or result.get("error", "")
                if result.get("task_id") and result.get("ok"):
                    self.open_task(result["task_id"])
                self.note("已完成" if result.get("ok") else "命令失败", not result.get("ok"))
            finally:
                self._finish_operation()
                self.page.update()
        self.page.run_thread(work)
