"""智能体页内建终端面板：回车执行命令，直接复用 tools_workspace 的 shell 实现。

UI 用户输入视为已授权——绕过审批，但保留同一实现里的危险命令 deny 名单、
超时与输出采集（不复制代码，直接调用 ``tools_workspace._shell_run``）。
输出区只保留最近 200 行；stderr / 非零退出码 / deny 命中用红色显示。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

import flet as ft

from .theme import palette, style_inputs, style_typography
from ..workbench import redact

OUTPUT_LINE_LIMIT = 200
OUTPUT_LINE_CHARS = 1000
_FOLDED = '…（长行已折叠）'


class TerminalPanel:
    """可收起的底部终端：cwd 默认当前会话绑定的项目根。"""

    def __init__(self, page: ft.Page, runtime_provider: Callable[[], Any],
                 session_provider: Callable[[], str]) -> None:
        self.page = page
        self.runtime_provider = runtime_provider
        self.session_provider = session_provider
        self.running = False
        self.closed = False
        self._lock = threading.RLock()
        self._generation = 0
        self._cancel_event: threading.Event | None = None
        self.last_output_session = None
        self.lines: list[ft.Text] = []
        self.output = ft.Column(spacing=1, scroll=ft.ScrollMode.AUTO, auto_scroll=True,
                                horizontal_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
        self.output_button = ft.TextButton('完整输出', visible=False, on_click=lambda e: self.open_outputs())
        self.stop_button = ft.TextButton('停止命令', icon=ft.Icons.STOP,
                                        disabled=True, on_click=self.stop)
        self.status = ft.Text('', size=10, color=palette.TEXT_3, max_lines=1,
                              overflow=ft.TextOverflow.ELLIPSIS)
        self.input = ft.TextField(
            hint_text="输入命令，回车执行 · cwd = 当前项目根",
            dense=True, border_radius=6, on_submit=self.execute, expand=True,
            bgcolor=palette.SURFACE, border_color=palette.BORDER)
        self.body = ft.Container(
            visible=False, height=230, bgcolor=palette.SURFACE_2, border_radius=8,
            border=ft.Border.all(1, palette.BORDER), padding=10,
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.TERMINAL, size=14, color=palette.TEXT_3),
                    ft.Text("内建终端", size=12, color=palette.TEXT_2),
                    ft.Text("输入即授权 · 危险命令仍被拦截", size=10, color=palette.TEXT_3,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                    self.status,
                    self.output_button,
                    ft.IconButton(ft.Icons.CLOSE, icon_size=14, icon_color=palette.TEXT_3,
                                  tooltip="收起终端", on_click=lambda e: self.toggle()),
                ], spacing=6),
                ft.Container(expand=True, content=self.output),
                ft.Row([self.input, self.stop_button], spacing=6),
            ], spacing=6, expand=True))
        style_inputs(self.body)

    def toggle(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.body.visible = not self.body.visible
            self._update()

    def _update(self) -> None:
        if self.closed:
            return
        try:
            self.page.update()
        except RuntimeError:
            # Only a confirmed application close makes a failed update harmless.
            if not self.closed:
                raise

    def close(self) -> None:
        """Application shutdown: cancel ownership and reject all later UI work."""
        with self._lock:
            self.closed = True
            self._generation += 1
            if self._cancel_event is not None:
                self._cancel_event.set()

    def stop(self, e=None) -> None:
        with self._lock:
            if self.closed or not self.running or self._cancel_event is None:
                return
            if self._cancel_event.is_set():
                return
            self._cancel_event.set()
            self.stop_button.disabled = True
            self.status.value = '正在停止…'
            self.status.color = palette.TEXT_3
            self._update()

    @staticmethod
    def _bounded_line(text: str) -> str:
        return text if len(text) <= OUTPUT_LINE_CHARS else text[:OUTPUT_LINE_CHARS] + _FOLDED

    def _emit_line(self, text: str, color: str) -> ft.Text:
        line = ft.Text(self._bounded_line(text.rstrip('\r')), size=11, font_family='Consolas',
                       color=color, selectable=True, no_wrap=False, max_lines=4,
                       overflow=ft.TextOverflow.ELLIPSIS)
        style_typography(line)
        self.lines.append(line)
        if len(self.lines) > OUTPUT_LINE_LIMIT:
            self.lines = self.lines[-OUTPUT_LINE_LIMIT:]
        self.output.controls = self.lines
        return line

    def _emit(self, text: str, color: str) -> None:
        for part in str(text).rstrip('\n').split('\n'):
            self._emit_line(part, color)

    def _emit_live(self, stream: str, text: str, partials: dict[str, ft.Text]) -> None:
        """Join incremental fragments independently for stdout and stderr."""
        parts = text.replace('\r\n', '\n').split('\n')
        color = palette.DANGER if stream == 'stderr' else palette.TEXT
        line = partials.pop(stream, None)
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            if final and not part:
                break
            if line is None:
                line = self._emit_line(part, color)
            elif not line.value.endswith(_FOLDED):
                line.value = self._bounded_line(line.value + part.rstrip('\r'))
            if final:
                partials[stream] = line
            else:
                line = None

    def execute(self, e=None) -> None:
        with self._lock:
            if self.closed or self.running:
                return
            command = (self.input.value or "").strip()
            if not command:
                return
            sid = self.session_provider() or ''
            runtime = self.runtime_provider()
            self._generation += 1
            generation = self._generation
            cancel_event = self._cancel_event = threading.Event()
            self.input.value = ""
            self._emit(str(redact(f"$ {command}")), palette.TEXT)
            self.running = True
            self.input.disabled = True
            self.stop_button.disabled = False
            self.status.value, self.status.color = '执行中…', palette.TEXT_3
            self.last_output_session = None
            self.output_button.visible = False
            self._update()

        partials: dict[str, ft.Text] = {}
        seen_output = False

        def current() -> bool:
            return not self.closed and self.running and generation == self._generation

        def on_output(stream: str, text: str) -> None:
            nonlocal seen_output
            with self._lock:
                if not current() or stream not in ('stdout', 'stderr') or not text:
                    return
                # The shell adapter supplies already-redacted, throttled fragments.
                self._emit_live(stream, text, partials)
                seen_output = True
                self._update()

        def work() -> None:
            from ..tools_workspace import _shell_run
            with self._lock:
                if not current():
                    return
            try:
                # UI 输入视为已授权：不经审批门，直接走 shell 底层函数
                # （deny 名单 / 超时 / 输出预览与归档均在 _shell_run 内部生效）。
                result = _shell_run({"__runtime": runtime,
                                     "__session_id": sid,
                                     "__on_output": on_output,
                                     "__cancel_event": cancel_event,
                                     "command": command, "cwd": ".",
                                     "timeout_seconds": ""})
            except Exception as exc:  # noqa: BLE001 —— deny 命中 / 参数错误都落到这里
                result = {'ok': False, 'error': f'执行失败：{redact(str(exc))}'}
            with self._lock:
                if not current():
                    return
                self._finish(result, sid, seen_output)
                self.running = False
                self._cancel_event = None
                self.input.disabled = False
                self.stop_button.disabled = True
                self._update()

        self.page.run_thread(work)

    def _finish(self, result: dict[str, Any], sid: str, seen_output: bool) -> None:
        pending: list[tuple[str, str]] = []
        if not seen_output:  # Legacy callers and mocks may only return a final preview.
            if result.get('stdout'):
                pending.append((result['stdout'], palette.TEXT))
            if result.get('stderr'):
                pending.append((result['stderr'], palette.DANGER))
        self.status.value, self.status.color = '已完成', palette.TEXT_3
        if result.get('tree_empty') is False:
            self.status.value, self.status.color = '停止未确认', palette.DANGER
        elif result.get('cancelled'):
            self.status.value, self.status.color = '已停止', palette.DANGER
            pending.append(('[已停止]', palette.DANGER))
        elif result.get('timed_out'):
            self.status.value, self.status.color = '已超时', palette.DANGER
            pending.append(('[执行超时]', palette.DANGER))
        elif result.get('exit_code') not in (0, None):
            self.status.value, self.status.color = f"退出码 {result['exit_code']}", palette.DANGER
            pending.append((f'[{self.status.value}]', palette.DANGER))
        elif result.get('ok') is False or result.get('error'):
            self.status.value, self.status.color = '执行失败', palette.DANGER
        elif result.get('capture_complete') is False:
            self.status.value = '已完成 · 部分输出'
        if result.get('error'):
            pending.append((str(redact(str(result['error']))), palette.DANGER))
        if result.get('capture_complete') is False:
            pending.append(('（已保存部分输出；输出采集不完整）' if result.get('output_refs')
                            else '（输出采集不完整；当前仅保留可用预览）', palette.DANGER))
        elif result.get('truncated'):
            pending.append(('（预览已截断；点击「完整输出」分段读取本次命令的已归档正文）'
                            if result.get('output_refs') else '（完整输出未能归档，当前只有预览）', palette.TEXT_3))
        if result.get('output_refs'):
            self.last_output_session = sid
            self.output_button.visible = True
            self.output_button.content = '已保存输出' if result.get('capture_complete') is False else '完整输出'
        if not seen_output and not pending:
            pending.append(('(无输出)', palette.TEXT_3))
        for text, color in pending:
            self._emit(text, color)

    def open_outputs(self):
        with self._lock:
            if self.closed or not self.last_output_session:
                return
            sid = self.last_output_session
            from .tool_outputs import ToolOutputsDialog
            ToolOutputsDialog(self.page, sid, lambda: sid).open()
