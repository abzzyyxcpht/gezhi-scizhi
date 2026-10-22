"""成果验收清单面板：只读核对任务记录中的事实，并提供人工复核入口。

判定完全来自后端的确定性检查（``satisfied`` / ``failed`` / ``stale`` /
``pending`` / ``unknown``）。本面板只做展示与触发复核，不改写任务产物；
"通过"只表示任务记录里存在对应依据，不表示科学结论正确。
"""
from __future__ import annotations

import re
from typing import Any, Callable

import flet as ft

from ..bridge import handle
from ..workbench import redact
from .theme import palette as p, radius, style_inputs

# 五档状态 → (符号, 中文标签, 配色角色)。只有 satisfied 是"已核实"。
_STATE = {
    'satisfied': ('✓', '已核实', 'SUCCESS'),
    'failed': ('✗', '未通过', 'DANGER'),
    'stale': ('⚠', '已过期', 'WARN'),
    'pending': ('○', '待人工确认', 'INFO'),
    'unknown': ('?', '无法核验', 'TEXT_2'),
}

# 整体结论只有三档（外加空清单的 unknown），不允许把"部分通过"说成完成。
_VERDICT = {
    'all_satisfied': ('全部检查项已有记录支持', 'SUCCESS'),
    'needs_review': ('需要复核', 'WARN'),
    'incomplete': ('尚未完成', 'ACCENT_TEXT'),
    'unknown': ('无法给出结论', 'TEXT_2'),
}

_KIND = {
    'artifact_present': '产物存在', 'material_integrity': '材料完整性',
    'citation_format': '参考文献完整性', 'deficit_free': '研究包缺口',
    'manual_review': '人工复核',
}

_BOUNDARY = ('本清单只核对任务记录中已有的事实，不判断科学结论是否正确；'
             '未通过只表示现有记录不足以支持「已完成」。')
_STALE_NOTICE = ('原结论需要复核：材料在记录之后发生变化或已不可访问，'
                 '早期判断不再适用。')
_CONTROL = re.compile(r'[\x00-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]')
_MAX_EVIDENCE = 4


def _text(value: Any, limit: int, fallback: str = '') -> str:
    """脱敏 + 去控制字符 + 截断；不做任何"补成通过"的措辞修饰。"""
    if not isinstance(value, str):
        return fallback
    value = _CONTROL.sub(' ', str(redact(value))).strip()
    return (value[:limit - 1] + '…') if len(value) > limit else value or fallback


def _count(value: Any, fallback: int = 0) -> int:
    return value if type(value) is int and 0 <= value <= 100000 else fallback


def _evidence_lines(item: dict[str, Any]) -> list[str]:
    """把 evidence 字典压成有界摘要行；不推断字段含义。"""
    entries = item.get('evidence')
    if not isinstance(entries, list):
        return []
    lines: list[str] = []
    for entry in entries[:_MAX_EVIDENCE]:
        if not isinstance(entry, dict):
            continue
        parts = []
        for key, value in entry.items():
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                parts.append(f'{key}={_text(str(value), 80)}')
        if parts:
            lines.append(_text(' · '.join(parts), 220))
    omitted = len(entries) - _MAX_EVIDENCE
    if omitted > 0:
        lines.append(f'另有 {omitted} 条证据未展开')
    return lines


class OutcomeCheckPanel:
    """成果页的验收清单页签：只读 checklist + 显式人工复核记录。"""

    def __init__(self, page: Any, *,
                 api: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        self.page = page
        self.api = api or handle
        self.task_id: str | None = None
        self.checks: dict[str, Any] | None = None
        self.dialog: ft.AlertDialog | None = None
        self._review_task: str | None = None
        self._epoch = 0
        self._saving = False

        self.title = ft.Text('成果验收清单', size=13,
                             weight=ft.FontWeight.W_600, color=p.TEXT)
        self.count = ft.Text('', size=11, color=p.TEXT_2, max_lines=1)
        self.review_button = ft.FilledButton(
            '记录人工复核', icon=ft.Icons.EDIT_NOTE, disabled=True,
            on_click=lambda e: self.open_review(),
            style=ft.ButtonStyle(bgcolor=p.ACCENT, color=p.ON_ACCENT))
        self.verdict = ft.Text('尚未读取验收清单', size=15,
                               weight=ft.FontWeight.W_600, color=p.TEXT_2, max_lines=2)
        self.summary = ft.Text('', size=12, color=p.TEXT_2, max_lines=4, selectable=True)
        self.boundary = ft.Text(_BOUNDARY, size=11, color=p.TEXT_3, max_lines=4)
        self.notice = ft.Text('', size=12, color=p.DANGER, max_lines=3,
                              selectable=True, visible=False)
        self.empty = ft.Text('正在读取验收清单…', size=12, color=p.TEXT_2,
                             max_lines=4, visible=True)
        self.items = ft.Column([], spacing=8, tight=True)

        self.summary_card = ft.Container(
            bgcolor=p.SURFACE_2, border_radius=radius('panel'), padding=12,
            border=ft.Border.all(1, p.BORDER), visible=False,
            content=ft.Column([self.verdict, self.summary, self.boundary],
                              spacing=4, tight=True))
        self.header = ft.Row(
            [ft.Row([self.title, self.count], spacing=8, tight=True),
             self.review_button],
            spacing=10, wrap=True, alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.content = ft.Container(
            ft.Column([self.header, self.notice, self.empty, self.summary_card,
                       self.items], spacing=10, scroll=ft.ScrollMode.AUTO, expand=True),
            padding=8)

        self.reviewer = ft.TextField(label='复核人', hint_text='填写实际完成复核的人',
                                     width=360, dense=True)
        self.review_note = ft.TextField(
            label='复核结论', hint_text='说明你核对过什么、依据是什么',
            multiline=True, min_lines=2, max_lines=4, width=360)
        self.dialog_error = ft.Text('', size=11, color=p.DANGER, max_lines=2,
                                    visible=False)
        style_inputs(self.reviewer, self.review_note)

    # ---- 状态机：空态 / 读取 / 错误 / 渲染 ----
    def reset(self) -> None:
        """清空清单（切换任务时调用），不保留上一任务的任何内容。"""
        self._epoch += 1
        self.task_id = None
        self.checks = None
        self._saving = False
        self.dialog = None
        self._review_task = None
        self.count.value = ''
        self.verdict.value = '尚未读取验收清单'
        self.verdict.color = p.TEXT_2
        self.summary.value = ''
        self.boundary.value = _BOUNDARY
        self.items.controls = []
        self.summary_card.visible = False
        self.notice.value, self.notice.visible = '', False
        self.empty.value = ('当前没有打开的研究任务。请先在「研究进度」打开任务；'
                            '这里只核对任务记录中已有的产物与人工复核记录。')
        self.empty.visible = True
        self.review_button.disabled = True

    def refresh(self, task_id: str | None, *, force: bool = False) -> None:
        """读取指定任务的验收清单；task_id 为空时显示明确空状态。

        force=False 时，同一任务已成功读取过就不重复请求（页签反复切换不闪屏）。
        """
        tid = str(task_id).strip() if task_id else ''
        if not tid:
            self.reset()
            return
        if not force and tid == self.task_id and self.checks is not None:
            return
        self.task_id = tid
        self._epoch += 1
        epoch = self._epoch
        self.checks = None
        self.items.controls = []
        self.summary_card.visible = False
        self.notice.value, self.notice.visible = '', False
        self.count.value = ''
        self.verdict.value = '正在读取验收清单…'
        self.verdict.color = p.TEXT_2
        self.summary.value = ''
        self.empty.value = '正在读取验收清单…'
        self.empty.visible = True
        self.review_button.disabled = True
        self.page.update()

        def work() -> None:
            try:
                reply = self.api({'cmd': 'outcome_checks', 'task_id': tid})
            except Exception as error:  # noqa: BLE001 —— 读取失败只显示，不抛出到 UI
                reply = {'ok': False, 'error': f'{type(error).__name__}: {error}'}
            if epoch != self._epoch or self.task_id != tid:
                return  # 迟到的回执不得覆盖已切换的任务
            checks = reply.get('checks') if isinstance(reply, dict) else None
            if not isinstance(reply, dict) or reply.get('ok') is not True:
                message = reply.get('error') if isinstance(reply, dict) else ''
                self._show_error(message or '验收清单读取失败')
            elif not isinstance(checks, dict):
                self._show_error('后端没有返回可用的验收清单')
            else:
                self._apply(checks)
            self.page.update()

        self.page.run_thread(work)

    def _show_error(self, message: Any) -> None:
        self.checks = None
        self.items.controls = []
        self.summary_card.visible = False
        self.verdict.value = '验收清单暂不可读'
        self.verdict.color = p.DANGER
        self.count.value = ''
        self.review_button.disabled = self.task_id is None
        self.empty.value = '没有可显示的检查项；请稍后重试或确认已打开有效的研究任务。'
        self.empty.visible = True
        self.notice.value = '暂时无法读取验收清单：' + _text(str(message or ''), 240,
                                                          '未知错误')
        self.notice.visible = True

    def _apply(self, checks: dict[str, Any]) -> None:
        self.checks = checks
        raw_items = checks.get('items')
        items = [row for row in raw_items if isinstance(row, dict)] \
            if isinstance(raw_items, list) else []
        verdict = checks.get('verdict')
        label, role = _VERDICT.get(verdict, _VERDICT['unknown'])
        counts = checks.get('counts') if isinstance(checks.get('counts'), dict) else {}
        total = _count(checks.get('total'), len(items))
        satisfied = _count(checks.get('satisfied'),
                           sum(row.get('state') == 'satisfied' for row in items))
        self.review_button.disabled = self.task_id is None
        if not items:
            self.items.controls = []
            self.summary_card.visible = False
            self.verdict.value = label
            self.verdict.color = getattr(p, role)
            self.count.value = '0 项'
            self.empty.value = ('该任务还没有可核验的成果记录；完成研究节点后，'
                                '这里会显示逐项验收结果。')
            self.empty.visible = True
            return
        self.empty.visible = False
        self.summary_card.visible = True
        self.verdict.value = label
        self.verdict.color = getattr(p, role)
        self.count.value = f'{satisfied}/{total} 项已有记录支持'
        pieces = [f'未通过 {_count(counts.get("failed"))}',
                  f'需复核 {_count(counts.get("stale"))}',
                  f'待人工确认 {_count(counts.get("pending"))}',
                  f'无法核验 {_count(counts.get("unknown"))}']
        line = ' · '.join(pieces)
        if verdict == 'needs_review':
            line += '。现有记录不足以支持「已完成」，需要人工复核。'
        elif verdict == 'incomplete':
            line += '。尚未完成：还有检查项没有记录或等待人工确认。'
        elif verdict == 'all_satisfied':
            line += '。清单核对的是任务记录中的事实，不代表科学结论正确。'
        else:
            line += '。无法给出整体结论，请查看逐项状态。'
        self.summary.value = str(redact(line))
        self.boundary.value = _text(checks.get('boundary'), 400, _BOUNDARY)
        self.items.controls = [self._item(row) for row in items]

    def _item(self, item: dict[str, Any]) -> ft.Control:
        state = item.get('state')
        state = state if isinstance(state, str) and state in _STATE else 'unknown'
        symbol, label, role = _STATE[state]
        color = getattr(p, role)
        title = _text(item.get('title'), 120, '未命名检查项')
        criterion = _text(item.get('criterion'), 400, '未记录验收条件')
        detail = _text(item.get('detail'), 400, '未记录判定说明')
        kind = _KIND.get(item.get('kind'), '检查项')
        controls: list[ft.Control] = [
            ft.Row([ft.Text(symbol, size=15, color=color, width=20),
                    ft.Text(f'{label} · {title}', size=13,
                            weight=ft.FontWeight.W_600, color=color,
                            max_lines=2, expand=True),
                    ft.Text(kind, size=11, color=p.TEXT_3, max_lines=1)],
                   spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text('验收条件 · ' + criterion, size=11, color=p.TEXT_2,
                    selectable=True, max_lines=3, tooltip=criterion),
            ft.Text('判定说明 · ' + detail, size=11, color=p.TEXT_2,
                    selectable=True, max_lines=3, tooltip=detail),
        ]
        if state == 'stale':
            controls.append(ft.Container(
                bgcolor=p.WARN_BG, border_radius=radius('small'),
                padding=ft.Padding(8, 6, 8, 6),
                content=ft.Text(_STALE_NOTICE, size=11, color=p.WARN, max_lines=3)))
        evidence = _evidence_lines(item)
        if evidence:
            controls.append(ft.Text('证据 · ' + '；'.join(evidence), size=11,
                                    color=p.TEXT_3, selectable=True, max_lines=3))
        elif state != 'satisfied':
            controls.append(ft.Text('证据 · 任务记录中没有可引用的依据', size=11,
                                    color=p.TEXT_3, max_lines=1))
        return ft.Container(
            ft.Column(controls, spacing=4, tight=True), padding=10,
            bgcolor=p.SURFACE, border_radius=radius('control'),
            border=ft.Border.all(1, p.BORDER))

    # ---- 人工复核入口 ----
    def open_review(self) -> None:
        """弹窗填写复核人与结论；写入成功后重新读取清单。"""
        if not self.task_id:
            self.notice.value = '请先打开一个有效的研究任务，再记录人工复核。'
            self.notice.visible = True
            self.page.update()
            return
        self._review_task = self.task_id
        self.reviewer.value = ''
        self.review_note.value = ''
        self.dialog_error.value, self.dialog_error.visible = '', False
        self.dialog = ft.AlertDialog(
            title=ft.Text('记录人工复核'),
            content=ft.Container(width=420, content=ft.Column([
                ft.Text('复核记录只来自你的显式动作，不会改写任务产物，'
                        '也不代表科学结论经过验证。', size=11, color=p.TEXT_3,
                        max_lines=3),
                self.reviewer, self.review_note, self.dialog_error,
            ], spacing=10, tight=True)),
            actions=[ft.TextButton('取消', on_click=lambda e: self.close_review()),
                     ft.FilledButton('保存复核', on_click=lambda e: self.save_review())],
            on_dismiss=lambda e: self._dismiss_review())
        style_inputs(self.dialog)
        self.page.show_dialog(self.dialog)

    def _dismiss_review(self) -> None:
        """弹窗被系统关闭（遮罩/返回键）时只清本地引用，不重复 pop_dialog。"""
        self.dialog = None
        self._review_task = None

    def close_review(self) -> None:
        self._dismiss_review()
        self.page.pop_dialog()

    def save_review(self) -> None:
        """写入一次人工复核；字段不全或后端拒绝时停留在弹窗并给出原因。"""
        if self.dialog is None:
            self.notice.value = '请先打开复核输入框，再保存复核记录。'
            self.notice.visible = True
            self.page.update()
            return
        tid = self._review_task
        if not tid:
            self.dialog_error.value = '请先打开一个有效的研究任务。'
            self.dialog_error.visible = True
            self.page.update()
            return
        if tid != self.task_id:
            # 弹窗打开期间切换了任务：绝不把复核写到另一个任务上。
            self.dialog_error.value = '任务已切换，请关闭后重新打开复核入口。'
            self.dialog_error.visible = True
            self.page.update()
            return
        reviewer = str(self.reviewer.value or '').strip()
        note = str(self.review_note.value or '').strip()
        if not reviewer or not note:
            self.dialog_error.value = '请填写复核人与复核结论。'
            self.dialog_error.visible = True
            self.page.update()
            return
        if self._saving:
            return
        self._saving = True
        try:
            reply = self.api({'cmd': 'outcome_review', 'task_id': tid,
                              'record': {'by': reviewer, 'note': note}})
        except Exception as error:  # noqa: BLE001 —— 写失败留在弹窗，不关闭
            reply = {'ok': False, 'error': f'{type(error).__name__}: {error}'}
        finally:
            self._saving = False
        if not isinstance(reply, dict) or reply.get('ok') is not True:
            message = reply.get('error') if isinstance(reply, dict) else ''
            self.dialog_error.value = '复核未保存：' + _text(str(message or ''), 240,
                                                          '后端未返回有效回执')
            self.dialog_error.visible = True
            self.page.update()
            return
        self.close_review()
        self.page.show_dialog(ft.SnackBar(
            ft.Text('已记录人工复核；正在重新读取验收清单。')))
        self.refresh(tid, force=True)
