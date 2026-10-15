"""Session controls and a separate side discussion for the native workbench."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import flet as ft
from ..bridge import handle
from ..workbench import redact
from .theme import palette, radius, style_inputs
from .markdown_view import MarkdownView

_MD = ft.MarkdownExtensionSet.GITHUB_WEB

# 会话转写控件上限：长会话（数百轮工具调用）下唯一没有边界的事件流容器，
# 超出后从头部裁剪并同步清理行索引（与 log_col 800 / terminal 200 同思路）
TRANSCRIPT_LIMIT = 400
EVENT_PAGE_LIMIT = 200
SHELL_PREVIEW_CHARS = 1000


class SessionComposer:
    def __init__(self, page, request, selected, project, open_session, changed,
                 events_provider=None, event_page_provider=None, events_changed=None,
                 on_draft_changed=None, open_new_session=None):
        self.page, self.request = page, request
        self.selected, self.project, self.open_session, self.changed = selected, project, open_session, changed
        self.open_new_session = open_new_session
        self.on_draft_changed = on_draft_changed or self.update_preview
        # events_provider(sid) → SessionStore 事件列表；为 None 时回退到
        # messages 末 20 条的旧渲染（事件不可用的环境，如部分测试）。
        self.events_provider = events_provider
        self.event_page_provider = event_page_provider
        self.events_changed = events_changed
        self._page_cursor = None
        self._event_epoch = 0
        self.recent_events = []
        self.has_more_events = False
        self.side_sid = None
        self.epoch = 0
        self._side_draft_generation = 0
        # FilePicker 必须注册为 page service（CODELY.md 硬约定）：未注册时
        # pick_files 对话框不弹出，按钮表现为"点了没反应"
        self.file_picker = ft.FilePicker()
        page.services.append(self.file_picker)
        # 事件流渲染状态：游标 + 各类型行的控件引用（增量更新用）
        self.event_cursor = 0
        self._event_anchor = None
        self._turn_index = 0
        self._message_index = 0
        self._rendered_sid = None
        self.stream_bubbles = {}   # (用户轮次, step) → Markdown 增量区
        self._step_shells = {}     # 同一 key → 气泡 Container（裁剪时定位）
        self.tool_rows = {}        # (用户轮次, tool_call_id) → (ExpansionTile, 标题 Text, 工具名)
        self._completed_tools = set()
        self.subagent_rows = {}    # child_session → 状态 Text 行
        self._open_step = None     # 最近收到 delta 且未 finalize 的 step
        self.attachments = ft.Row(wrap=True, spacing=6)
        self.preview = ft.Container(visible=False, padding=14, bgcolor=palette.SURFACE_2,
            content=MarkdownView('', selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB))
        self.transcript = ft.Column(spacing=14)
        self.info = ft.Text('新建会话后，消息会保留在同一研究讨论中。', size=11, color=palette.TEXT_3,
                            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.primary_actions = ft.Row([
            self.button('新建会话', self.new, ft.Icons.ADD_COMMENT_OUTLINED),
            self.button('对话记录', self.show_history, ft.Icons.HISTORY),
            self.button('添加文件', lambda: page.run_task(self.choose_attachments), ft.Icons.ATTACH_FILE),
        ], wrap=True, spacing=4)
        self.toolbar = ft.Row([
            self.button('管理会话', self.manage, ft.Icons.FORUM_OUTLINED),
            self.button('工具输出', self.show_tool_outputs, ft.Icons.DESCRIPTION_OUTLINED),
            self.button('预览 Markdown', self.toggle_preview, ft.Icons.NOTES_OUTLINED),
            self.button('分叉', self.fork, ft.Icons.CALL_SPLIT),
            self.button('回滚上一轮', self.rewind, ft.Icons.UNDO),
            self.button('侧边讨论', self.open_side, ft.Icons.CHAT_BUBBLE_OUTLINE),
            self.button('分享 / 导出', self.share, ft.Icons.IOS_SHARE),
        ], wrap=True, spacing=4)
        self.side_input = ft.TextField(label='围绕当前材料继续追问', multiline=True, min_lines=2, max_lines=5,
                                       on_change=self._side_draft_changed)
        self.side_answer = MarkdownView('', selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)
        self.side_notice = ft.Text('独立讨论，主会话保持原样。', size=11, color=palette.TEXT_2)
        self.side_send = ft.FilledButton('发送', on_click=lambda e: self.send_side())
        self.side = ft.Container(visible=False, width=320, padding=14, bgcolor=palette.SURFACE_2, border_radius=radius('panel'),
            content=ft.Column([ft.Row([ft.Text('侧边讨论', weight=ft.FontWeight.W_600, expand=True),
                ft.IconButton(ft.Icons.CLOSE, tooltip='收起侧边讨论', on_click=lambda e: self.close_side())]), self.side_notice,
                self.side_answer, self.side_input, ft.Row([self.side_send,
                self.button('打开完整会话', lambda: self.open_session(self.side_sid) if self.side_sid else None)])],
                scroll=ft.ScrollMode.AUTO, expand=True))
        self.request.on_change = lambda e: self.update_preview()
        style_inputs(self.primary_actions, self.toolbar, self.side, self.preview)

    def button(self, text, action, icon=None):
        return ft.TextButton(text, icon=icon, on_click=lambda e: action(),
                             style=ft.ButtonStyle(color=palette.ACCENT_TEXT, icon_size=16))

    def notice(self, text):
        self.page.show_dialog(ft.SnackBar(ft.Text(str(text), max_lines=3,
                                                  overflow=ft.TextOverflow.ELLIPSIS)))
        self.page.update()

    def call(self, req):
        result = handle(req)
        if not result.get('ok'):
            self.notice(result.get('error', '操作失败'))
            return None
        return result

    def new(self, preserve_input=False):
        text = (self.request.value or '') if preserve_input else ''
        result = self.call({'cmd': 'chat_new', 'project_id': self.project() or ''})
        if result:
            if self.open_new_session:
                self.open_new_session(result['session_id'], preserve_input)
            else:
                self.open_session(result['session_id'])
                self.request.value = text
            self.on_draft_changed()
            self.preview.visible = False
            self.refresh()

    def ensure_session(self):
        if not self.selected():
            self.new(preserve_input=True)
        return self.selected()

    def _bubble(self, role: str, text: str) -> ft.Container:
        """user/assistant 消息气泡（与旧 messages 渲染同款样式）。"""
        return ft.Container(padding=12, border_radius=radius('panel'),
            bgcolor=palette.SURFACE_2 if role == 'user' else palette.SURFACE,
            content=ft.Column([ft.Text('研究者' if role == 'user' else '智能体',
                size=11, color=palette.ACCENT_TEXT),
                MarkdownView(text, selectable=True, extension_set=_MD)], spacing=6))

    def _assistant_shell(self, md: MarkdownView) -> ft.Container:
        return ft.Container(padding=12, border_radius=radius('panel'), bgcolor=palette.SURFACE,
            content=ft.Column([ft.Text('智能体', size=11, color=palette.ACCENT_TEXT), md], spacing=6))

    def _add_tool_row(self, call_id: str, name: str, arguments: dict) -> None:
        """tool_call 折叠行：标题=工具名，展开看参数 JSON，结果出来后打勾/打叉。"""
        title = ft.Text(f"⚙ {name}", size=11, font_family="Consolas", color=palette.TEXT_2)
        detail = ft.Text(json.dumps(arguments, ensure_ascii=False, indent=2)
                         if arguments else "（无参数）", size=11, font_family="Consolas",
                         color=palette.TEXT_3, selectable=True, max_lines=16,
                         overflow=ft.TextOverflow.ELLIPSIS)
        tile = ft.ExpansionTile(title=title, controls=[detail], dense=True,
            tile_padding=ft.Padding(0, 0, 0, 0), min_tile_height=30,
            collapsed_shape=ft.RoundedRectangleBorder(radius=0),
            shape=ft.RoundedRectangleBorder(radius=0))
        self.tool_rows[(self._turn_index, call_id)] = (tile, title, name)
        self.transcript.controls.append(tile)

    def _update_tool_row(self, call_id: str, result) -> None:
        key = (self._turn_index, call_id)
        row = self.tool_rows.get(key)
        if row is None:
            return
        tile, title, name = row
        ok = isinstance(result, dict) and result.get("ok")
        if name == 'shell.run' and isinstance(result, dict):
            ok = ok and not result.get('cancelled') and not result.get('timed_out') \
                and result.get('exit_code') in (0, None)
        self._completed_tools.add(key)
        title.value = f"{'✔' if ok else '✘'} {name}"
        title.color = palette.SUCCESS if ok else palette.DANGER
        summary = str(result)[:300] if result else ""
        if summary:
            tile.controls.append(ft.Text(summary, size=11, font_family="Consolas",
                                         color=palette.TEXT_3, selectable=True,
                                         max_lines=8, overflow=ft.TextOverflow.ELLIPSIS))

    def _update_shell_output(self, payload: dict) -> bool:
        """Keep a small live plain-text preview on its current-turn tool row."""
        key = (self._turn_index, str(payload.get('tool_call_id') or ''))
        row = self.tool_rows.get(key)
        text = payload.get('text')
        if row is None or row[2] != 'shell.run' or key in self._completed_tools \
                or payload.get('stream') not in ('stdout', 'stderr') \
                or not isinstance(text, str) or not text:
            return False
        tile = row[0]
        if tile.subtitle is None:
            tile.subtitle = ft.Text('', size=11, font_family='Consolas', selectable=True,
                color=palette.TEXT_2, max_lines=8, no_wrap=False, overflow=ft.TextOverflow.ELLIPSIS)
        preview = tile.subtitle
        value = str(redact(preview.value + text))[-SHELL_PREVIEW_CHARS:]
        if value == preview.value:
            return False
        preview.value = value
        # A subtitle stays visible while the argument ExpansionTile is collapsed.
        preview.visible = True
        return True

    def _update_subagent_row(self, kind: str, p: dict) -> None:
        """Render reported child state without keeping stale running labels."""
        child = str(p.get("child") or p.get("agent") or "")
        agent = str(p.get("agent") or "")
        row = self.subagent_rows.get(child)
        if row is None:
            row = ft.Text(size=12)
            self.subagent_rows[child] = row
            self.transcript.controls.append(row)
        status = p.get("status")
        if kind == "subagent_started" or status == "running":
            row.value, row.color = f"◆ 子代理 {agent} 运行中…", palette.INFO
        elif kind == "subagent_completed":
            ok = bool(p.get("ok"))
            row.value = f"✔ {agent} 完成" if ok else f"✘ {agent} 失败"
            row.color = palette.SUCCESS if ok else palette.DANGER
        elif status == "done":
            steps, tokens = p.get('steps'), p.get('tokens')
            row.value = f"✔ {agent} 完成（{steps if type(steps) is int else '未报告'} 步 / {tokens if type(tokens) is int else '未报告'} tokens）"
            row.color = palette.SUCCESS
        elif status == "error":
            row.value, row.color = f"✘ {agent} 失败", palette.DANGER
        else:
            from .control_tower import STATUS
            label = STATUS.get(status, "状态未报告")
            failed = status in {"blocked", "partial", "failed"}
            waiting = status in {"waiting_approval", "waiting_task", "waiting_children", "paused"}
            row.value = f"{'✘' if failed else '◇'} 子代理 {agent} {label}"
            row.color = palette.DANGER if failed else palette.WARN if waiting else palette.TEXT_2

    def _cap_transcript(self) -> None:
        """长会话控件上限：超出后从头部裁剪，行索引字典同步清理。

        不清理的话 stream_bubbles / tool_rows / subagent_rows 只增不减，
        被裁掉的气泡再收到 delta 也会写进不显示的控件（静默丢失）。
        """
        extra = len(self.transcript.controls) - TRANSCRIPT_LIMIT
        if extra <= 0:
            return
        dropped = self.transcript.controls[:extra]
        del self.transcript.controls[:extra]
        dropped_ids = {id(c) for c in dropped}
        for step, shell in list(self._step_shells.items()):
            if id(shell) in dropped_ids:
                del self._step_shells[step]
                self.stream_bubbles.pop(step, None)
        for call_id, row in list(self.tool_rows.items()):
            if id(row[0]) in dropped_ids:
                del self.tool_rows[call_id]
                self._completed_tools.discard(call_id)
        for child, row in list(self.subagent_rows.items()):
            if id(row) in dropped_ids:
                del self.subagent_rows[child]
        if self._open_step is not None and self._open_step not in self.stream_bubbles:
            self._open_step = None

    def _render_events(self, events: list) -> bool:
        """把一段事件追加进 transcript；返回是否有可见变化。

        assistant_delta 按 step 拼接到对应气泡的 Markdown 上，
        随后的 assistant_message 用完整 content 覆盖该步增量区。
        """
        changed = False
        for e in events:
            typ = e.get("type", "")
            p = e.get("payload") or {}
            if typ == "user_message":
                self._turn_index += 1
                self.transcript.controls.append(
                    self._bubble('user', str(p.get('text') or '')))
                self._open_step = None
                changed = True
            elif typ == "assistant_delta":
                step = (self._turn_index, p.get("step"))
                md = self.stream_bubbles.get(step)
                if md is None:
                    md = MarkdownView('', selectable=True, extension_set=_MD)
                    self.stream_bubbles[step] = md
                    shell = self._assistant_shell(md)
                    self._step_shells[step] = shell
                    self.transcript.controls.append(shell)
                md.value = (md.value or '') + str(p.get('delta') or '')
                self._open_step = step
                changed = True
            elif typ == 'steer_applied':
                self.transcript.controls.append(self._bubble('user', '补充本轮：' + str(p.get('text') or '')))
                self._open_step = None
                changed = True
            elif typ == "assistant_message":
                content = str(p.get('content') or '')
                raw_step = p.get("step")
                step = (self._turn_index, raw_step) if raw_step is not None else None
                md = self.stream_bubbles.get(step) if step is not None else None
                if step is None and self._open_step is not None:
                    md = self.stream_bubbles.get(self._open_step)
                if md is None:
                    if not content and p.get("tool_calls"):
                        continue  # 纯工具调用步：tool_call 行已展示
                    md = MarkdownView(content, selectable=True, extension_set=_MD)
                    self._message_index += 1
                    key = step if step is not None else (self._turn_index, 'message', self._message_index)
                    self.stream_bubbles[key] = md
                    shell = self._assistant_shell(md)
                    self._step_shells[key] = shell
                    self.transcript.controls.append(shell)
                elif content:
                    md.value = content  # 最终版替换增量区
                self._open_step = None
                changed = True
            elif typ == "tool_call":
                self._add_tool_row(str(p.get('id') or ''), str(p.get('name') or ''),
                                   p.get('arguments') or {})
                changed = True
            elif typ == "tool_result":
                self._update_tool_row(str(p.get('id') or ''), p.get('result'))
                changed = True
            elif typ == 'shell_output':
                changed = self._update_shell_output(p) or changed
            elif typ in ("subagent_started", "subagent_status", "subagent_completed"):
                self._update_subagent_row(typ, p)
                changed = True
            elif typ == "error":
                self.transcript.controls.append(ft.Text(
                    '✘ ' + str(p.get('message') or '执行失败'), size=12, color=palette.DANGER,
                    max_lines=3, overflow=ft.TextOverflow.ELLIPSIS))
                changed = True
        self._cap_transcript()
        return changed

    def consume_events(self) -> None:
        """轮询期间增量拉取事件并局部更新当前气泡（整段刷新的替代）。

        只在事件数变化时重渲染新增片段；会话切换时走 refresh() 全量重建。
        """
        sid = self.selected()
        if not sid or not (self.event_page_provider or self.events_provider):
            return
        if sid != self._rendered_sid:
            self.refresh()
            return
        epoch = self._event_epoch
        if self.event_page_provider:
            try:
                result = self.event_page_provider(sid, cursor=self._page_cursor,
                                                  limit=EVENT_PAGE_LIMIT, tail=True)
            except Exception:  # noqa: BLE001 — retain the last good view on transient I/O errors
                self.has_more_events = False
                return
            if sid != self.selected() or epoch != self._event_epoch:
                return
            # One page per tick bounds catch-up work, even when has_more is true.
            if self._accept_page(result):
                style_inputs(self.transcript)
                self.page.update()
            return
        try:
            events = self.events_provider(sid) or []
        except Exception:  # noqa: BLE001 —— 事件源暂不可读时保持现状
            return
        if sid != self.selected() or epoch != self._event_epoch:
            return
        # 回滚可在同一会话截断日志；相同长度的重写也不能沿用旧游标。
        if len(events) < self.event_cursor or (self.event_cursor and
                events[self.event_cursor - 1] != self._event_anchor):
            self.refresh()
            return
        fresh = events[self.event_cursor:]
        if not fresh:
            return
        self.event_cursor = len(events)
        self._event_anchor = copy.deepcopy(events[-1]) if events else None
        if self._render_events(fresh):
            style_inputs(self.transcript)
            self.page.update()

    def _reset_event_view(self):
        self.transcript.controls = []
        self.stream_bubbles, self.tool_rows, self.subagent_rows = {}, {}, {}
        self._completed_tools = set()
        self._step_shells = {}
        self._open_step = None
        self.event_cursor = 0
        self._event_anchor = None
        self._turn_index = 0
        self._message_index = 0

    def _accept_page(self, result, *, reset=False):
        if not isinstance(result, dict) or result.get('ok') is False or not isinstance(result.get('events'), list):
            return False
        reset = reset or bool(result.get('reset'))
        changed = bool(reset and self.transcript.controls)
        if reset:
            self._reset_event_view()
        fresh = result['events']
        self._page_cursor = result.get('cursor')
        self.has_more_events = bool(result.get('has_more'))
        self.event_cursor += len(fresh)
        changed = self._render_events(fresh) or changed
        # The collapsed operation log does not need one blank row per stream
        # chunk. Share this bounded committed-event view; never read JSONL twice.
        observed = [event for event in fresh if event.get('type') not in ('assistant_delta', 'shell_output')]
        recent = (([] if reset else self.recent_events) + observed)[-EVENT_PAGE_LIMIT:]
        if recent != self.recent_events or reset:
            self.recent_events = recent
            if self.events_changed:
                self.events_changed(recent)
                changed = True
        return changed

    def refresh(self):
        self._event_epoch += 1
        epoch = self._event_epoch
        sid = self.selected()
        switched = sid != self._rendered_sid
        if switched or not sid:
            self.attachments.controls = []
            self._reset_event_view()
            self._page_cursor = None
            self.has_more_events = False
            self._rendered_sid = None
            self.recent_events = []
            self.info.value = '正在读取会话…' if sid else '新建会话后，消息会保留在同一研究讨论中。'
            if self.events_changed:
                self.events_changed([])
        if sid:
            request = {'cmd': 'chat_get', 'session_id': sid}
            if self.event_page_provider:
                request['metadata_only'] = True
            result = handle(request)
            if not result.get('ok'):
                if switched:
                    self.info.value = '会话信息暂不可读，请稍后重新打开。'
                    self.page.update()
                return
            if sid != self.selected() or epoch != self._event_epoch:
                return
            row = result['session']
            self.info.value = row['title'] + (' · 已归档' if row['archived'] else '')
            if self.event_page_provider:
                self.info.value += ' · 最近活动 · 完整对话见「对话记录」'
            self.attachments.controls = []
            for path in row.get('attachments', []):
                self.attachments.controls.append(ft.OutlinedButton(path+' ×', on_click=lambda e, p=path: self.detach(p)))
            if self.event_page_provider:
                self._rendered_sid = sid
                try:
                    event_page = self.event_page_provider(sid, cursor=None, limit=EVENT_PAGE_LIMIT, tail=True)
                except Exception:  # noqa: BLE001 — same-session refresh keeps its last good view
                    self.has_more_events = False
                    if switched:
                        self.page.update()
                    return
                if sid != self.selected() or epoch != self._event_epoch:
                    return
                self._accept_page(event_page, reset=True)
                self.update_preview()
                style_inputs(self.transcript)
                self.page.update()
                return
            events = []
            events_available = False
            if self.events_provider:
                try:
                    events = self.events_provider(sid) or []
                    events_available = True
                except Exception:  # noqa: BLE001 —— 回退到 messages 渲染
                    events = []
            if sid != self.selected() or epoch != self._event_epoch:
                return
            self._reset_event_view()
            if events_available:
                self._rendered_sid = sid
                self.event_cursor = len(events)
                self._event_anchor = copy.deepcopy(events[-1]) if events else None
                self._render_events(events)
            else:
                # 回退路径：events 不可用时按 messages 末 20 条渲染
                self._rendered_sid = None
                for message in result.get('messages', [])[-20:]:
                    self.transcript.controls.append(self._bubble(message['role'], message['content']))
        self.update_preview()
        style_inputs(self.transcript)
        self.page.update()

    def show_history(self):
        """Open asynchronous indexed pages without loading full JSONL into the UI."""
        sid = self.selected()
        if not sid:
            return self.notice('先打开一个会话')
        from .session_history import SessionHistoryDialog
        SessionHistoryDialog(self.page, sid, self.selected, self._bubble, api=handle).open()

    def show_tool_outputs(self):
        sid = self.selected()
        if not sid:
            return self.notice('先打开一个会话')
        from .tool_outputs import ToolOutputsDialog
        ToolOutputsDialog(self.page, sid, self.selected, api=handle).open()

    def rewind(self):
        sid = self.selected()
        if not sid:
            return self.notice('先打开需要回滚的会话')

        def apply():
            self.page.pop_dialog()
            result = self.call({'cmd': 'session_rewind', 'session_id': sid, 'turns': 1})
            if result and self.selected() == sid:
                self.open_session(sid)
                self.refresh()
                self.notice(result.get('warning') or '已回滚上一轮，可重新输入问题')

        self.page.show_dialog(ft.AlertDialog(title=ft.Text('回滚上一轮对话？'),
            content=ft.Text('移除最后一次提问及其后的会话记录。不会撤销已经写入的文件、导出或外部操作；运行中的任务需要先停止。',
                            max_lines=5),
            actions=[self.button('回滚上一轮', apply), self.button('取消', self.page.pop_dialog)]))

    def attach(self, path):
        sid = self.ensure_session()
        if sid and self.call({'cmd': 'chat_attach', 'session_id': sid, 'path': path}):
            self.refresh()

    def detach(self, path):
        if self.call({'cmd': 'chat_detach', 'session_id': self.selected(), 'path': path}):
            self.refresh()

    async def choose_attachments(self):
        # Material belongs to the chat that opened the picker, even if another
        # chat/project becomes active while the native dialog is awaiting input.
        sid = self.ensure_session()
        if not sid:
            return
        try:
            session = self.call({'cmd': 'chat_get', 'session_id': sid, 'metadata_only': True})
            if not session:
                return
            project_id = session['session'].get('project_id')
            if not project_id:
                return self.notice('先选择项目并新建项目会话，再添加文件')
            from ..project_workspace import get_project
            project_root = Path(get_project(project_id)['path'])
            files = await self.file_picker.pick_files(allow_multiple=True, dialog_title='添加项目内文件')
        except Exception as exc:  # noqa: BLE001 —— 选择器异常要可见，不能静默吞掉
            self.notice(f'选择文件失败：{exc}')
            return
        for file in files or []:
            if file.path:
                try:
                    # Keep lexical children for the backend to reject symlinks;
                    # resolving here could hide a link in the selected path.
                    path = Path(file.path).relative_to(project_root).as_posix()
                except ValueError:
                    self.notice('请选择发起添加的项目目录内的文件')
                    continue
                result = self.call({'cmd': 'chat_attach', 'session_id': sid, 'path': path})
                if result and sid == self.selected():
                    self.refresh()

    def update_preview(self):
        self.preview.content.value = self.request.value or '*暂无输入*'
        if self.preview.visible:
            self.page.update()

    def toggle_preview(self):
        self.preview.visible = not self.preview.visible
        self.update_preview()
        self.page.update()

    def fork(self):
        if not self.selected():
            return self.notice('先打开需要分叉的会话')
        result = self.call({'cmd': 'chat_fork', 'session_id': self.selected()})
        if result:
            self.open_session(result['session_id'])
            self.request.value = ''
            self.refresh()

    def share(self):
        if not self.selected():
            return self.notice('先打开一个会话')
        sid = self.selected()
        def export(format):
            result = self.call({'cmd': 'chat_share', 'session_id': sid, 'format': format})
            if result:
                self.notice('已导出：'+result['path'])
        self.page.show_dialog(ft.AlertDialog(title=ft.Text('分享会话'), content=ft.Text('生成可离线打开的文件，保留可见对话。'),
            actions=[self.button('HTML', lambda: export('html')), self.button('Markdown', lambda: export('markdown')),
                     self.button('JSON', lambda: export('json')), self.button('关闭', self.page.pop_dialog)]))

    def manage(self):
        archived = ft.Switch(label='包括已归档', value=False)
        query = ft.TextField(label='搜索标题或会话 ID')
        rows = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)
        def refresh(e=None):
            result = handle({'cmd': 'chat_sessions', 'include_archived': archived.value, 'query': query.value or ''})
            rows.controls = []
            for item in result.get('sessions', []):
                def open_row(r=item):
                    self.page.pop_dialog()
                    self.open_session(r['session_id'])
                def archive(r=item):
                    self.call({'cmd': 'chat_archive', 'session_id': r['session_id'], 'archived': not r['archived']})
                    refresh()
                def rename(r=item):
                    field = ft.TextField(label='会话标题', value=r['title'])
                    def save():
                        if self.call({'cmd': 'chat_rename', 'session_id': r['session_id'], 'title': field.value}):
                            self.page.pop_dialog()
                            refresh()
                    self.page.show_dialog(ft.AlertDialog(title=ft.Text('重命名'), content=field,
                        actions=[self.button('保存', save), self.button('取消', self.page.pop_dialog)]))
                rows.controls.append(ft.Row([ft.Text(item['title'], expand=True, max_lines=2, color=palette.TEXT),
                    self.button('打开', open_row), self.button('命名', rename), self.button('恢复' if item['archived'] else '归档', archive)]))
            if not rows.controls:
                rows.controls = [ft.Text('没有符合条件的会话', color=palette.TEXT_2)]
            self.page.update()
        archived.on_change, query.on_change = refresh, refresh
        refresh()
        self.page.show_dialog(ft.AlertDialog(title=ft.Text('研究会话'), content=ft.Container(width=660, height=460,
            content=ft.Column([query, archived, rows], expand=True)), actions=[self.button('关闭', self.page.pop_dialog)]))

    def open_side(self):
        sid = self.ensure_session()
        if not sid:
            return
        result = self.call({'cmd': 'chat_fork', 'session_id': sid, 'side_chat': True})
        if result:
            self.epoch += 1
            self.side_sid, self.side.visible = result['session_id'], True
            self.side_send.disabled = False
            self.side_answer.value, self.side_input.value = '', ''
            self.side_notice.value = '独立讨论 · '+self.side_sid
            self.changed()

    def close_side(self):
        self.side.visible = False
        self.changed()

    def _side_draft_changed(self, e=None):
        self._side_draft_generation += 1

    def send_side(self):
        if self.side_send.disabled or not self.side_sid or not (self.side_input.value or '').strip():
            return
        sid, epoch, text = self.side_sid, self.epoch, self.side_input.value
        generation = self._side_draft_generation
        self.side_send.disabled = True
        self.side_notice.value = '正在处理…'
        self.page.update()
        def work():
            try:
                result = handle({'cmd': 'chat_turn', 'session_id': sid, 'request': text})
                if epoch != self.epoch or sid != self.side_sid:
                    return
                if result.get('ok') is not True:
                    raise ValueError(result.get('error') or '未确认发送结果，输入已保留')
                data = result.get('result') or {}
                self.side_answer.value = data.get('content') or '已记录。需要审批时打开完整会话处理。'
                self.side_notice.value = '等待审批，打开完整会话处理' if data.get('status')=='waiting_approval' else data.get('status', '操作结束')
                if generation == self._side_draft_generation and self.side_input.value == text:
                    self.side_input.value = ''
                    self._side_draft_generation += 1
            except Exception as exc:  # noqa: BLE001 — an unconfirmed send keeps the editable request
                if epoch == self.epoch and sid == self.side_sid:
                    self.side_notice.value = str(exc)
            finally:
                if epoch == self.epoch and sid == self.side_sid:
                    self.side_send.disabled = False
                    self.page.update()
        self.page.run_thread(work)
