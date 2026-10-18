"""Paged history requests leave the native input and event loop responsive."""
from __future__ import annotations

import flet as ft

from ..bridge import handle
from ..workbench import redact
from .markdown_view import MarkdownView
from .theme import motion_duration, palette as p, radius, style_inputs, ui_size


class SessionHistoryDialog:
    def __init__(self, page, sid, selected, bubble, *, api=None):
        self.page, self.sid, self.selected, self.bubble = page, sid, selected, bubble
        self.api, self.closed, self.sequence = api or handle, False, 0
        self.current, self.snapshot = None, None
        self.rows = ft.Column(spacing=12, expand=True, scroll=ft.ScrollMode.AUTO)
        self.label = ft.Text('读取历史…', size=11, color=p.TEXT_2, max_lines=3)
        self.query = ft.TextField(label='搜索对话', hint_text='按 Enter 搜索已保存的完整对话', dense=True,
                                  expand=True, on_submit=lambda e: self.load(search=True))
        self.previous = ft.TextButton('上一页', disabled=True, on_click=lambda e: self.move(-1))
        self.following = ft.TextButton('下一页', disabled=True, on_click=lambda e: self.move(1))
        self.search_row = ft.Row([self.query, ft.TextButton('搜索', on_click=lambda e: self.load(search=True)),
                                 ft.TextButton('清除', on_click=lambda e: self.clear())], spacing=8)
        self.navigation = ft.Row([self.previous, self.following,
            ft.TextButton('刷新历史', on_click=lambda e: self.load(search=True))], spacing=8)

    def open(self):
        style_inputs(self.search_row)
        self.page.show_dialog(ft.AlertDialog(title=ft.Text('对话记录'),
            content=ft.Container(width=660, height=460, content=ft.Column(
                [self.search_row, self.rows, self.label, self.navigation], expand=True)),
            actions=[ft.TextButton('关闭', on_click=lambda e: self.close())], on_dismiss=lambda e: self.dismiss()))
        self.load()

    def dismiss(self):
        self.closed = True
        self.sequence += 1

    def close(self):
        self.dismiss()
        self.page.pop_dialog()

    def clear(self):
        self.query.value = ''
        self.load(search=True)

    def move(self, delta):
        if self.current:
            self.load(page=max(1, min(self.current['pages'], self.current['page'] + delta)))

    def load(self, *, page=None, search=False):
        if self.closed or self.selected() != self.sid:
            return
        self.sequence += 1
        sequence = self.sequence
        query = (self.query.value or '').strip()
        if self.current and query != self.current['query']:
            search, page = True, None
        snapshot = None if search else self.snapshot
        request = {'cmd': 'session_history', 'session_id': self.sid, 'query': query, 'page_size': 40,
                   **({'page': page} if page else {}), **({'snapshot': snapshot} if snapshot else {})}
        self.previous.disabled = self.following.disabled = True
        self.label.value = '读取历史…首次建立索引后，后续按需分页。'
        self.page.update()
        def work():
            try:
                reply = self.api(request)
                if self.closed or sequence != self.sequence or self.selected() != self.sid:
                    return
                if reply.get('ok') is not True:
                    raise ValueError(reply.get('error') or '历史读取未得到有效结果')
                result = reply.get('history')
                if not isinstance(result, dict) or result.get('session_id') != self.sid or result.get('query') != query:
                    raise ValueError('历史结果与会话或搜索条件不匹配')
                self.current, self.snapshot = result, result['snapshot']
                self.rows.controls = []
                for row in result['messages']:
                    self.rows.controls.append(self.bubble(row['role'], row['content']))
                    if row['truncated']:
                        self.rows.controls.append(ft.TextButton(f'长消息 {row["id"]}：分段查看完整内容（{row["characters"]} 字符）',
                            on_click=lambda e, i=row['id'], g=result['generation']: self.show_message(i, g)))
                if not result['messages']:
                    self.rows.controls = [ft.Text('没有匹配的对话' if query else '当前会话还没有已保存的对话', color=p.TEXT_2)]
                start = (result['page'] - 1) * result['page_size']
                end = min(start + len(result['messages']), result['total'])
                self.label.value = ((f'命中 {result["total"]} · ' if query else '') +
                    f'消息 {start + 1 if result["total"] else 0}–{end} / {result["total"]} · 第 {result["page"]}/{result["pages"]} 页 · 已保存快照' +
                    (' · 有新消息，刷新查看' if result['new_messages'] else '') +
                    (' · 历史已改写，已重置快照' if result['reset'] else ''))
                self.previous.disabled = result['page'] == 1
                self.following.disabled = result['page'] == result['pages']
                style_inputs(self.rows)
                self.page.update()
            except Exception as error:  # noqa: BLE001 — preserve visible history, never invent an empty result
                if not self.closed and sequence == self.sequence and self.selected() == self.sid:
                    self.label.value = str(redact(str(error)))
                    self.previous.disabled = not self.current or self.current['page'] == 1
                    self.following.disabled = not self.current or self.current['page'] == self.current['pages']
                    self.page.update()
        self.page.run_thread(work)

    def show_message(self, message_id, generation):
        content = MarkdownView('', selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)
        reader = ft.Column([content], expand=True, scroll=ft.ScrollMode.AUTO, visible=False,
                           horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        label = ft.Text('', size=ui_size('sm'), color=p.TEXT_2, visible=False)
        notice = ft.Text('', size=ui_size('sm'), color=p.TEXT_2, max_lines=3, visible=False)
        previous, following = ft.TextButton('上一段'), ft.TextButton('下一段')
        retry = ft.TextButton('重试读取', visible=False)
        initial_retry = ft.TextButton('重新读取', icon=ft.Icons.REFRESH, visible=False)
        progress = ft.ProgressRing(width=28, height=28, stroke_width=2.5, color=p.ACCENT)
        status_icon = ft.Icon(ft.Icons.HOURGLASS_EMPTY, size=28, color=p.TEXT_2, visible=False)
        status_title = ft.Text('正在读取完整消息', size=ui_size('base'), color=p.TEXT,
                               weight=ft.FontWeight.W_600, text_align=ft.TextAlign.CENTER)
        status_detail = ft.Text('长消息按段加载，请稍候。', size=ui_size('sm'), color=p.TEXT_2,
                                text_align=ft.TextAlign.CENTER, max_lines=4)
        status = ft.Container(expand=True, padding=24, bgcolor=p.SURFACE_2, border_radius=radius('panel'),
            alignment=ft.Alignment.CENTER, content=ft.Column(
                [progress, status_icon, status_title, status_detail, initial_retry], spacing=12,
                tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER))
        navigation = ft.Row([previous, following, retry], spacing=8, run_spacing=4, wrap=True, visible=False)
        state = {'offset': 0, 'next': None, 'closed': False, 'sequence': 0, 'request_offset': 0, 'loaded': False}
        def active():
            return not self.closed and not state['closed'] and self.selected() == self.sid
        def dismiss(e=None):
            state['closed'] = True
        def load_chunk(offset):
            if not active():
                return
            state['sequence'] += 1
            sequence = state['sequence']
            state['request_offset'] = offset
            previous.disabled = following.disabled = retry.disabled = initial_retry.disabled = True
            retry.visible = initial_retry.visible = False
            if state['loaded']:
                notice.value = '正在读取其他段落，当前内容保留。'
                notice.tooltip = None
                notice.visible = True
            else:
                progress.visible = motion_duration() > 0
                status_icon.visible = not progress.visible
                status_icon.icon = ft.Icons.HOURGLASS_EMPTY
                status_title.value = '正在读取完整消息'
                status_detail.value = '长消息按段加载，请稍候。'
                status_detail.tooltip = None
            self.page.update()
            def current():
                return active() and state['sequence'] == sequence
            def work():
                try:
                    reply = self.api({'cmd': 'session_history_message', 'session_id': self.sid, 'message_id': message_id,
                        'generation': generation, 'offset': offset, 'limit': 20000})
                    if not current():
                        return
                    if reply.get('ok') is not True:
                        raise ValueError(reply.get('error') or '消息读取失败')
                    row = reply['message']
                    if row['session_id'] != self.sid or row['generation'] != generation or row['message_id'] != message_id:
                        raise ValueError('消息身份已变化')
                    content.value = row['content']
                    state.update(offset=offset, next=row['next_offset'], loaded=True)
                    label.value = f'字符 {offset + 1 if row["content"] else offset}–{offset + len(row["content"])} / {row["characters"]}'
                    reader.visible = label.visible = navigation.visible = True
                    status.visible = notice.visible = False
                    retry.visible = False
                except Exception as error:  # noqa: BLE001
                    if current():
                        detail = str(redact(str(error))) or '暂时无法读取，请重试。'
                        if state['loaded']:
                            notice.value = f'未能读取该段，当前内容已保留。{detail}'
                            notice.tooltip = notice.value
                            notice.visible = retry.visible = True
                        else:
                            progress.visible = False
                            status_icon.icon = ft.Icons.CLOUD_OFF_OUTLINED
                            status_icon.visible = True
                            status_title.value = '完整消息暂时无法读取'
                            status_detail.value = detail
                            status_detail.tooltip = detail
                            initial_retry.visible = True
                if current():
                    previous.disabled = not state['loaded'] or state['offset'] == 0
                    following.disabled = not state['loaded'] or state['next'] is None
                    retry.disabled = initial_retry.disabled = False
                    self.page.update()
            self.page.run_thread(work)
        previous.on_click = lambda e: load_chunk(max(0, state['offset'] - 20000))
        following.on_click = lambda e: load_chunk(state['next']) if state['next'] is not None else None
        retry.on_click = lambda e: load_chunk(state['request_offset'])
        initial_retry.on_click = retry.on_click
        dialog = ft.AlertDialog(title=ft.Text(f'完整消息 · {message_id}'),
            content=ft.Container(width=680, height=440, content=ft.Column(
                [status, reader, label, notice, navigation], spacing=10, expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH)),
            actions=[ft.TextButton('关闭', on_click=lambda e: (dismiss(), self.page.pop_dialog()))],
            on_dismiss=dismiss)
        style_inputs(dialog)
        self.page.show_dialog(dialog)
        load_chunk(0)
