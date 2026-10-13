"""Explicit same-turn corrections, preserving draft and expected-turn identity."""
from __future__ import annotations

import time
import uuid
import flet as ft

from ..bridge import handle
from ..workbench import redact
from .theme import palette as p, style_inputs


class SessionSteeringPanel:
    def __init__(self, page, request, selected, *, epoch=lambda: 0, api=None, on_continue=None,
                 on_draft_changed=None, draft_revision=lambda: None):
        self.page, self.request, self.selected, self.epoch = page, request, selected, epoch
        self.api, self.on_continue, self.on_draft_changed = api or handle, on_continue, on_draft_changed
        self.draft_revision = draft_revision
        self.view, self.value, self.pending = None, {}, None
        self.busy, self.reading, self.sequence, self.last_read = False, False, 0, 0.0
        self.add_button = ft.OutlinedButton('补充本轮', icon=ft.Icons.EDIT_NOTE, visible=False, disabled=True,
            tooltip='在当前模型回合的下一执行边界补充要求；不会自动批准工具。', on_click=lambda e: self.add())
        self.toggle = ft.Switch(label='实验：运行中干预', value=False, on_change=lambda e: self.set_enabled())
        self.notice = ft.Text('', size=12, color=p.TEXT_2, visible=False, selectable=True)
        self.status = ft.Text('', size=12, color=p.TEXT_2, visible=False, selectable=True)
        self.resume_button = ft.TextButton('继续处理补充', visible=False,
            on_click=lambda e: self.on_continue() if self.on_continue and self._identity() == self.view else None)
        self.content = ft.Column([self.status, self.notice, self.resume_button], spacing=2, visible=False)
        style_inputs(self.toggle)

    def _identity(self):
        return self.selected(), self.epoch()

    def _sync(self):
        if self.view != self._identity():
            self.view, self.value = self._identity(), {}
            self.sequence += 1
            self.busy, self.reading, self.last_read = False, False, 0.0
            self.notice.value, self.notice.visible = '', False

    def _render(self):
        self.add_button.visible = bool(self.value.get('enabled'))
        self.add_button.disabled = (self.busy or not self.value.get('turn_id') or self.value.get('state') != 'open'
            or self.value.get('status') not in {'running', 'waiting_approval', 'waiting_task', 'waiting_children', 'paused'}
            or not (self.request.value or '').strip())
        self.toggle.value = bool(self.value.get('enabled'))
        self.toggle.disabled = self.busy
        pending = sum(item['state'] == 'pending' for item in self.value.get('items', []))
        self.resume_button.visible = bool(pending and self.value.get('resume_required'))
        self.resume_button.disabled = self.busy
        if pending:
            self.status.value = f'本轮补充 {pending} 条待处理。' + ('请显式继续；原审批不会自动放行。' if self.value.get('resume_required') else '将在模型或工具边界生效。')
        else:
            counts = {state: sum(item['state'] == state for item in self.value.get('items', [])) for state in ('applied', 'expired')}
            self.status.value = f'本轮补充：已写入上下文 {counts["applied"]} · 已过期 {counts["expired"]}'
        self.status.visible = bool(self.value.get('items'))
        self.content.visible = self.status.visible or self.notice.visible

    def draft_changed(self):
        self._sync()
        self._render()
        self.page.update()

    def _run(self, operation, accepted=None):
        self._sync()
        if self.busy:
            return
        self.busy = True
        self.reading = False
        self.sequence += 1
        key, sequence = self.view, self.sequence
        self._render()
        self.page.update()
        def work():
            try:
                reply = operation()
                if key != self._identity() or sequence != self.sequence:
                    return
                if reply.get('ok') is not True:
                    raise ValueError(reply.get('error') or '未确认补充结果，草稿已保留')
                value = reply.get('steering')
                if value is not None:
                    if value.get('session_id') != key[0]:
                        raise ValueError('回合补充的会话身份不匹配')
                    self.value = value
                else:
                    self.value = {'enabled': (reply.get('settings') or {}).get('experimental_inflight_steering', False)}
                if accepted:
                    accepted(reply)
            except Exception as error:  # noqa: BLE001 — keep unconfirmed drafts and idempotency identity
                if key == self._identity() and sequence == self.sequence:
                    self.notice.value, self.notice.visible = str(redact(str(error))), True
            finally:
                if key == self._identity() and sequence == self.sequence:
                    self.busy = False
                    self._render()
                    self.page.update()
        self.page.run_thread(work)

    def refresh(self, *, force=False):
        changed = self._identity() != self.view
        self._sync()
        if changed:
            self._render()
            self.page.update()
        if self.busy or self.reading or (not force and time.monotonic() - self.last_read < 1):
            return
        self.last_read = time.monotonic()
        sid = self.selected()
        self.sequence += 1
        key, sequence = self.view, self.sequence
        self.reading = True
        def work():
            try:
                reply = self.api({'cmd': 'steer_status', 'session_id': sid} if sid else {'cmd': 'settings_get'})
                if key != self._identity() or sequence != self.sequence:
                    return
                if reply.get('ok') is not True:
                    raise ValueError(reply.get('error') or '回合状态暂不可读')
                value = reply.get('steering') if sid else {'enabled': (reply.get('settings') or {}).get('experimental_inflight_steering', False)}
                if not isinstance(value, dict) or (sid and value.get('session_id') != sid):
                    raise ValueError('回合状态身份不匹配')
                if value != self.value:
                    self.value = value
                    self._render()
                    self.page.update()
            except Exception as error:  # noqa: BLE001
                if key == self._identity() and sequence == self.sequence:
                    self.notice.value, self.notice.visible = str(redact(str(error))), True
                    self._render()
                    self.page.update()
            finally:
                if key == self._identity() and sequence == self.sequence:
                    self.reading = False
        self.page.run_thread(work)

    def set_enabled(self):
        enabled, sid = bool(self.toggle.value), self.selected()
        def operation():
            result = self.api({'cmd': 'settings_save', 'settings': {'experimental_inflight_steering': enabled}})
            if result.get('ok') is not True:
                return result
            return self.api({'cmd': 'steer_status', 'session_id': sid} if sid else {'cmd': 'settings_get'})
        self._run(operation)

    def add(self):
        self._sync()
        self._render()
        if self.add_button.disabled:
            return
        sid, text, turn = self.selected(), self.request.value, self.value['turn_id']
        revision = self.draft_revision()
        if self.pending is None or self.pending[:2] != (sid, text):
            self.pending = sid, text, turn, uuid.uuid4().hex
        elif self.pending[2] != turn:
            self.notice.value, self.notice.visible = '回合已改变；请修改草稿后重新提交，或使用下一轮队列。', True
            self.page.update()
            return
        request_id = self.pending[3]
        def accepted(reply):
            ticket = reply.get('ticket') or {}
            if (reply['steering'].get('turn_id') != turn or ticket.get('request_id') != request_id
                    or not ticket.get('id') or ticket.get('state') not in {'pending', 'applied', 'expired'}):
                raise ValueError('未收到匹配当前回合的补充回执，草稿已保留')
            if ticket['state'] == 'expired':
                raise ValueError('该补充已过期，草稿已保留；请核对回合后修改重发')
            self.pending = None
            if self.request.value == text and self.draft_revision() == revision:
                self.request.value = ''
                if self.on_draft_changed:
                    self.on_draft_changed()
            self.notice.value, self.notice.visible = '补充已接收；待处理不等于模型已采用。', True
        self._run(lambda: self.api({'cmd': 'steer_add', 'session_id': sid, 'expected_turn_id': turn,
                                   'steer_request_id': request_id, 'request': text}), accepted)
