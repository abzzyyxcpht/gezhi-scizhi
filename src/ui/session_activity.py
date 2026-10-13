"""Bounded native activity overview, polled only while explicitly expanded."""
from __future__ import annotations

import time

import flet as ft

from ..bridge import handle
from ..workbench import redact
from .control_tower import STATUS
from .theme import palette as p, style_inputs


class SessionActivityPanel:
    def __init__(self, page, selected, *, epoch=lambda: 0, open_session=None, api=None, clock=None):
        self.page, self.selected, self.epoch = page, selected, epoch
        self.api, self.clock, self.open_session = api or handle, clock or time.monotonic, open_session or (lambda sid: None)
        self.expanded, self.busy, self.sequence, self.scope = False, False, 0, None
        self.last_request, self.snapshot, self.child_buttons = float('-inf'), None, {}
        self.summary = ft.Text("执行活动与子任务", size=12, color=p.TEXT_2, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.note = ft.Text("展开后查看真实执行记录与子任务状态。", size=11, color=p.TEXT_3, max_lines=3)
        self.rows = ft.Column(spacing=6)
        self.content = ft.ExpansionTile(title=self.summary, controls=[
            ft.Row([ft.TextButton("刷新活动", icon=ft.Icons.REFRESH, on_click=lambda e: self.refresh(force=True))]),
            self.note, self.rows], on_change=lambda e: self.expand(str(e.data).lower() == "true"),
            dense=True, tile_padding=0, min_tile_height=32, maintain_state=True,
            controls_padding=ft.Padding(0, 0, 0, 8))

    def expand(self, value=True):
        self.expanded = value
        if value:
            self.refresh(force=True)
        else:
            self.sequence += 1
            self.busy = False

    def _key(self):
        return self.selected(), self.epoch()

    def _render(self, data):
        if data.get("session_id") != self.selected():
            raise ValueError("执行活动与当前会话不匹配")
        self.snapshot, self.child_buttons = data, {}
        children, items = data.get("children", []), data.get("items", [])
        self.summary.value = f"执行活动 · {len(items)} 条记录 · {len(children)} 个子任务"
        self.note.value = "来自已保存日志；可打开子会话查看详情。" + (" 当前仅显示部分记录。" if data.get("truncated") else "")
        self.note.color = p.TEXT_3
        controls = []
        rendered_scope = self._key()
        def open_child(identity):
            if rendered_scope == self._key() and self.expanded:
                self.open_session(identity)
        for child in children[:30]:
            sid = child.get("session_id")
            if not sid:
                continue
            status = STATUS.get(child.get("status"), "状态未报告")
            button = ft.TextButton(f"{child.get('agent') or '子任务'} · {status} · {sid}",
                on_click=lambda e, identity=sid: open_child(identity),
                style=ft.ButtonStyle(color=p.WARN if child.get("pending") else p.ACCENT_TEXT))
            self.child_buttons[sid] = button
            controls.append(ft.Container(ft.Column([button,
                ft.Text(redact(str(child.get("request") or ""))[:150], size=11, color=p.TEXT_2, max_lines=2)], spacing=0),
                padding=ft.Padding(min(36, max(0, int(child.get("depth") or 1) - 1) * 12), 0, 0, 0)))
        for item in reversed(items[-30:]):
            color = p.DANGER if item.get("status") in {"error", "blocked"} else p.TEXT_2
            controls.append(ft.Row([ft.Text(STATUS.get(item.get("status"), item.get("status") or "已记录"), size=11, color=color, width=82),
                ft.Text(redact(str(item.get("label") or "执行记录")), size=12, color=p.TEXT, expand=True,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=8))
        self.rows.controls = controls or [ft.Text("当前会话尚无工具或子任务记录。", size=12, color=p.TEXT_2)]
        style_inputs(self.rows)

    def refresh(self, force=False):
        scope = self._key()
        if scope != self.scope:
            self.scope = scope
            self.sequence += 1
            self.busy = False
            self.snapshot, self.child_buttons, self.rows.controls = None, {}, []
            self.last_request = float('-inf')
        if not self.expanded or not scope[0] or self.busy:
            return
        now = self.clock()
        if not force and now - self.last_request < 1.5:
            return
        self.last_request, self.busy = now, True
        self.sequence += 1
        sequence = self.sequence
        def work():
            try:
                response = self.api({"cmd": "session_activity", "session_id": scope[0], "limit": 30})
                if scope != self._key() or sequence != self.sequence or not self.expanded:
                    return
                if response.get("ok") is not True:
                    raise ValueError(response.get("error") or "活动读取失败")
                self._render(response["activity"])
            except Exception as error:
                if scope == self._key() and sequence == self.sequence:
                    self.note.value, self.note.color = redact(str(error)), p.DANGER
            finally:
                if sequence == self.sequence:
                    self.busy = False
                    if scope == self._key() and self.expanded:
                        self.page.update()
        self.page.run_thread(work)
