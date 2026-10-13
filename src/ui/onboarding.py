"""Small first-run guidance, with explicit offline demos and no automatic sends."""
from __future__ import annotations

import flet as ft

from ..bridge import handle
from ..workbench import redact
from .theme import palette as p, style_inputs


class OnboardingPanel:
    def __init__(self, page, request, *, open_models, open_demo, api=None,
                 on_ready=None, on_example=None, on_demo_state=None, selected=lambda: None):
        self.page, self.request = page, request
        self.open_models, self.open_demo = open_models, open_demo
        self.api = api or handle
        self.on_ready, self.on_example, self.on_demo_state = on_ready, on_example, on_demo_state
        self.selected, self._manual_expanded = selected, None
        self.readiness = None
        self.demo_pending = False
        self.heading = ft.Text('开始使用', size=13, color=p.TEXT, weight=ft.FontWeight.W_600,
                               expand=True, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.detail = ft.Text('正在读取本地配置…', size=12, color=p.TEXT_2,
                              max_lines=3, overflow=ft.TextOverflow.ELLIPSIS)
        self.offline_note = ft.Text('', size=11, color=p.TEXT_3, max_lines=3,
                                    overflow=ft.TextOverflow.ELLIPSIS)
        self.notice = ft.Text('', size=12, color=p.TEXT_2, visible=False,
                              max_lines=3, overflow=ft.TextOverflow.ELLIPSIS)
        self.settings_button = ft.TextButton('配置模型', icon=ft.Icons.TUNE,
            on_click=lambda e: self.open_models(), style=ft.ButtonStyle(color=p.ACCENT_TEXT))
        self.example_button = ft.TextButton('填入示例请求', icon=ft.Icons.EDIT_NOTE, disabled=True,
            on_click=lambda e: self.fill_example(), style=ft.ButtonStyle(color=p.TEXT_2))
        self.demo_button = ft.OutlinedButton('体验离线研究', icon=ft.Icons.SCIENCE_OUTLINED, disabled=True,
            on_click=lambda e: self.start_demo())
        self.body = ft.Column([
            ft.Row([self.heading, self.settings_button], spacing=8), self.detail,
            ft.Row([self.example_button, self.demo_button], spacing=8, wrap=True),
            self.offline_note, self.notice,
        ], spacing=4)
        self.disclosure = ft.ExpansionTile(title=ft.Text('开始使用', size=12, color=p.TEXT_2),
            controls=[self.body], expanded=False, dense=True, min_tile_height=30, tile_padding=0,
            maintain_state=True, on_change=self._expansion_changed,
            shape=ft.RoundedRectangleBorder(radius=0), collapsed_shape=ft.RoundedRectangleBorder(radius=0))
        self.content = self.disclosure
        style_inputs(self.content)

    def _expansion_changed(self, event):
        raw = getattr(event, 'data', None)
        self._manual_expanded = raw == 'true' if raw in {'true', 'false'} else bool(self.disclosure.expanded)

    def update_context(self):
        if self._manual_expanded is None:
            model = (self.readiness or {}).get('model') or {}
            self.disclosure.expanded = (not self.selected() and model.get('status') != 'configured') or model.get('status') == 'invalid'
        else:
            self.disclosure.expanded = self._manual_expanded

    def refresh(self):
        try:
            response = self.api({'cmd': 'product_readiness'})
            if not response.get('ok') or not isinstance(response.get('readiness'), dict):
                raise ValueError(response.get('error') or '运行配置暂不可读')
            row = response['readiness']
            self.readiness = row
            model = row.get('model') or {}
            if row.get('agent_mode') == 'model':
                self.heading.value = '新会话：模型模式'
                self.detail.value = (f'{model.get("model") or "已保存模型配置"} · 尚未验证连接。'
                                     '可在模型设置中检测；已有会话沿用原执行方式。')
            else:
                self.heading.value = '新会话：规则模式'
                fallback = {'disabled': '模型增强已关闭。', 'invalid': '模型配置需要检查。',
                            'not_configured': '尚未配置模型。'}
                reason = str(model.get('reason') or fallback.get(model.get('status'), '当前没有可执行的模型配置。'))
                self.detail.value = str(redact(reason)) + ' 可先体验离线研究，或配置模型后开始新会话。'
            research = row.get('research') or {}
            self.offline_note.value = str(research.get('notice') or '离线体验使用内置合成样例，不可作为真实科研证据。')
            self.demo_button.disabled = self.demo_pending or not bool(research.get('offline_available'))
            self.example_button.disabled = not bool((row.get('suggestions') or {}).get('example_request'))
            if self.on_ready:
                self.on_ready(row)
        except Exception as error:  # noqa: BLE001 — no guessed connectivity or availability
            self.readiness = None
            self.heading.value = '无法读取运行配置'
            self.detail.value = str(redact(str(error))) + '；请打开模型设置检查后重试。'
            self.demo_button.disabled = self.example_button.disabled = True
            if self.on_ready:
                self.on_ready(None)
        self.update_context()
        self.page.update()

    def _notice(self, text, *, error=False):
        self.notice.value = str(redact(str(text)))
        self.notice.color, self.notice.visible = p.DANGER if error else p.TEXT_2, True

    def fill_example(self):
        if (self.request.value or '').strip():
            self._notice('输入中已有草稿，已保留；清空后可填入示例。')
        else:
            example = ((self.readiness or {}).get('suggestions') or {}).get('example_request')
            if not example:
                self._notice('运行配置暂不可读，请打开模型设置检查后重试。')
            else:
                self.request.value = str(example)
                self._notice('示例已填入，请检查后执行；尚未发送请求。')
                if self.on_example:
                    self.on_example()
        self.page.update()

    def _demo_state(self, pending):
        self.demo_pending = pending
        self.demo_button.disabled = pending or not bool(((self.readiness or {}).get('research') or {}).get('offline_available'))
        if self.on_demo_state:
            self.on_demo_state(pending)

    def start_demo(self):
        if self.demo_pending or self.demo_button.disabled:
            return
        self._demo_state(True)
        self._notice('正在启动离线示例…')
        self.page.update()

        def work():
            try:
                response = self.api({'cmd': 'product_demo'})
                if not response.get('ok'):
                    raise ValueError(response.get('error') or '离线示例启动失败')
                task_id = response.get('task_id')
                if not isinstance(task_id, str) or not task_id or response.get('offline') is not True:
                    raise ValueError('示例启动结果未确认离线任务，请检查运行记录。')
                self._notice(response.get('notice') or '离线示例已启动。')
                self.open_demo(task_id)
            except Exception as error:  # noqa: BLE001 — explicit clicks get an actionable error
                self._notice(error, error=True)
                self.page.show_dialog(ft.SnackBar(ft.Text(self.notice.value, max_lines=3)))
            finally:
                self._demo_state(False)
                self.page.update()

        self.page.run_thread(work)
