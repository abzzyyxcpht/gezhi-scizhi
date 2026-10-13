"""Opt-in follow-up queue controls; execution authority remains in the runtime."""
from __future__ import annotations

import time
import uuid

import flet as ft

from ..bridge import handle
from ..workbench import redact
from .theme import palette as p, style_inputs

FLAG = 'experimental_message_queue'
ACTIVE = {'queued', 'dispatching', 'needs_review'}
LABELS = {'queued': '等待投递', 'dispatching': '正在投递', 'needs_review': '需要重新核对',
          'delivered': '已交给会话', 'removed': '已移除'}


class SessionQueuePanel:
    def __init__(self, page, request, selected, *, epoch=lambda: 0, api=None,
                 clock=None, on_draft_changed=None, draft_revision=lambda: None):
        self.page, self.request, self.selected, self.epoch = page, request, selected, epoch
        self.api, self.clock = api or handle, clock or time.monotonic
        self.on_draft_changed = on_draft_changed
        self.draft_revision = draft_revision
        self.queue, self.enabled = None, None
        self._view, self._sequence = None, 0
        self._reading = self._mutating = self._feature_busy = False
        self._last_read = float('-inf')
        self._pending_submission = None
        self._rows_key = None
        self.add_button = ft.OutlinedButton('加入后续', icon=ft.Icons.PLAYLIST_ADD, disabled=True,
            tooltip='加入当前会话的后续任务；等待当前轮结束，不绕过审批。', on_click=lambda e: self.add())
        self.enable_button = ft.TextButton('启用后续队列（实验）', visible=False, on_click=lambda e: self.set_enabled(True))
        self.toggle = ft.Switch(label='实验：后续任务队列', value=False,
            on_change=lambda e: self.set_enabled(bool(self.toggle.value)))
        self.heading = ft.Text('后续任务', size=12, color=p.TEXT, weight=ft.FontWeight.W_600, expand=True)
        self.pause_button = ft.TextButton('暂停后续', on_click=lambda e: self.command('queue_pause'))
        self.resume_button = ft.TextButton('恢复后续', visible=False, on_click=lambda e: self.command('queue_resume'))
        self.reason = ft.Text('', size=11, color=p.TEXT_2, max_lines=3,
                              overflow=ft.TextOverflow.ELLIPSIS)
        self.rows = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO,
                              horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.rows_box = ft.Container(content=self.rows, height=150)
        self.items_view = ft.ExpansionTile(title='查看后续任务', controls=[self.rows_box], expanded=False,
            dense=True, min_tile_height=28, tile_padding=0, maintain_state=True,
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
            shape=ft.RoundedRectangleBorder(radius=0), collapsed_shape=ft.RoundedRectangleBorder(radius=0))
        self.dock = ft.Column([ft.Row([self.heading, self.pause_button, self.resume_button], spacing=6),
                              self.reason, self.items_view], spacing=3, visible=False)
        self.notice = ft.Text('', size=12, color=p.TEXT_2, visible=False, selectable=True)
        self.content = ft.Column([self.enable_button, self.dock, self.notice], spacing=3)
        style_inputs(self.content, self.toggle)

    def _key(self):
        return self.selected(), self.epoch()

    def _valid(self, view, sequence):
        return self._view == view == self._key() and sequence == self._sequence

    def _sync(self):
        view = self._key()
        if view == self._view:
            return False
        self._view, self._sequence = view, self._sequence + 1
        self.queue, self.enabled = None, None
        self._reading = self._mutating = False
        self._last_read, self._rows_key = float('-inf'), None
        self.rows.controls = []
        self.notice.value, self.notice.visible = '', False
        return True

    @staticmethod
    def _set(control, key, value):
        if getattr(control, key) == value:
            return False
        setattr(control, key, value)
        return True

    def _message(self, message, error=False):
        self.notice.value = str(redact(str(message)))
        self.notice.color, self.notice.visible = p.DANGER if error else p.TEXT_2, bool(message)

    def _render(self):
        value = self.queue or {}
        items = [item for item in value.get('items', []) if item.get('state') in ACTIVE]
        busy = self._mutating or self._feature_busy
        paused = bool(value.get('paused') or value.get('resume_required'))
        reason = str(value.get('pause_reason') or '')
        if paused and not reason:
            reason = '后续任务已暂停，核对后可显式恢复。'
        if not self.enabled and items:
            reason = '实验队列已关闭，存量保留；开启后仍需按显示的恢复条件处理。' + (' ' + reason if reason else '')
        if any(item.get('state') == 'needs_review' for item in items):
            reason += ' 需要核对的条目须先放弃，再按当前材料重新加入。'
        if items:
            reason += ' 暂停后续不会取消当前运行。'
        changes = [
            (self.add_button, 'disabled', not bool(self.enabled and self.selected() and (self.request.value or '').strip()) or busy),
            (self.enable_button, 'visible', self.enabled is False),
            (self.enable_button, 'disabled', busy), (self.toggle, 'value', bool(self.enabled)),
            (self.toggle, 'disabled', self._feature_busy),
            (self.dock, 'visible', bool(items or paused)),
            (self.heading, 'value', f'后续任务 {len(items)}'),
            (self.reason, 'value', reason.strip()), (self.reason, 'visible', bool(reason.strip())),
            (self.pause_button, 'visible', bool(items) and not paused and bool(self.enabled)),
            (self.resume_button, 'visible', paused and bool(self.enabled)),
            (self.pause_button, 'disabled', busy), (self.resume_button, 'disabled', busy),
        ]
        changed = False
        for control, name, value in changes:
            changed = self._set(control, name, value) or changed
        signature = (self.selected(), (self.queue or {}).get('revision'), busy,
                     tuple((row.get('id'), row.get('state'), row.get('request'), row.get('error')) for row in items))
        if signature != self._rows_key:
            self._rows_key = signature
            self.rows.controls = []
            queued = [row['id'] for row in items if row.get('state') == 'queued']
            for row in items:
                ident, sid, state = row['id'], self.selected(), row.get('state')
                actions = [ft.TextButton('详情', on_click=lambda e, r=dict(row): self.show_item(r))]
                if state in {'queued', 'needs_review'}:
                    actions.append(ft.TextButton('放弃' if state == 'needs_review' else '移除', disabled=busy,
                        on_click=lambda e, i=ident, s=sid, review=state == 'needs_review': self.remove(i, session_id=s, discard=review)))
                if state == 'queued':
                    for direction, icon, label in [(-1, ft.Icons.ARROW_UPWARD, '前移'), (1, ft.Icons.ARROW_DOWNWARD, '后移')]:
                        position = queued.index(ident) + direction
                        actions.append(ft.IconButton(icon, tooltip=label, icon_size=15,
                            disabled=busy or not 0 <= position < len(queued),
                            on_click=lambda e, i=ident, d=direction, s=sid: self.move(i, d, session_id=s)))
                self.rows.controls.append(ft.Column([
                    ft.Text(f'{LABELS.get(state, state)} · {str(row.get("request") or "")}', size=12,
                        color=p.WARN if state == 'needs_review' else p.TEXT_2, max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS, selectable=True),
                    ft.Text(str(row.get('error') or ''), size=11, color=p.WARN, visible=bool(row.get('error')),
                        max_lines=3, overflow=ft.TextOverflow.ELLIPSIS, tooltip=str(row.get('error') or '')),
                    ft.Row(actions, spacing=2, wrap=True),
                ], spacing=2))
            self.rows_box.height = min(220, max(70, len(items) * 76))
            if len(items) == 1:
                self.items_view.expanded = True
            style_inputs(self.rows)
            changed = True
        return changed

    def draft_changed(self):
        changed = self._sync()
        if self._render() or changed:
            self.page.update()

    def _accept(self, response):
        if response.get('ok') is False:
            raise ValueError(response.get('error') or '队列操作失败')
        value = response.get('queue')
        if value is not None:
            if not isinstance(value, dict) or value.get('session_id') != self.selected():
                raise ValueError('队列会话不匹配')
            self.queue, self.enabled = value, bool(value.get('enabled'))
        elif 'settings' in response:
            self.enabled = bool((response.get('settings') or {}).get(FLAG))

    def refresh(self, *, force=False):
        changed = self._sync()
        if self._render() or changed:
            self.page.update()
        now = self.clock()
        if self._reading or self._mutating or self._feature_busy or (not force and now - self._last_read < 1.0):
            return
        self._last_read, self._reading = now, True
        self._sequence += 1
        view, sequence = self._view, self._sequence
        request = ({'cmd': 'queue_list', 'session_id': view[0]} if view[0] else {'cmd': 'settings_get'})
        def work():
            try:
                result = self.api(request)
                if not self._valid(view, sequence):
                    return
                self._reading = False
                self._accept(result)
                if self._render():
                    self.page.update()
            except Exception as error:  # noqa: BLE001
                if self._valid(view, sequence):
                    self._reading = False
                    self._message(error, True)
                    self._render()
                    self.page.update()
        self.page.run_thread(work)

    def _run(self, request, *, success=None, feature=False):
        if self._mutating or self._feature_busy:
            return
        self._mutating = True
        self._feature_busy = feature
        self._reading = False
        self._sequence += 1
        view, sequence = self._view, self._sequence
        self._render()
        self.page.update()
        def work():
            try:
                response = self.api(request)
                if feature and response.get('ok'):
                    response = self.api({'cmd': 'queue_list', 'session_id': view[0]} if view[0] else {'cmd': 'settings_get'})
                if not self._valid(view, sequence):
                    return
                self._mutating = False
                self._accept(response)
                self._message('设置已保存。' if feature else '队列已更新。')
                if success:
                    success(response)
            except Exception as error:  # noqa: BLE001
                if self._valid(view, sequence):
                    self._mutating = False
                    self._message(error, True)
            finally:
                if feature:
                    self._feature_busy = False
                if self._valid(view, sequence):
                    self._render()
                    self.page.update()
        self.page.run_thread(work)

    def set_enabled(self, enabled):
        self._sync()
        self._run({'cmd': 'settings_save', 'settings': {FLAG: bool(enabled)}}, feature=True)

    def add(self):
        changed = self._sync()
        if self._render() or changed:
            self.page.update()
        if self.add_button.disabled:
            return
        sid, text = self.selected(), self.request.value or ''
        revision = self.draft_revision()
        if not self._pending_submission or self._pending_submission[:2] != (sid, text):
            self._pending_submission = (sid, text, 'gui-' + uuid.uuid4().hex)
        request_id = self._pending_submission[2]
        def accepted(response):
            ticket = response.get('ticket')
            if not isinstance(ticket, dict) or not ticket.get('id'):
                raise ValueError('入队结果未返回任务标识，草稿已保留；可重试核对。')
            receipt = response.get('queue') or {}
            matched = any(isinstance(row, dict) and row.get('id') == ticket['id']
                          and row.get('request_id') == request_id for row in receipt.get('items', []))
            if receipt.get('session_id') != sid or ticket.get('request_id') != request_id or not matched:
                raise ValueError('入队回执未匹配本次会话、请求与票据；草稿和重试标识已保留。')
            self._pending_submission = None
            same_draft = self.request.value == text and self.draft_revision() == revision
            new_draft = not same_draft and bool((self.request.value or '').strip())
            if same_draft:
                self.request.value = ''
                if self.on_draft_changed:
                    self.on_draft_changed()
            state = ticket.get('state', 'queued')
            self._message('后续请求已保存：' + LABELS.get(state, state) + '。'
                          + ('输入区的新草稿尚未发送。' if new_draft else ''))
        self._run({'cmd': 'queue_add', 'session_id': sid, 'request': text, 'queue_request_id': request_id}, success=accepted)

    def command(self, command, *, success=None, **payload):
        if self._key() != self._view or not self.selected():
            return
        self._run({'cmd': command, 'session_id': self.selected(), **payload}, success=success)

    def remove(self, ticket_id, *, session_id=None, discard=False):
        if session_id is not None and session_id != self.selected():
            return
        sid = self.selected()
        def removed(response):
            ticket, receipt = response.get('ticket') or {}, response.get('queue') or {}
            pending = self._pending_submission
            if (pending and pending[0] == sid and receipt.get('session_id') == sid
                    and ticket.get('id') == ticket_id and ticket.get('request_id') == pending[2]
                    and ticket.get('state') == 'removed'):
                self._pending_submission = None
        self.command('queue_discard' if discard else 'queue_remove', ticket_id=ticket_id, success=removed)

    def move(self, ticket_id, direction, *, session_id=None):
        if session_id is not None and session_id != self.selected():
            return
        ordered = [row['id'] for row in (self.queue or {}).get('items', []) if row.get('state') == 'queued']
        if ticket_id not in ordered:
            return
        index, other = ordered.index(ticket_id), ordered.index(ticket_id) + direction
        if not 0 <= other < len(ordered):
            return
        ordered[index], ordered[other] = ordered[other], ordered[index]
        self.command('queue_reorder', ordered_ids=ordered)

    def show_item(self, row):
        mode = '离线示例' if row.get('offline') else '在线执行'
        self.page.show_dialog(ft.AlertDialog(title=ft.Text('后续任务详情'), content=ft.Container(width=600, height=380,
            content=ft.Column([ft.Text(str(row.get('request') or ''), selectable=True, width=550),
                ft.Text(f'{mode} · {row.get("agent", "")} · {row.get("mode", "")}', size=12),
                ft.Text(str(row.get('error') or ''), color=p.WARN, width=550)], expand=True, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton('关闭', on_click=lambda e: self.page.pop_dialog())]))
