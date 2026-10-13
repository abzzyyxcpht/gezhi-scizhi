"""Read-only, bounded tool output pages for the native workbench."""
from __future__ import annotations

import flet as ft

from ..bridge import handle
from ..workbench import redact
from .theme import palette as p, style_inputs

LIST_LIMIT, READ_BYTES, SEARCH_LIMIT = 20, 4000, 10


class ToolOutputsDialog:
    def __init__(self, page, sid, selected, *, api=None):
        self.page, self.sid, self.selected, self.api = page, sid, selected, api or handle
        self.closed = False
        self.sequence = dict.fromkeys(('list', 'read', 'search'), 0)
        self.busy = dict.fromkeys(self.sequence, False)
        self.items, self.list_stack, self.body_stack = [], [], []
        self.list_offset, self.list_next = 0, None
        self.current, self.current_item, self.search_current = None, None, None
        self.list_rows = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
        self.list_label = ft.Text('读取输出…', size=11, color=p.TEXT_2, max_lines=3)
        self.list_previous = ft.TextButton('上一页', disabled=True, on_click=lambda e: self.previous_list())
        self.list_following = ft.TextButton('下一页', disabled=True, on_click=lambda e: self.next_list())
        self.heading = ft.Text('选择一项工具输出', size=14, weight=ft.FontWeight.W_600,
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.metadata = ft.Text('按需读取正文，每段最多 4,000 字节。', size=11, color=p.TEXT_2, max_lines=2)
        self.body = ft.Text('', selectable=True, font_family='Consolas', size=12)
        self.body_container = ft.Container(height=220, padding=10, bgcolor=p.SURFACE_2,
            content=ft.Column([self.body], scroll=ft.ScrollMode.AUTO))
        self.body_label = ft.Text('正文尚未读取', size=11, color=p.TEXT_2, max_lines=2)
        self.body_previous = ft.TextButton('上一段', disabled=True, on_click=lambda e: self.previous_chunk())
        self.body_following = ft.TextButton('下一段', disabled=True, on_click=lambda e: self.next_chunk())
        self.hash_text = ft.Text('', selectable=True, size=10, font_family='Consolas')
        self.reference_text = ft.Text('', selectable=True, size=10, font_family='Consolas')
        self.hash_detail = ft.ExpansionTile(title=ft.Text('输出标识与校验值', size=11),
            controls=[self.reference_text, self.hash_text], dense=True, min_tile_height=28)
        self.query = ft.TextField(label='搜索当前输出', hint_text='字面文本，区分大小写', dense=True,
            expand=True, on_submit=lambda e: self.search(), on_change=lambda e: self._query_changed())
        self.matches = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO)
        self.search_label = ft.Text('命中位置以 UTF-8 字节计；点击可定位正文。', size=11, color=p.TEXT_2, max_lines=2)
        self.search_previous = ft.TextButton('上一组', disabled=True, on_click=lambda e: self.previous_search())
        self.search_following = ft.TextButton('下一组', disabled=True, on_click=lambda e: self.next_search())

    def open(self):
        sidebar = ft.Container(width=250, content=ft.Column([
            ft.Row([ft.Text('本会话输出', weight=ft.FontWeight.W_600, expand=True),
                    ft.IconButton(ft.Icons.REFRESH, tooltip='刷新输出', on_click=lambda e: self.load_list())]),
            self.list_rows, self.list_label, ft.Row([self.list_previous, self.list_following], spacing=0)], expand=True))
        detail = ft.Column([self.heading, self.metadata, self.body_container,
            ft.Row([self.body_previous, self.body_following, self.body_label], spacing=8, wrap=True), self.hash_detail,
            ft.Row([self.query, ft.TextButton('搜索', on_click=lambda e: self.search()),
                    ft.TextButton('清除', on_click=lambda e: self.clear_search())], spacing=4),
            ft.Container(height=92, content=self.matches),
            ft.Row([self.search_previous, self.search_following, self.search_label], spacing=4)],
            spacing=8, expand=True, scroll=ft.ScrollMode.AUTO)
        content = ft.Container(width=880, height=570, content=ft.Column([
            ft.Text('只读工具数据；读取与搜索不代表科学核验通过。', size=11, color=p.TEXT_2),
            ft.Row([sidebar, ft.VerticalDivider(width=1), ft.Container(content=detail, expand=True)],
                   spacing=16, expand=True)], spacing=12))
        style_inputs(content)
        self.page.show_dialog(ft.AlertDialog(title=ft.Text('工具输出'), content=content,
            actions=[ft.TextButton('关闭', on_click=lambda e: self.close())],
            on_dismiss=lambda e: self.dismiss()))
        self.load_list()

    def dismiss(self):
        self.closed = True
        for kind in self.sequence:
            self.sequence[kind] += 1

    def close(self):
        self.dismiss()
        self.page.pop_dialog()

    def _active(self):
        return not self.closed and self.selected() == self.sid

    def _sync_buttons(self):
        self.list_previous.disabled = self.busy['list'] or not self.list_stack
        self.list_following.disabled = self.busy['list'] or self.list_next is None
        self.body_previous.disabled = self.busy['read'] or not self.body_stack
        self.body_following.disabled = self.busy['read'] or not self.current or self.current['next_offset'] is None
        search = self.search_current
        self.search_previous.disabled = self.busy['search'] or not search or not search['stack']
        self.search_following.disabled = self.busy['search'] or not search or search['next_offset'] is None

    def _request(self, kind, request, label, accept, *, valid=lambda: True):
        if not self._active():
            return
        self.sequence[kind] += 1
        sequence = self.sequence[kind]
        self.busy[kind] = True
        label.value = '读取中…'
        self._sync_buttons()
        self.page.update()

        def current():
            return self._active() and sequence == self.sequence[kind] and valid()

        def work():
            try:
                reply = self.api({'session_id': self.sid, **request})
                if not current():
                    return
                if not isinstance(reply, dict) or reply.get('ok') is not True:
                    raise ValueError(reply.get('error') if isinstance(reply, dict) else '输出读取失败')
                result = reply.get('output')
                if not isinstance(result, dict) or result.get('session_id') != self.sid:
                    raise ValueError('输出结果与当前会话不匹配')
                if request.get('output_id') and result.get('output_id') != request['output_id']:
                    raise ValueError('输出结果与所选输出不匹配')
                accept(result)
            except Exception as error:  # noqa: BLE001 — keep the last successful page visible.
                if current():
                    label.value = '读取失败：' + str(redact(str(error)))[:300]
            if current():
                self.busy[kind] = False
                self._sync_buttons()
                self.page.update()

        self.page.run_thread(work)

    @staticmethod
    def _next(result, offset):
        next_offset = result.get('next_offset')
        if next_offset is not None and (type(next_offset) is not int or next_offset <= offset):
            raise ValueError('输出分页位置无效')
        return next_offset

    def load_list(self, offset=0, stack=None):
        stack = list(stack or [])

        def accept(result):
            rows, total = result.get('items'), result.get('total')
            if not isinstance(rows, list) or len(rows) > LIST_LIMIT or type(total) is not int or total < 0:
                raise ValueError('输出列表格式无效')
            next_offset = self._next(result, offset)
            controls = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('output_id'), str):
                    raise ValueError('输出标识无效')
                controls.append(ft.ListTile(title=ft.Text(str(row.get('tool', '工具输出')), size=12, max_lines=1),
                    subtitle=ft.Text(f'{row["output_id"][:12]}… · {row.get("total_bytes", 0):,} 字节',
                        size=10, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    dense=True, on_click=lambda e, item=row: self.select_output(item)))
            self.items, self.list_offset, self.list_next, self.list_stack = rows, offset, next_offset, stack
            self.list_rows.controls = controls or [ft.Text('当前会话没有已归档的长输出', size=12, color=p.TEXT_2)]
            self.list_label.value = f'输出 {offset + 1 if rows else 0}–{offset + len(rows)} / {total}'
            if result.get('errors'):
                self.list_label.value += f' · {len(result["errors"])} 项未能读取'

        self._request('list', {'cmd': 'session_tool_outputs', 'offset': offset, 'limit': LIST_LIMIT}, self.list_label, accept)

    def next_list(self):
        if self.list_next is not None:
            self.load_list(self.list_next, [*self.list_stack, self.list_offset])

    def previous_list(self):
        if self.list_stack:
            self.load_list(self.list_stack[-1], self.list_stack[:-1])

    def select_output(self, item):
        self.sequence['search'] += 1
        self.busy['search'] = False
        self._load_chunk(item, 0, [], reset_search=True)

    def _load_chunk(self, item, offset, stack, *, reset_search=False):
        output_id = item['output_id']

        def accept(result):
            text = result.get('text')
            if not isinstance(text, str) or len(text.encode('utf-8')) > READ_BYTES or result.get('offset') != offset:
                raise ValueError('输出正文或分页位置无效')
            next_offset = self._next(result, offset)
            total = result.get('total_bytes')
            if type(total) is not int or total < offset:
                raise ValueError('输出字节数无效')
            self.current, self.current_item, self.body_stack = {**result, 'next_offset': next_offset}, item, list(stack)
            self.body.value = text
            self.heading.value = str(item.get('tool', '工具输出'))
            self.metadata.value = f'{total:,} 字节 · {item.get("kind", "tool_result")} · {item.get("created_at", "")}'
            self.hash_text.value = str(result.get('sha256', ''))
            self.reference_text.value = output_id
            self.body_label.value = f'字节 {offset + 1 if text else offset}–{offset + len(text.encode("utf-8"))} / {total}'
            if next_offset is None:
                self.body_label.value += ' · 已到末尾'
            if reset_search:
                self.clear_search(update=False)

        self._request('read', {'cmd': 'session_tool_output_read', 'output_id': output_id,
            'offset': offset, 'limit': READ_BYTES}, self.body_label, accept)

    def next_chunk(self):
        if self.current and self.current['next_offset'] is not None:
            self._load_chunk(self.current_item, self.current['next_offset'], [*self.body_stack, self.current['offset']])

    def previous_chunk(self):
        if self.current and self.body_stack:
            self._load_chunk(self.current_item, self.body_stack[-1], self.body_stack[:-1])

    def _query_changed(self):
        self.sequence['search'] += 1
        self.busy['search'] = False
        self._sync_buttons()
        if self._active():
            self.page.update()

    def clear_search(self, *, update=True):
        self.sequence['search'] += 1
        self.busy['search'] = False
        self.query.value, self.matches.controls, self.search_current = '', [], None
        self.search_label.value = '命中位置以 UTF-8 字节计；点击可定位正文。'
        self._sync_buttons()
        if update and self._active():
            self.page.update()

    def search(self, offset=0, stack=None):
        if not self._active() or not self.current:
            return
        query = self.query.value or ''
        if not query:
            self.search_label.value = '输入要查找的字面文本。'
            self.page.update()
            return
        if self.search_current and self.search_current['query'] != query:
            offset, stack = 0, []
        output_id, stack = self.current['output_id'], list(stack or [])

        def accept(result):
            rows = result.get('matches')
            if not isinstance(rows, list) or len(rows) > SEARCH_LIMIT:
                raise ValueError('搜索结果格式无效')
            next_offset = self._next(result, offset)
            controls = []
            for row in rows:
                if not isinstance(row, dict) or type(row.get('offset')) is not int or row['offset'] < 0:
                    raise ValueError('命中位置无效')
                controls.append(ft.ListTile(title=ft.Text(str(row.get('preview', '')), size=11,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    subtitle=ft.Text(f'字节 {row["offset"]}', size=10), dense=True,
                    on_click=lambda e, position=row['offset']: self.jump_to_match(output_id, position)))
            self.matches.controls = controls or [ft.Text('未找到匹配文本', size=11, color=p.TEXT_2)]
            self.search_current = {**result, 'query': query, 'offset': offset, 'stack': stack, 'next_offset': next_offset}
            self.search_label.value = f'本组 {len(rows)} 个命中' + (' · 已搜索到末尾' if next_offset is None else ' · 还有结果')

        self._request('search', {'cmd': 'session_tool_output_search', 'output_id': output_id,
            'query': query, 'offset': offset, 'limit': SEARCH_LIMIT}, self.search_label, accept,
            valid=lambda: bool(self.current and self.current['output_id'] == output_id and (self.query.value or '') == query))

    def next_search(self):
        current = self.search_current
        if current and current['next_offset'] is not None:
            self.search(current['next_offset'], [*current['stack'], current['offset']])

    def previous_search(self):
        current = self.search_current
        if current and current['stack']:
            self.search(current['stack'][-1], current['stack'][:-1])

    def jump_to_match(self, output_id, offset):
        if self.current and self.current['output_id'] == output_id:
            self._load_chunk(self.current_item, offset, [*self.body_stack, self.current['offset']])
