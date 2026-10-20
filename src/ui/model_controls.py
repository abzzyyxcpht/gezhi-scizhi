"""Native quick model controls; loading and preview have no model side effects."""
from __future__ import annotations

import flet as ft

from ..bridge import handle
from ..workbench import redact
from .effort_feedback import EffortFeedback
from .theme import palette as p, style_inputs

STAGES = {"default": "默认用途", "planning": "智能体规划", "extraction": "信息抽取", "synthesis": "综述生成", "review": "证据审查", "fast": "快速任务", "strong": "复杂任务"}
EFFORTS = {"auto": "自动", "off": "关闭", "minimal": "极低", "low": "轻量", "medium": "均衡", "high": "深入", "xhigh": "超高", "max": "最高"}
PROTOCOLS = {"chat_completions": "兼容对话接口", "responses": "Responses 接口", "anthropic_messages": "Messages 接口", "gemini": "Gemini 接口", "ollama": "Ollama 本地接口"}


class ModelControlPanel:
    def __init__(self, page, *, api=None, on_saved=None):
        self.page, self.api, self.on_saved = page, api or handle, on_saved or (lambda: None)
        self.snapshot, self.opened, self.sequence, self.busy = None, False, 0, False
        self.stage = ft.Dropdown(label="科研阶段", value="planning", options=[ft.dropdown.Option(k, v) for k, v in STAGES.items()], on_select=lambda e: self.load())
        self.profile = ft.Dropdown(label="模型配置", options=[], on_select=lambda e: self.select_profile())
        self.effort = ft.Dropdown(label="该模型配置的思考强度", options=[],
                                  on_select=lambda e: self.preview_effort())
        self.feedback = EffortFeedback(page)
        self.preview_note = ft.Text("", size=12, color=p.TEXT_2, expand=True)
        self.preview = ft.Row([self.feedback.content, self.preview_note], spacing=10)
        self.summary = ft.Text("读取模型配置…", size=13, color=p.TEXT, max_lines=2)
        self.scope = ft.Text("保存用于后续请求。已有模型会话若检测到配置变化会停止，需新建或分叉；请先完成或停止当前执行。", size=11, color=p.WARN)
        self.notes = ft.Text("", size=11, color=p.TEXT_3, max_lines=6)
        self.message = ft.Text("", size=12, color=p.TEXT_2)
        self.apply_button = ft.FilledButton("保存阶段与思考设置", icon=ft.Icons.CHECK, disabled=True, on_click=lambda e: self.apply())
        self.body = ft.Container(width=560, content=ft.Column([
            self.summary, self.stage, self.profile, self.effort, self.preview, self.scope, self.notes,
            self.message, ft.Row([ft.TextButton("刷新", icon=ft.Icons.REFRESH, on_click=lambda e: self.load()), self.apply_button], wrap=True)
        ], spacing=12, scroll=ft.ScrollMode.AUTO, tight=True), padding=4)
        self.dialog = ft.AlertDialog(title=ft.Text("科研模型 · 选择与思考", size=20), content=self.body,
            actions=[ft.TextButton("关闭", on_click=lambda e: self.close())], on_dismiss=lambda e: self.dismiss())
        self.button = ft.OutlinedButton("科研模型", icon=ft.Icons.TUNE, tooltip="按科研阶段选择实际模型与思考参数", on_click=lambda e: self.open())
        style_inputs(self.body)

    def open(self):
        self.opened = True
        width = self.page.width
        self.body.width = min(560, max(260, width - 100)) if isinstance(width, (int, float)) else 560
        self.dialog.open = True
        self.page.show_dialog(self.dialog)
        self.load()

    def dismiss(self):
        self.opened, self.busy = False, False
        self.sequence += 1
        self.feedback.clear_feedback()

    def close(self):
        self.dismiss()
        self.page.pop_dialog()

    def _enabled(self):
        self.stage.disabled = self.profile.disabled = self.effort.disabled = self.busy
        self.apply_button.disabled = self.busy or not self.snapshot or not self._valid_effort()

    def _valid_effort(self):
        target = next((row for row in (self.snapshot or {}).get("items", [])
                       if row["id"] == self.profile.value), None)
        return target is not None and any(row["enabled"] and row["value"] == self.effort.value
                                         for row in target["efforts"])

    def _effort_row(self):
        target = next((row for row in (self.snapshot or {}).get("items", [])
                       if row["id"] == self.profile.value), None)
        if target is None:
            return None
        return next((row for row in target["efforts"]
                     if row["value"] == self.effort.value), None)

    @staticmethod
    def _wire_text(row) -> str:
        """What the adapter will actually put on the wire for this level."""
        if not isinstance(row, dict):
            return ""
        state = row.get("state")
        wire = row.get("wire") or []
        if state == "sent" and wire:
            return "将发送 " + "、".join(f"{item['key']}={item['value']}" for item in wire)
        if state == "budget":
            return "将以 token 预算表达（非等级名）"
        if state == "omitted":
            return "请求不附带思考参数"
        return ""

    def preview_effort(self):
        if self.busy:
            return
        self.feedback.refresh(self.effort.value or "unknown")
        if self._valid_effort():
            wire = self._wire_text(self._effort_row())
            label = EFFORTS.get(self.effort.value, self.effort.value)
            self.preview_note.value = (f"选择预览 · {label} · {wire} · 尚未保存"
                                       if wire else f"选择预览 · {label} · 尚未保存")
        else:
            row = self._effort_row()
            reason = (row or {}).get("reason") or ""
            self.preview_note.value = (f"该档位不会被发送：{reason}" if reason
                                       else "请选择该模型可用的思考档位")
        self._enabled()
        self.page.update()

    def _accept(self, catalog):
        if not isinstance(catalog, dict) or catalog.get("stage") != self.stage.value:
            raise ValueError("模型目录与所选阶段不匹配，请刷新")
        self.snapshot = catalog
        self.profile.options = [ft.dropdown.Option(row["id"], f"{row['label']} · {row['model'] or '未填写模型'}") for row in catalog["items"]]
        self.profile.value = catalog["active_id"]
        self.select_profile(update=False)
        active = next((row for row in catalog["items"] if row["id"] == catalog["active_id"]), {})
        self.button.content = f"科研模型 · {str(active.get('label') or '默认')[:16]}"
        style_inputs(self.body)

    def select_profile(self, update=True):
        target = next((row for row in (self.snapshot or {}).get("items", []) if row["id"] == self.profile.value), None)
        if target is None:
            return
        accepted = [row for row in target["efforts"] if row["enabled"]]
        self.effort.options = [ft.dropdown.Option(row["value"], EFFORTS.get(row["value"], row["value"])) for row in accepted]
        values = {row["value"] for row in accepted}
        self.effort.value = target["reasoning_effort"] if target["reasoning_effort"] in values else None
        self.feedback.refresh(target["reasoning_effort"])
        stored_wire = self._wire_text(self._effort_row())
        self.preview_note.value = (
            f"已存档位 · {EFFORTS.get(self.effort.value, self.effort.value)}"
            + (f" · {stored_wire}" if stored_wire else "")
            if self.effort.value is not None else "已存档位不可用，请重新选择")
        self.summary.value = f"{target['model'] or '模型未配置'} · {PROTOCOLS.get(target['protocol'], target['protocol'])} · {target['label']}"
        omitted = list(dict.fromkeys(row['reason'].replace('thinking_style', '思考方式').replace('reasoning_effort', '思考强度') for row in target["efforts"] if not row["enabled"]))
        self.notes.value = "档位仅经本地适配器校验，供应商能力尚未验证。" + ("\n" + "\n".join(omitted[:2]) if omitted else "")
        self.message.value = "" if target["reasoning_effort"] in values else "当前强度无法通过适配器校验，保存时请选择可用档位。"
        self._enabled()
        if update:
            self.page.update()

    def _request(self, request, accept):
        self.sequence += 1
        sequence, stage = self.sequence, self.stage.value
        self.busy = True
        self._enabled()
        self.page.update()
        def work():
            try:
                result = self.api(request)
                if not self.opened or sequence != self.sequence or stage != self.stage.value:
                    return
                if result.get("ok") is not True:
                    raise ValueError(result.get("error") or "模型配置未确认保存")
                accept(result["catalog"])
            except Exception as error:
                if self.opened and sequence == self.sequence:
                    self.message.value, self.message.color = redact(str(error)), p.DANGER
            finally:
                if self.opened and sequence == self.sequence:
                    self.busy = False
                    self._enabled()
                    self.page.update()
        self.page.run_thread(work)

    def load(self):
        self.snapshot = None
        self.feedback.clear_feedback()
        self.message.value, self.message.color = "读取模型配置…", p.TEXT_2
        self._request({"cmd": "model_control_catalog", "stage": self.stage.value}, self._accept)

    def apply(self):
        if self.busy or not self.snapshot or not self._valid_effort():
            return
        before = next(row["reasoning_effort"] for row in self.snapshot["items"]
                      if row["id"] == self.profile.value)
        request = {"cmd": "model_control_apply", "stage": self.stage.value, "profile_id": self.profile.value,
                   "effort": self.effort.value, "expected_revision": self.snapshot["revision"]}
        def accept(catalog):
            self._accept(catalog)
            if self.effort.value != before and self.effort.value == request["effort"]:
                self.feedback.confirm()
            self.preview_note.value = f"已保存 · {EFFORTS.get(self.effort.value, self.effort.value)} · 用于后续请求"
            self.message.value, self.message.color = "已保存 · 后续科研请求使用所选配置", p.SUCCESS
            self.on_saved()
        self._request(request, accept)
