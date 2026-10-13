"""Native control tower, explicit Swarm execution and reasoning shortcuts."""
from __future__ import annotations

import asyncio
import os
from typing import Callable

import flet as ft

from .. import service
from .theme import palette as p, style_inputs


STATUS = {"draft": "草稿", "planned": "计划就绪", "running": "运行中", "pending": "排队中",
          "waiting_approval": "等待审批", "waiting_human": "等待确认", "waiting_task": "等待任务",
          "done": "已完成", "completed": "已完成", "error": "失败", "failed": "失败",
          "blocked": "已阻止", "cancelled": "已取消", "cancelling": "正在取消", "paused": "已暂停",
          "interrupted": "已中断", "partial": "部分完成", "queued": "排队中"}
STATUS["waiting_children"] = "等待分支"
TERMINAL = {"done", "completed", "error", "failed", "blocked", "cancelled", "interrupted", "partial"}
ACTIVE = {"running", "cancelling", "queued", "waiting_approval", "waiting_children", "waiting_task"}


class ReasoningControl:
    """The waveform depicts the chosen setting, never private model reasoning."""

    CHOICES = {"auto": "自动", "low": "轻量", "medium": "均衡", "high": "深入"}
    EFFORTS = {
        "auto": ("自动", "沿用服务默认强度"),
        "off": ("关闭", "请求关闭思考，需模型支持"),
        "minimal": ("极低", "简单判断，使用最低强度"),
        "low": ("轻量", "快速筛读与短问答"),
        "medium": ("均衡", "常规分析与证据整理"),
        "high": ("深入", "复杂比较与方法审查"),
        "xhigh": ("超高", "更深入的多步分析"),
        "max": ("最高", "最高强度偏好，留意耗时与用量"),
    }
    SHAPES = {"minimal": (2, 4, 6, 4, 2), "low": (4, 8, 12, 8, 4),
              "medium": (6, 12, 18, 12, 6), "high": (8, 16, 24, 16, 8),
              "xhigh": (12, 20, 24, 20, 12), "max": (20, 24, 24, 24, 20)}

    def __init__(self, page, value="auto", save: Callable | None = None, motion=lambda: True, changed=None):
        self.page, self.value, self.save, self.motion, self.changed = page, value, save, motion, changed
        self.bars = [ft.Container(width=3, height=8, border_radius=3, bgcolor=p.ACCENT_TEXT) for _ in range(5)]
        self.bar_slots = [ft.Container(content=bar, width=3, height=24,
            alignment=ft.Alignment.CENTER) for bar in self.bars]
        self.dots = [ft.Container(width=3, height=3, border_radius=3, bgcolor=p.ACCENT_TEXT) for _ in range(5)]
        self.off_mark = ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINED, size=18, color=p.TEXT_3)
        self.glyph = ft.Stack([ft.Row(items, spacing=3, height=30,
            alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            for items in (self.bar_slots, self.dots)] + [ft.Container(self.off_mark,
                width=34, height=30, alignment=ft.Alignment.CENTER)], width=34, height=30)
        self.labels = {key: ft.Text(label, size=12, text_align=ft.TextAlign.CENTER)
                       for key, label in self.CHOICES.items()}
        self.buttons = {key: ft.TextButton(self.labels[key], width=56, height=30,
            tooltip=f"将默认模型新配置的思考强度设为{label}（{key}）",
            on_click=lambda e, k=key: self.choose(k)) for key, label in self.CHOICES.items()}
        self.segments = {key: ft.Container(content=button, border_radius=6)
                         for key, button in self.buttons.items()}
        self.track = ft.Container(content=ft.Row(list(self.segments.values()), spacing=2, tight=True),
            padding=3, border_radius=9, bgcolor=p.SURFACE_3, border=ft.Border.all(1, p.BORDER))
        self.advanced_items = {key: ft.PopupMenuItem(content=ft.Column([
            ft.Row([ft.Text(label, size=12, weight=ft.FontWeight.W_500, expand=True),
                    ft.Text(key, size=11, font_family="Consolas")], spacing=8),
            ft.Text(hint, size=11, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        ], spacing=2, width=250), checked=key == value, height=52,
            on_click=lambda e, k=key: self.choose(k)) for key, (label, hint) in self.EFFORTS.items()}
        self.advanced_label = ft.Text("全部档位", size=12, max_lines=1,
                                      overflow=ft.TextOverflow.ELLIPSIS)
        self.advanced = ft.PopupMenuButton(items=list(self.advanced_items.values()),
            content=ft.Container(ft.Row([self.advanced_label,
                ft.Icon(ft.Icons.EXPAND_MORE, size=16)], spacing=4, tight=True),
                padding=ft.Padding(8, 0, 8, 0), height=32, alignment=ft.Alignment.CENTER),
            padding=0, width=128, height=34, shape=ft.RoundedRectangleBorder(radius=6))
        self.note = ft.Text(size=11, color=p.TEXT_3, max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS)
        self.content = ft.Column([
            ft.Row([ft.Row([self.glyph, ft.Text("思考强度", size=12, color=p.TEXT_2)], spacing=6, tight=True),
                    self.track, self.advanced], spacing=8, run_spacing=4, wrap=True), self.note], spacing=5)
        self.refresh(value)

    def motion_enabled(self):
        return bool(self.motion()) and os.environ.get("GEZHI_REDUCED_MOTION", "").lower() not in {"1", "true", "yes", "on"}

    def refresh(self, value=None, confirmed=False):
        if value is not None:
            self.value = value
        self.track.bgcolor, self.track.border = p.SURFACE_3, ft.Border.all(1, p.BORDER)
        for key, button in self.buttons.items():
            selected = key == self.value
            self.labels[key].color = p.ACCENT_TEXT if selected else p.TEXT_2
            self.labels[key].weight = ft.FontWeight.W_600 if selected else ft.FontWeight.W_400
            self.segments[key].bgcolor = p.ACTIVE if selected else "transparent"
            button.style = ft.ButtonStyle(color=p.ACCENT_TEXT if selected else p.TEXT_2,
                bgcolor="transparent", shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.Padding(8, 0, 8, 0), overlay_color=p.HOVER,
                animation_duration=180 if self.motion_enabled() else 0)
        label, self.use_hint = self.EFFORTS.get(self.value, (f"未识别（{self.value}）", "请到模型设置检查此值"))
        self.advanced_label.value = "全部档位" if self.value in self.CHOICES else f"{label} · {self.value}" if self.value in self.EFFORTS else "未识别档位"
        self.advanced_label.color = p.TEXT_2 if self.value in self.CHOICES else p.ACCENT_TEXT
        self.advanced.bgcolor = p.INPUT
        self.advanced.style = ft.ButtonStyle(color=p.TEXT_2,
            overlay_color={ft.ControlState.HOVERED: p.HOVER, ft.ControlState.FOCUSED: p.ACTIVE},
            side=ft.BorderSide(1, p.BORDER), shape=ft.RoundedRectangleBorder(radius=6),
            animation_duration=180 if self.motion_enabled() else 0)
        self.advanced.tooltip = f"完整思考档位；当前 {label}（{self.value}）。实际支持由模型与协议决定。"
        for key, item in self.advanced_items.items():
            item.checked = key == self.value
            label_text, code_text = item.content.controls[0].controls
            label_text.color = p.ACCENT_TEXT if item.checked else p.TEXT
            code_text.color = p.TEXT_3
            item.content.controls[1].color = p.TEXT_2
        prefix = "已保存 · " if confirmed else ""
        self.note.value = f"{prefix}{label}（{self.value}）· {self.use_hint}。默认模型新配置；独立模型配置另设。"
        self.note.color = p.TEXT_3
        self.pulse(0, active=False)

    def pulse(self, frame: int, active: bool):
        enabled = self.motion_enabled()
        known = self.value in self.EFFORTS
        automatic = self.value == "auto"
        off = self.value == "off"
        transition = ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC) if enabled else None
        for segment in self.segments.values():
            segment.animate = transition
        for button in self.buttons.values():
            if button.style:
                button.style.animation_duration = 180 if enabled else 0
        if self.advanced.style:
            self.advanced.style.animation_duration = 180 if enabled else 0
        self.advanced.popup_animation_style = ft.AnimationStyle(
            duration=180, reverse_duration=120, curve=ft.AnimationCurve.EASE_OUT_CUBIC
        ) if enabled else ft.AnimationStyle.no_animation()
        self.off_mark.opacity = 1 if off else 0
        self.off_mark.color = p.TEXT_3
        for i, bar in enumerate(self.bars):
            # Native Flet 0.86.5 animate_scale does not render per-axis Scale
            # reliably. Animate the inner fill; its outer slot stays 3x24.
            bar.animate = bar.animate_opacity = transition
            bar.height = self.SHAPES.get(self.value, (2, 2, 2, 2, 2))[i]
            bar.bgcolor = p.ACCENT_TEXT
            intensity = (1 if (frame + i) % 5 in (1, 2) else .45) if active and enabled else .85
            bar.opacity = 0 if automatic or off or not known else intensity
            dot = self.dots[i]
            dot.animate_opacity = transition
            dot.bgcolor = p.ACCENT_TEXT
            dot.opacity = .45 if not known else intensity if automatic else 0

    def choose(self, value: str):
        if value not in self.EFFORTS:
            return
        try:
            if self.save:
                result = self.save({"llm_reasoning_effort": value})
                if isinstance(result, dict) and result.get("ok") is False:
                    raise ValueError(result.get("error", "无法保存思考强度"))
            self.refresh(value, self.save is not None)
            if self.changed:
                self.changed(value)
        except Exception as exc:
            from ..workbench import redact
            self.note.value, self.note.color = redact(str(exc)), p.DANGER
        self.page.update()


class ControlTower:
    """All rows come from service snapshots; workers never update a closed view."""

    def __init__(self, page, open_task=None, open_session=None, request=None, api=None):
        self.page, self.api = page, api or service
        self.open_task, self.open_session = open_task, open_session
        self.get_request = request or (lambda: "")
        self.opened, self.busy, self.epoch, self.view_epoch = False, False, 0, 0
        self.section, self.plan_id, self.snapshot = "tower", "", {}
        self.width = 820
        self.request = ft.TextField(label="Swarm 研究目标", multiline=True, min_lines=2, max_lines=5,
            hint_text="例如：比较三种科研智能体的检索策略，形成可复核的结论", on_change=lambda e: self.invalidate_plan())
        self.parallel = ft.Dropdown(label="同时运行", value="4", width=150,
            options=[ft.dropdown.Option(str(n), f"{n} 个分支") for n in range(1, 5)],
            on_select=lambda e: self.invalidate_plan())
        self.status = ft.Text("", size=12, color=p.TEXT_2, selectable=True)
        self.body = ft.Column(spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)
        self.preview_button = ft.OutlinedButton("生成 Swarm 计划", icon=ft.Icons.SCHEMA, on_click=lambda e: self.preview())
        self.start_button = ft.FilledButton("启动 Swarm", icon=ft.Icons.PLAY_ARROW, disabled=True, on_click=lambda e: self.start())
        self.cancel_button = ft.TextButton("取消 Swarm", disabled=True, on_click=lambda e: self.cancel())
        self.form = ft.Column([self.request, ft.Row([self.parallel, self.preview_button, self.start_button,
            self.cancel_button], wrap=True, spacing=8)], spacing=12, visible=False)
        self.container = ft.Container(width=self.width, height=540, content=ft.Column([
            ft.Row([ft.TextButton("控制塔", on_click=lambda e: self.select("tower")),
                    ft.TextButton("Swarm 协作", on_click=lambda e: self.select("swarm")),
                    ft.IconButton(ft.Icons.REFRESH, tooltip="刷新控制塔", on_click=lambda e: self.refresh())], spacing=6),
            self.form, self.status, ft.Divider(height=1, color=p.BORDER), self.body], spacing=14))
        self.dialog = ft.AlertDialog(title=ft.Text("Tower · 运行控制塔", size=22, color=p.TEXT),
            content=self.container, actions=[ft.TextButton("关闭", on_click=lambda e: self.close())],
            on_dismiss=lambda e: self.dismiss())
        style_inputs(self.container)

    def resize(self):
        width, height = self.page.width, self.page.height
        self.width = min(880, max(360, float(width) - 120)) if isinstance(width, (int, float)) else 820
        self.container.width = self.width
        self.container.height = min(660, max(360, float(height) - 210)) if isinstance(height, (int, float)) else 540
        self.request.width = self.width

    def text(self, value, *, bold=False, color=None, size=12):
        return ft.Text(str(value), size=size, color=color or p.TEXT_2, selectable=True,
            weight=ft.FontWeight.W_600 if bold else ft.FontWeight.NORMAL, width=self.width - 34)

    def open(self, section="tower"):
        if self.opened and self.dialog.open:
            self.select(section)
            return
        self.opened = True
        self.epoch += 1
        self.resize()
        self.dialog.open = True
        self.page.show_dialog(self.dialog)
        self.select(section)
        self.page.run_task(self.poll, self.epoch)

    def dismiss(self):
        self.opened = False
        self.busy = False
        self.epoch += 1

    def close(self):
        self.dismiss()
        if self.dialog.open:
            self.page.pop_dialog()

    def select(self, section):
        self.view_epoch += 1
        self.section = section if section in {"tower", "swarm"} else "tower"
        self.form.visible = self.section == "swarm"
        self.dialog.title.value = "Swarm · 多智能体协作" if self.form.visible else "Tower · 运行控制塔"
        if self.form.visible and not self.request.value:
            self.request.value = self.get_request() or ""
        self.body.controls = []
        self.refresh()

    def notice(self, value, error=False):
        self.status.value, self.status.color = value, p.DANGER if error else p.TEXT_2

    def update_buttons(self):
        status = self.snapshot.get("status", "")
        self.request.disabled = self.parallel.disabled = self.busy or status in ACTIVE
        self.preview_button.disabled = self.busy or status in ACTIVE
        self.start_button.disabled = self.busy or not self.plan_id or status not in {"planned", "draft"}
        self.cancel_button.disabled = self.busy or not self.plan_id or status in TERMINAL | {"planned", "draft", ""}

    def submit(self, operation, callback):
        if self.busy:
            return
        self.busy = True
        epoch, section, view_epoch = self.epoch, self.section, self.view_epoch
        self.notice("正在读取…")
        self.update_buttons()
        self.page.update()

        def work():
            try:
                result = operation()
                if not self.opened or not self.dialog.open or epoch != self.epoch:
                    return
                if isinstance(result, dict) and result.get("ok") is False:
                    raise ValueError(result.get("error", "操作失败"))
                if section == self.section and view_epoch == self.view_epoch:
                    callback(result)
                    self.notice("已更新 · 显示保存的执行状态与已报送用量")
            except Exception as exc:
                if self.opened and epoch == self.epoch:
                    from ..workbench import redact
                    self.notice(redact(str(exc)), True)
            finally:
                if epoch == self.epoch:
                    self.busy = False
                    self.update_buttons()
                    if self.opened:
                        self.page.update()
        self.page.run_thread(work)

    def refresh(self):
        if not self.opened:
            return
        if self.section == "tower":
            self.submit(self.api.get_control_tower, self.render_tower)
        elif self.plan_id:
            plan_id = self.plan_id
            self.submit(lambda: self.api.get_swarm(plan_id), self.render_swarm)
        else:
            self.body.controls = [self.text("把任务拆成独立分支。先检查计划，再启动并行执行。", bold=True),
                self.text("分支沿用工具权限与审批；控制塔中可以查看每个会话。")]
            self.notice("填写目标，然后生成计划。")
            self.update_buttons()
            self.page.update()

    async def poll(self, epoch):
        while self.opened and self.dialog.open and epoch == self.epoch:
            await asyncio.sleep(3)
            if self.opened and self.dialog.open and epoch == self.epoch and not self.busy:
                self.refresh()

    def invalidate_plan(self):
        # Running work retains its identity. A new request is planned after it finishes.
        if self.snapshot.get("status") in ACTIVE:
            return
        self.view_epoch += 1
        self.plan_id, self.snapshot = "", {}
        self.body.controls = []
        self.update_buttons()
        self.notice("目标已修改，请重新生成计划。")
        self.page.update()

    def preview(self):
        request = (self.request.value or "").strip()
        if not request:
            self.notice("请先填写研究目标。", True)
            self.page.update()
            return
        parallel = int(self.parallel.value or "4")
        self.submit(lambda: self.api.plan_swarm(request, max_parallel=parallel), self.render_swarm)

    def start(self):
        if self.plan_id and self.snapshot.get("status") in {"planned", "draft"}:
            plan_id = self.plan_id
            self.submit(lambda: self.api.start_swarm(plan_id), self.render_swarm)

    def cancel(self):
        if self.plan_id:
            plan_id = self.plan_id
            self.submit(lambda: self.api.cancel_swarm(plan_id), self.render_swarm)

    def go_session(self, session_id):
        if self.open_session and session_id:
            self.close()
            self.open_session(session_id)

    def pending_rows(self, rows):
        result = []
        for row in rows:
            sid = row.get("session_id", "")
            result.append(ft.Container(padding=12, border_radius=6, bgcolor=p.WARN_BG,
                content=ft.Column([self.text(f"等待审批 · {row.get('tool', '工具操作')}", bold=True, color=p.WARN),
                    self.text(f"会话 {sid} · 操作 {row.get('id', '')}"),
                    ft.OutlinedButton("查看审批", disabled=not bool(sid and self.open_session),
                        on_click=lambda e, s=sid: self.go_session(s))], spacing=8)))
        return result

    def render_tower(self, snapshot):
        snapshot = snapshot.get("tower", snapshot)
        summary, usage = snapshot.get("summary", {}), snapshot.get("usage", {})
        rows = [ft.Row([ft.Container(padding=12, width=max(120, (self.width - 36) / 4),
            bgcolor=p.SURFACE_2, border_radius=6, content=ft.Column([
                ft.Text(str(summary.get(key, 0)), size=25, weight=ft.FontWeight.W_600, color=color,
                        max_lines=1),
                ft.Text(label, size=11, color=p.TEXT_2)], spacing=4))
            for key, label, color in [("running", "运行中", p.INFO), ("waiting", "等待处理", p.WARN),
                                      ("done", "已完成", p.SUCCESS), ("errors", "异常", p.DANGER)]],
            spacing=10, wrap=True),
            self.text(f"模型调用 {usage.get('model_calls', 0)}  ·  已报送 tokens {usage.get('total_tokens', 0)}  ·  工具调用 {usage.get('tool_calls', 0)}")]
        rows.extend(self.pending_rows(snapshot.get("pending", [])))
        for key, title in [("swarms", "Swarm 协作"), ("sessions", "智能体会话"), ("tasks", "科研任务")]:
            items = snapshot.get(key, [])
            rows.append(self.text(f"{title} · {len(items)}", bold=True, color=p.TEXT, size=14))
            if not items:
                rows.append(self.text("暂无记录"))
            for row in items[:25]:
                ident = row.get("id") or row.get("plan_id") or row.get("session_id") or row.get("task_id")
                status = STATUS.get(row.get("status"), row.get("status", "未知"))
                content = [self.text(row.get("title") or row.get("request") or ident, bold=True), self.text(f"{status} · {ident}")]
                if key == "swarms":
                    content.append(ft.TextButton("查看协作", on_click=lambda e, i=ident: self.show_swarm(i)))
                elif key == "sessions" and self.open_session:
                    content.append(ft.TextButton("打开会话", on_click=lambda e, i=ident: self.go_session(i)))
                elif key == "tasks" and self.open_task:
                    def open_task(e, i=ident):
                        self.close()
                        self.open_task(i)
                    content.append(ft.TextButton("打开任务", on_click=open_task))
                rows.append(ft.Container(padding=12, bgcolor=p.SURFACE_2, border_radius=6,
                    content=ft.Column(content, spacing=6)))
        rows.extend(self.text(w, color=p.WARN) for w in snapshot.get("warnings", []))
        self.body.controls = rows

    def show_swarm(self, plan_id):
        self.plan_id = plan_id
        self.snapshot = {}
        self.select("swarm")

    def render_swarm(self, snapshot):
        snapshot = snapshot.get("swarm", snapshot)
        self.snapshot, self.plan_id = snapshot, snapshot.get("plan_id", self.plan_id)
        self.request.value = snapshot.get("request") or self.request.value
        self.parallel.value = str(snapshot.get("max_parallel") or self.parallel.value)
        running = snapshot.get("status") in ACTIVE
        self.request.disabled = self.parallel.disabled = running
        rows = [self.text(f"{STATUS.get(snapshot.get('status'), snapshot.get('status', '计划'))} · {self.plan_id}", bold=True, color=p.TEXT)]
        rows.extend(self.pending_rows(snapshot.get("pending", [])))
        for index, branch in enumerate(snapshot.get("branches", []), 1):
            status = STATUS.get(branch.get("status"), branch.get("status", "计划就绪"))
            if snapshot.get("status") == "planned" and branch.get("status") in {"queued", "pending"}:
                status = "待启动"
            details = [self.text(f"{index:02d}  {branch.get('agent', '智能体')} · {status}", bold=True, color=p.ACCENT_TEXT),
                       self.text(branch.get("request", ""))]
            if branch.get("error"):
                details.append(self.text(branch["error"], color=p.DANGER))
            if branch.get("session_id"):
                details.append(ft.TextButton("查看分支会话", on_click=lambda e, s=branch["session_id"]: self.go_session(s)))
            rows.append(ft.Container(padding=14, border=ft.Border.only(left=ft.BorderSide(3, p.ACCENT)),
                bgcolor=p.SURFACE_2, content=ft.Column(details, spacing=8)))
        if snapshot.get("error"):
            rows.append(self.text(snapshot["error"], color=p.DANGER))
        self.body.controls = rows
        self.update_buttons()
