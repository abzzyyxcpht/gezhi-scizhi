"""Local, scoped draft lifecycle; execution and approval remain separate."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock

import flet as ft

from ..composer_drafts import DraftConflict, DraftStore
from .theme import palette as p, style_inputs


@dataclass
class _Draft:
    scope: str
    row: dict
    text: str = ''
    agent: str = 'research'
    mode: str = 'agent'
    generation: int = 0
    dirty: bool = False
    error: str = ''
    unreadable: bool = False
    pending: int = 0


@dataclass
class DraftTicket:
    snapshots: list = field(default_factory=list)
    finished: bool = False


class ComposerDrafts:
    def __init__(self, page, request, agent, mode, *, store=None, changed=None):
        self.page, self.request, self.agent, self.mode = page, request, agent, mode
        self.store, self.changed = store or DraftStore(), changed
        self.scope = None
        self._drafts = OrderedDict()
        self._lock = RLock()
        self._timer = None
        self._closed = False
        self._status_key = None
        self.status = ft.Text('输入会自动保存到本机', size=11, color=p.TEXT_3, expand=True,
                              max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.clear_button = ft.TextButton('清除草稿', visible=False, on_click=lambda e: self.clear())
        self.resolve_button = ft.TextButton('检查草稿', visible=False, on_click=lambda e: self.compare())
        self.content = ft.Row([self.status, self.resolve_button, self.clear_button], spacing=4)

    @staticmethod
    def key(session_id=None, project_id=''):
        return f'session:{session_id}' if session_id else f'project:{project_id or ""}'

    def _load(self, scope, defaults=None):
        if scope in self._drafts:
            self._drafts.move_to_end(scope)
            draft = self._drafts[scope]
            if not draft.dirty and not draft.error and not draft.pending:
                try:
                    row = self.store.load(scope)
                    if row['revision'] != draft.row['revision']:
                        draft.row = row
                        draft.text, draft.agent, draft.mode = row['text'], row['agent'], row['mode']
                        draft.generation += 1
                except Exception as exc:
                    # Retain the last readable version, never blank an editor
                    # just because another window or disk operation failed.
                    draft.error, draft.unreadable = f'无法读取原草稿：{exc}', True
            return draft
        try:
            row = self.store.load(scope)
            if not row['revision'] and defaults:
                row = {**row, **defaults}
            draft = _Draft(scope, row, row['text'], row['agent'], row['mode'])
        except Exception as exc:  # A damaged optional draft must not disable execution.
            draft = _Draft(scope, {'revision': ''}, error=f'无法读取原草稿：{exc}', unreadable=True)
        self._drafts[scope] = draft
        # Never evict unsaved input or an execution's submission identity.
        for key, candidate in list(self._drafts.items()):
            if len(self._drafts) <= 64:
                break
            if key not in {scope, self.scope} and not candidate.dirty and not candidate.error and not candidate.pending:
                del self._drafts[key]
        return draft

    def _render(self):
        if self._closed or self.scope is None:
            return
        item = self._drafts[self.scope]
        label = item.error or ('草稿待保存…' if item.dirty else '草稿已保存到本机' if item.text else '输入会自动保存到本机')
        key = (label, bool(item.text), bool(item.error))
        if key == self._status_key:
            return
        self._status_key = key
        self.status.value = label
        self.status.color = p.WARN if item.error else p.TEXT_3
        self.clear_button.visible = bool(item.text)
        self.resolve_button.visible = bool(item.error)
        self.page.update()

    def _apply(self, item):
        if self._closed:
            return
        self.request.value, self.agent.value, self.mode.value = item.text, item.agent, item.mode
        if self.changed:
            self.changed()
        self._render()

    def _capture_values(self, touched=False):
        if self.scope is None:
            return None
        item = self._drafts[self.scope]
        values = (self.request.value or '', self.agent.value or 'research', self.mode.value or 'agent')
        if values == (item.text, item.agent, item.mode) and not touched:
            return None
        item.text, item.agent, item.mode = values
        item.generation += 1
        item.dirty = True
        return item

    def capture(self, *, touched=False):
        with self._lock:
            if self._closed or self.scope is None:
                return
            item = self._capture_values(touched)
            if item is None:
                return
            if self._timer:
                self._timer.cancel()
            try:
                self._timer = self.page.run_task(self._debounced, item.scope, item.generation)
            except RuntimeError:
                # Disconnect may have closed Flet's scheduler; close/flush still
                # writes the captured input synchronously under this lock.
                self._timer = None
            self._render()

    def revision(self):
        with self._lock:
            self.capture()
            return (self.scope, self._drafts[self.scope].generation) if self.scope is not None else None

    async def _debounced(self, scope, generation, delay=.4):
        await asyncio.sleep(delay)
        await asyncio.to_thread(self._save_if_current, scope, generation)

    def _save_if_current(self, scope, generation):
        with self._lock:
            item = self._drafts.get(scope)
            if self._closed or item is None or item.generation != generation:
                return
            self._save(item)

    def _save(self, item):
        if not item.dirty:
            return not item.error
        if item.unreadable:
            self._render()
            return False
        try:
            item.row = self.store.save(item.scope, item.text, agent=item.agent, mode=item.mode,
                                       expected_revision=item.row['revision'])
            item.dirty, item.error = False, ''
        except DraftConflict:
            item.error = '其他窗口更新了草稿；当前输入已保留，请检查两个版本。'
        except Exception as exc:
            item.error = f'草稿尚未保存：{exc}'
        self._render()
        return not item.error

    def flush(self):
        with self._lock:
            self.capture()
            if self.scope is None:
                return True
            return self._save(self._drafts[self.scope])

    def select(self, session_id=None, project_id='', *, defaults=None):
        with self._lock:
            scope = self.key(session_id, project_id)
            if scope == self.scope:
                self.capture()
                self._apply(self._load(scope, defaults))
                return
            self.flush()
            item = self._load(scope, defaults)
            self.scope = scope
            self._apply(item)

    def submitted(self):
        with self._lock:
            self.flush()
            item = self._drafts[self.scope]
            item.pending += 1
            return DraftTicket([(item, item.generation, item.text)])

    def adopt_session(self, session_id, ticket):
        """Move the unsent composer to its freshly-created chat, without sending it."""
        with self._lock:
            self.capture()
            source, generation, sent = ticket.snapshots[0]
            if self.scope != source.scope or ticket.finished:
                return
            self._save(source)
            target = self._load(self.key(session_id))
            # A newly-created ID should be empty. Preserve an unexpected existing
            # target draft instead of overwriting another window's input.
            if target.text or target.error:
                self.scope = target.scope
                self._apply(target)
                return
            target.text, target.agent, target.mode = source.text, source.agent, source.mode
            target.generation += 1
            target.dirty = True
            copied = self._save(target)
            if source.generation == generation and source.text == sent:
                target.pending += 1
                ticket.snapshots.append((target, target.generation, sent))
            if copied and not source.error:
                source.text = ''
                source.generation += 1
                source.dirty = True
                self._save(source)
            self.scope = target.scope
            self._apply(target)

    def finish(self, ticket, *, accepted):
        with self._lock:
            if ticket.finished:
                return
            self.capture()
            ticket.finished = True
            for item, generation, sent in ticket.snapshots:
                item.pending = max(0, item.pending - 1)
                if not accepted or item.generation != generation or item.text != sent:
                    continue
                item.text = ''
                item.generation += 1
                item.dirty = True
                self._save(item)
                if self.scope == item.scope:
                    self._apply(item)

    def clear(self):
        with self._lock:
            self.request.value = ''
            self.capture(touched=True)
            item = self._drafts[self.scope]
            if self._save(item):
                try:
                    item.row = self.store.clear(item.scope, expected_revision=item.row['revision'])
                except Exception as exc:
                    item.error, item.dirty = f'草稿未能清除：{exc}', True
                self._render()
            if self.changed:
                self.changed()

    def compare(self):
        """Explicit conflict resolution; only the versions shown may be replaced."""
        with self._lock:
            self.capture()
            item = self._drafts[self.scope]
            generation = item.generation
            try:
                saved = self.store.load(item.scope)
            except Exception as exc:
                item.error = f'仍无法读取本机草稿：{exc}；当前输入保留。'
                self._render()
                return

        def resolve(use_local):
            with self._lock:
                self.capture()
                if self.scope != item.scope or item.generation != generation:
                    return
                try:
                    if use_local:
                        saved_row = self.store.save(item.scope, item.text, agent=item.agent, mode=item.mode,
                                                    expected_revision=saved['revision'])
                    else:
                        latest = self.store.load(item.scope)
                        if latest['revision'] != saved['revision']:
                            raise DraftConflict(latest)
                        saved_row = latest
                    item.row = saved_row
                    item.text, item.agent, item.mode = saved_row['text'], saved_row['agent'], saved_row['mode']
                    item.generation += 1
                    item.dirty, item.unreadable, item.error = False, False, ''
                    self.page.pop_dialog()
                    self._apply(item)
                except Exception:
                    item.error = '草稿版本再次变化或保存失败；当前输入保留，请关闭后重新检查。'
                    self._render()

        fields = [ft.TextField(label=label, value=text, read_only=True, multiline=True, min_lines=3, max_lines=6)
                  for label, text in [('当前未发送输入', item.text), ('本机已保存草稿', saved['text'])]]
        style_inputs(*fields)
        self.page.show_dialog(ft.AlertDialog(title=ft.Text('比较本地草稿'),
            content=ft.Container(width=580, height=340,
                content=ft.Column(fields, tight=True, spacing=14, scroll=ft.ScrollMode.AUTO,
                                  horizontal_alignment=ft.CrossAxisAlignment.STRETCH)),
            actions=[ft.TextButton('保存当前输入', on_click=lambda e: resolve(True)),
                     ft.TextButton('恢复本机版本', on_click=lambda e: resolve(False)),
                     ft.TextButton('取消', on_click=lambda e: self.page.pop_dialog())]))

    def close(self, *, force=True):
        with self._lock:
            if self._closed:
                return True
            self._capture_values()
            self._closed = True
            if self._timer:
                self._timer.cancel()
            saved = [self._save(item) for item in self._drafts.values()]
            if not all(saved) and not force:
                self._closed = False
                self._render()
                return False
            return all(saved)
