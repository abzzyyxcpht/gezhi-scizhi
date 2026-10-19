"""Small, read-only session metrics and explicit local context maintenance."""
from __future__ import annotations

import time
from typing import Callable

import flet as ft

from ..bridge import handle
from ..workbench import redact
from .control_tower import STATUS
from .execution_plan import ExecutionPlanPanel
from .theme import palette as p, style_inputs


def _number(value) -> str:
    return f'{value:,}' if isinstance(value, int) and not isinstance(value, bool) else '未报告'


_MANIFEST_STATUS = {
    'captured': '已冻结',
    'included': '已纳入', 'budget_deferred': '因预算延期', 'disabled': '已停用',
    'manual_only': '禁止模型调用（手动入口待支持）', 'unsupported': '不支持', 'unapproved': '未获批准',
    'missing': '缺失', 'error': '读取失败', 'loaded': '正文已读取', 'partial': '部分正文已读取',
}
_MANIFEST_UNITS = {'characters': '字符', 'chars': '字符', 'tokens': 'token'}
_PURPOSES = {'general': '通用', 'research': '科研'}
_EXECUTIONS = {'agent': '执行任务', 'plan': '只读规划', 'review': '证据审查'}
_SOURCE_KINDS = {'instructions': '项目规则', 'skill_index': '技能索引',
                 'skill_body': '技能正文', 'memory': '记忆'}
_SOURCE_SCOPES = {'user': '用户', 'project': '项目', 'local': '项目本地',
                  'stage': '阶段', 'session': '会话', 'global': '用户',
                  'application': '程序目录', 'working_directory': '工作目录'}
_SOURCE_LIMIT = 24
_RESEARCH_CONTEXT_LIMIT = 4


def _manifest_value(value) -> str:
    """Render a bounded manifest scalar without exposing prompt or memory text."""
    if isinstance(value, int) and not isinstance(value, bool):
        return f'{value:,}'
    if isinstance(value, str) and value.strip():
        return redact(value.strip())[:180]
    return '未记录'


def execution_context_identity(status: dict | None) -> str:
    """Separate workspace purpose, agent role and execution mode in one line."""
    row = status if isinstance(status, dict) else {}
    purpose = _PURPOSES.get(str(row.get('workspace_mode')), _manifest_value(row.get('workspace_mode')))
    execution = _EXECUTIONS.get(str(row.get('mode')), _manifest_value(row.get('mode')))
    return ('用途 ' + purpose
            + ' · 角色 ' + _manifest_value(row.get('agent'))
            + ' · 执行 ' + execution)


def context_manifest_lines(manifest: dict | None) -> list[str]:
    """Format the persisted context manifest as safe, bounded inspector rows.

    The manifest is metadata only.  Unknown or legacy sessions stay explicit;
    this helper never falls back to current global settings and never renders
    arbitrary source fields such as prompt/body/content.
    """
    if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        return ['执行上下文来源：未记录（旧会话或规则流程）']
    status = _MANIFEST_STATUS.get(str(manifest.get('status') or ''), '未记录')
    basis = '冻结前缀' if manifest.get('basis') == 'frozen_prefix' else _manifest_value(manifest.get('basis'))
    prompt = _manifest_value(manifest.get('prompt_version'))
    lines = [f'执行上下文来源：{status} · 基于{basis} · prompt v{prompt}']
    lines.append('记录时间 ' + _manifest_value(manifest.get('captured_at')))
    lines.append('用途 ' + _PURPOSES.get(str(manifest.get('workspace_mode')), _manifest_value(manifest.get('workspace_mode')))
                 + ' · 角色 ' + _manifest_value(manifest.get('agent'))
                 + ' · 阶段 ' + _manifest_value(manifest.get('stage'))
                 + ' · 项目 ' + _manifest_value(manifest.get('project_id')))
    budget = manifest.get('budget')
    if isinstance(budget, dict):
        unit = _MANIFEST_UNITS.get(str(budget.get('unit') or '').lower(), _manifest_value(budget.get('unit')))
        used, limit = _manifest_value(budget.get('used_chars')), _manifest_value(budget.get('limit_chars'))
        lines.append(f'预算：{unit} {used}/{limit}')
    else:
        lines.append('预算：未记录')
    sources = manifest.get('sources')
    if not isinstance(sources, list) or not sources:
        lines.append('  · 来源：未记录')
    else:
        for source in sources[:_SOURCE_LIMIT]:
            if not isinstance(source, dict):
                continue
            kind = _SOURCE_KINDS.get(str(source.get('kind')), _manifest_value(source.get('kind')))
            name = _manifest_value(source.get('name'))
            scope = _SOURCE_SCOPES.get(str(source.get('scope')), _manifest_value(source.get('scope')))
            state = _MANIFEST_STATUS.get(str(source.get('status') or ''), '未记录')
            if source.get('kind') == 'skill_index' and source.get('status') == 'included':
                state = '索引已纳入（正文按需读取）'
            used = _manifest_value(source.get('used_chars'))
            total = _manifest_value(source.get('total_chars'))
            line = f'  · {kind} · {name} · {scope} · {state} · 字符 {used}/{total}'
            path = source.get('path')
            if isinstance(path, str) and path.strip():
                line += f' · 路径 {_manifest_value(path)}'
            reason = source.get('reason')
            if isinstance(reason, str) and reason.strip():
                line += f' · {redact(reason.strip())[:180]}'
            lines.append(line)
    omitted = manifest.get('omitted_sources')
    omitted = omitted if type(omitted) is int and omitted > 0 else 0
    omitted += max(0, len(sources) - _SOURCE_LIMIT) if isinstance(sources, list) else 0
    if omitted:
        lines.append(f'另有 {omitted} 个来源未显示（有界快照）')
    notices = manifest.get('notices')
    if isinstance(notices, list):
        for notice in notices[:8]:
            if isinstance(notice, str) and notice.strip():
                lines.append('提示：' + redact(notice.strip())[:220])
    return lines


def session_context_lines(status: dict | None) -> list[str]:
    """Keep the main prefix separate from observed internal model requests."""
    row = status if isinstance(status, dict) else {}
    main = context_manifest_lines(row.get('context_manifest'))
    research = row.get('research_contexts')
    if not isinstance(research, dict) or type(research.get('schema_version')) is not int or research['schema_version'] != 1:
        return main
    entries = research.get('entries')
    entries = [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []
    issue = research.get('issue')
    issue = _manifest_value(issue) if isinstance(issue, str) and issue.strip() else ''
    if not entries and not issue:
        return main
    recent = entries[-_RESEARCH_CONTEXT_LIMIT:]
    lines = ['主会话前缀', *main, '', '近期工具内部模型请求']
    if issue:
        lines.append(issue)
    for entry in recent:
        outcome = {'returned': '请求已返回', 'error': '请求失败'}.get(str(entry.get('outcome')), '请求状态未记录')
        lines.append('请求 ' + _manifest_value(entry.get('request_id'))
                     + ' · 节点 ' + _manifest_value(entry.get('node'))
                     + ' · 提示 ' + _manifest_value(entry.get('prompt_key')) + ' · ' + outcome)
        lines.append('请求记录 ' + _manifest_value(entry.get('observed_at')))
        lines.extend(context_manifest_lines(entry.get('context_manifest')))
        lines.append('')
    omitted = research.get('omitted_entries')
    omitted = omitted if type(omitted) is int and omitted > 0 else 0
    omitted += max(0, len(entries) - _RESEARCH_CONTEXT_LIMIT)
    if omitted:
        lines.append(f'另有 {omitted} 条工具内部模型请求未显示（有界快照）')
    elif lines[-1] == '':
        lines.pop()
    return lines


class SessionStatusPanel:
    """Fetches bounded status without rebuilding the transcript or touching drafts."""

    MIN_REFRESH_SECONDS = 1.0

    def __init__(self, page, selected: Callable, *, epoch=lambda: 0, busy=lambda: False,
                 api=None, clock=None, on_status=None):
        self.page, self.selected, self.epoch, self.busy = page, selected, epoch, busy
        self.api, self.clock, self.on_status = api or handle, clock or time.monotonic, on_status
        self.status = None
        self._view = None
        self._sequence = 0
        self._fetching = False
        self._compacting = False
        self._read_error = False
        self._last_request = float('-inf')
        self.summary = ft.Text('打开会话查看状态', size=12, color=p.TEXT_2, expand=True,
                               selectable=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.metrics = ft.Text('', size=12, color=p.TEXT_2, selectable=True, max_lines=1,
                               overflow=ft.TextOverflow.ELLIPSIS)
        self.execution_scope = ft.Text('', size=12, color=p.TEXT_2, selectable=True,
                                       max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.context_detail = ft.Text('', size=12, color=p.TEXT_2, selectable=True)
        self.context_source_detail = ft.Text('', size=11, color=p.TEXT_2, selectable=True)
        self.usage = ft.Text('', size=12, color=p.TEXT_2, selectable=True)
        self.budget = ft.Text('', size=12, color=p.TEXT_2, selectable=True)
        self.identity = ft.Text('', size=11, color=p.TEXT_3, selectable=True)
        self.warning = ft.Text('', size=11, color=p.WARN, visible=False)
        self.waiting = ft.Text('', size=12, color=p.TEXT_2, visible=False)
        self.reason = ft.Text('打开会话后可查看整理条件。', size=11, color=p.TEXT_3, max_lines=2,
                              overflow=ft.TextOverflow.ELLIPSIS)
        self.feedback = ft.Text('', size=12, color=p.TEXT_2, visible=False, selectable=True)
        self.execution_plan = ExecutionPlanPanel()
        self.compact_button = ft.TextButton('整理上下文', icon=ft.Icons.COMPRESS, disabled=True,
            tooltip='用本地规则整理活跃上下文，不调用模型；对话历史和当前草稿保留。',
            on_click=lambda e: self.compact(), style=ft.ButtonStyle(color=p.ACCENT_TEXT))
        self.details = ft.ExpansionTile(title=ft.Text('用量与预算', size=12, color=p.TEXT_2),
            controls=[self.context_detail, self.usage, self.budget, self.identity], dense=True, min_tile_height=30,
            tile_padding=0, controls_padding=ft.Padding(0, 0, 0, 6), maintain_state=True,
            shape=ft.RoundedRectangleBorder(radius=0),
            collapsed_shape=ft.RoundedRectangleBorder(radius=0))
        # Context provenance is intentionally collapsed: it is useful for audit
        # and recovery, but must not displace the request, approval, or transcript.
        self.context_sources = ft.ExpansionTile(
            title=ft.Text('执行上下文来源', size=12, color=p.TEXT_2),
            controls=[self.context_source_detail], dense=True, expanded=False,
            min_tile_height=30, tile_padding=0, maintain_state=True,
            expanded_alignment=ft.Alignment.TOP_LEFT,
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.START,
            controls_padding=ft.Padding(0, 0, 0, 6),
            shape=ft.RoundedRectangleBorder(radius=0),
            collapsed_shape=ft.RoundedRectangleBorder(radius=0))
        self.content = ft.Column([
            ft.Row([ft.Text('会话状态', size=12, weight=ft.FontWeight.W_600, color=p.TEXT),
                    self.summary, self.compact_button], spacing=8),
            self.execution_scope, self.metrics, self.waiting, self.warning,
            self.execution_plan.content,
            self.context_sources, self.details, self.reason, self.feedback,
        ], spacing=4)
        style_inputs(self.content)
        self._render()

    def _key(self):
        return self.selected(), self.epoch()

    def _valid(self, view, sequence):
        return self._view == view == self._key() and self._sequence == sequence

    def _sync_view(self):
        view = self._key()
        if view == self._view:
            return False
        self._view = view
        self._sequence += 1
        self._fetching = self._compacting = self._read_error = False
        self._last_request = float('-inf')
        self.status = None
        self.feedback.value, self.feedback.visible = '', False
        return True

    @staticmethod
    def _set(control, key, value):
        if getattr(control, key) == value:
            return False
        setattr(control, key, value)
        return True

    def _render(self):
        row = self.status or {}
        sid = self.selected()
        model = row.get('model') or {}
        model_name = ('规则流程' if row.get('driver') == 'planned' else
                      str(model.get('model') or '未记录模型'))
        state = STATUS.get(row.get('status'), row.get('status') or '未知状态')
        summary = f'{state} · {model_name}' if row else ('正在读取状态…' if sid else '打开会话查看状态')
        context, budget, usage = row.get('context'), row.get('budget'), row.get('session_usage')
        execution_scope = execution_context_identity(row) if row else ''
        manifest_lines = session_context_lines(row) if row else []
        if context:
            used, limit = _number(context.get('tokens')), context.get('limit_tokens')
            context_text = (f'估算上下文 {used} / {_number(limit)} tokens' if isinstance(limit, int) and limit > 0
                            else f'估算上下文 {used} tokens · 上限未记录')
            context_text += f' · 整理 {_number(context.get("compressions"))} 次'
        else:
            context_text = '上下文未记录'
        if budget:
            context_text += f' · 本轮调用 {_number(budget.get("model_calls"))}/{_number(budget.get("max_steps"))}'
            budget_text = (f'本轮已报送 {_number(row.get("tokens"))} tokens · '
                f'本轮额度 {_number(budget.get("token_limit"))} · 剩余 {_number(budget.get("remaining_tokens"))} tokens'
                f' · 剩余调用 {_number(budget.get("remaining_steps"))}')
        else:
            budget_text = f'本轮已报送 {_number(row.get("tokens"))} tokens · 本轮预算未记录'
        if row.get('usage_complete') is None:
            budget_text += ' · 用量完整性未记录'
        usage_text = (f'会话累计已报送 {_number(usage.get("tokens"))} tokens · '
                      f'模型调用 {_number(usage.get("model_calls"))} 次' if usage else '会话累计用量未记录')
        if usage and usage.get('usage_complete') is None:
            usage_text += ' · 用量完整性未记录'
        partial = row.get('usage_complete') is False or bool(usage and usage.get('usage_complete') is False)
        warning = '部分调用未报送用量；以上数字仅包含已报告部分。' if partial else ''
        reason = ''
        if self._compacting:
            reason = '正在整理上下文…'
        elif self._read_error:
            reason = '状态暂不可读，恢复读取后可整理。'
        elif self.busy():
            reason = '当前会话正在执行，请完成或停止后再整理。'
        elif not row:
            reason = '打开会话后可查看整理条件。' if not sid else '读取状态后显示整理条件。'
        elif not row.get('can_compact'):
            reason = row.get('compact_unavailable_reason') or '当前会话暂不支持整理上下文。'
        identity = f'会话 {row.get("session_id", sid) or "未选择"} · 模型 {model_name} · 协议 {model.get("protocol") or "未记录"}'
        if context:
            identity += f' · 活跃消息 {_number(context.get("messages"))}'
            if context.get('input_tokens') is not None:
                identity += f' · 估算总输入 {_number(context.get("input_tokens"))}'
                identity += f'（工具定义 {_number(context.get("schema_tokens"))}）'
                identity += f' · 输出预留估算 {_number(context.get("output_reserve_tokens"))}'
        updates = [
            (self.summary, 'value', summary), (self.summary, 'tooltip', summary),
            (self.execution_scope, 'value', execution_scope),
            (self.execution_scope, 'visible', bool(row)),
            (self.execution_scope, 'tooltip', execution_scope),
            (self.metrics, 'value', context_text), (self.metrics, 'visible', bool(row)),
            (self.metrics, 'tooltip', context_text), (self.context_detail, 'value', context_text),
            (self.usage, 'value', usage_text), (self.budget, 'value', budget_text),
            (self.identity, 'value', identity), (self.details, 'visible', bool(row)),
            (self.context_source_detail, 'value', '\n'.join(manifest_lines)),
            (self.context_sources, 'visible', bool(row)),
            (self.warning, 'value', warning), (self.warning, 'visible', bool(warning)),
            (self.waiting, 'value', row.get('waiting_reason') or ''),
            (self.waiting, 'visible', bool(row.get('waiting_reason'))),
            (self.reason, 'value', reason), (self.reason, 'visible', bool(reason)),
            (self.reason, 'tooltip', reason),
            (self.compact_button, 'disabled', bool(reason) or not bool(row.get('can_compact'))),
        ]
        changed = self.execution_plan.update(row.get('execution_plan'), read_error=self._read_error)
        for control, attribute, value in updates:
            changed = self._set(control, attribute, value) or changed
        return changed

    def _error(self, message, *, operation='状态暂不可读'):
        self._read_error = True
        changed = self._set(self.feedback, 'value', operation + '：' + str(redact(str(message or '请稍后重试'))))
        changed = self._set(self.feedback, 'color', p.DANGER) or changed
        changed = self._set(self.feedback, 'visible', True) or changed
        if self._render() or changed:
            self.page.update()

    def _accept_status(self, row):
        if not isinstance(row, dict) or row.get('session_id') != self.selected():
            raise ValueError('返回的会话状态不匹配')
        self.status = row
        cleared_error = self._read_error
        self._read_error = False
        if cleared_error:
            self.feedback.value, self.feedback.visible = '', False
        changed = bool(self.on_status(row)) if self.on_status else False
        return self._render() or changed or cleared_error

    def refresh(self, *, force=False):
        switched = self._sync_view()
        changed = self._render()
        if switched or changed:
            self.page.update()
        sid = self.selected()
        if not sid or self._fetching or self._compacting:
            return
        now = self.clock()
        if not force and now - self._last_request < self.MIN_REFRESH_SECONDS:
            return
        self._last_request = now
        self._fetching = True
        self._sequence += 1
        view, sequence = self._view, self._sequence

        def work():
            try:
                result = self.api({'cmd': 'session_status', 'session_id': sid})
                if not self._valid(view, sequence):
                    return
                self._fetching = False
                if not result.get('ok'):
                    raise ValueError(result.get('error') or '状态读取失败')
                if self._accept_status(result.get('status')):
                    self.page.update()
            except Exception as error:  # noqa: BLE001 — keep the last good same-session display
                if self._valid(view, sequence):
                    self._fetching = False
                    self._error(error)

        self.page.run_thread(work)

    def compact(self):
        self._sync_view()
        if self._render():
            self.page.update()
        if self.compact_button.disabled or not self.status or self._compacting:
            return
        sid = self.selected()
        self._compacting = True
        self._fetching = False
        self._sequence += 1  # an older read may not overwrite the compacted result
        view, sequence = self._view, self._sequence
        self._render()
        self.page.update()

        def work():
            try:
                response = self.api({'cmd': 'session_compact', 'session_id': sid})
                if not self._valid(view, sequence):
                    return
                self._compacting = False
                if not response.get('ok'):
                    raise ValueError(response.get('error') or '整理失败')
                self._accept_status(response.get('status'))
                result = response.get('result') or {}
                if result.get('changed'):
                    text = (f'已整理：估算 {_number(result.get("before_tokens"))} → '
                            f'{_number(result.get("after_tokens"))} tokens')
                    if result.get('method') == 'deterministic' and result.get('model_calls') == 0:
                        text += ' · 不调用模型'
                    self.feedback.color = p.SUCCESS
                    message = str(result.get('message') or '').strip()
                    if message:
                        text += '\n' + message
                        # A committed change can still report an audit/storage
                        # warning. Preserve the backend explanation without
                        # presenting the whole mixed outcome as green success.
                        self.feedback.color = p.TEXT_2
                else:
                    text = str(result.get('message') or '当前上下文无需整理')
                    self.feedback.color = p.TEXT_2
                self.feedback.value, self.feedback.visible = str(redact(text)), True
                self._last_request = self.clock()
                self._render()
                self.page.update()
            except Exception as error:  # noqa: BLE001 — backend refusal remains actionable
                if self._valid(view, sequence):
                    self._compacting = False
                    self._error(error, operation='整理上下文失败')

        self.page.run_thread(work)
