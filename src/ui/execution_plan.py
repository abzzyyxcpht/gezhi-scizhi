"""Read-only execution steps with explicit completion evidence."""
from __future__ import annotations

import re

import flet as ft

from ..workbench import redact
from .theme import motion_duration, palette as p, radius, style_inputs

_STEP_LIMIT = 32
_RECEIPT_LIMIT = 4
_CONTROL_CHARACTERS = re.compile(r'[\x00-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]')
_STATUS = {
    'pending': ('未开始', 'TEXT_3', ft.Icons.RADIO_BUTTON_UNCHECKED),
    'in_progress': ('进行中', 'ACCENT_TEXT', ft.Icons.PLAY_CIRCLE_OUTLINE),
    'completed': ('已完成', 'SUCCESS', ft.Icons.CHECK_CIRCLE_OUTLINE),
    'blocked': ('受阻', 'WARN', ft.Icons.PAUSE_CIRCLE_OUTLINE),
    'cancelled': ('已取消', 'TEXT_3', ft.Icons.CANCEL_OUTLINED),
    'unknown': ('状态未记录', 'TEXT_3', ft.Icons.HELP_OUTLINE),
}


def _text(value, limit: int, fallback: str = '') -> str:
    if not isinstance(value, str):
        return fallback
    # Redaction precedes truncation so a clipped credential cannot evade matching.
    value = _CONTROL_CHARACTERS.sub(' ', redact(value)).strip()
    return (value[:limit - 1] + '…') if len(value) > limit else value or fallback


def _count(value, fallback: int) -> int:
    return value if type(value) is int and 0 <= value <= 100000 else fallback


def _snapshot(plan) -> dict | None:
    """Project only fields displayed by the card; never retain raw tool output."""
    if not isinstance(plan, dict) or plan.get('has_plan') is not True:
        return None
    raw_steps = plan.get('steps')
    if not isinstance(raw_steps, list) or not raw_steps:
        return None
    rows = []
    for item in raw_steps[:_STEP_LIMIT]:
        if not isinstance(item, dict):
            continue
        status = item.get('status')
        status = status if isinstance(status, str) and status in _STATUS else 'unknown'
        kind = item.get('evidence_kind')
        kind = kind if kind in ('tool_receipt', 'model_statement') else 'none'
        receipts = []
        details = item.get('evidence_details')
        if isinstance(details, list):
            for receipt in details[:_RECEIPT_LIMIT]:
                if isinstance(receipt, dict):
                    receipts.append((
                        _text(receipt.get('tool'), 80, '工具'),
                        _text(receipt.get('call_id'), 80),
                        _text(receipt.get('summary'), 320, '回执摘要未记录'),
                    ))
        evidence = item.get('evidence')
        evidence = [_text(value, 80) for value in evidence[:_RECEIPT_LIMIT]
                    if isinstance(value, str) and value.strip()] if isinstance(evidence, list) else []
        rows.append({
            'id': _text(item.get('id'), 80), 'title': _text(item.get('title'), 180, '未命名步骤'),
            'criterion': _text(item.get('criterion'), 420, '验收条件未记录'),
            'status': status, 'kind': 'reasoning' if item.get('kind') == 'reasoning' else 'execution',
            'reason': _text(item.get('reason'), 360),
            'evidence_kind': kind, 'receipts': receipts, 'evidence': evidence,
        })
    if not rows:
        return None
    total = max(_count(plan.get('total'), len(raw_steps)), len(rows))
    completed = min(_count(plan.get('completed'), sum(row['status'] == 'completed' for row in rows)), total)
    return {
        'identity': (_text(plan.get('session_id'), 128), _text(plan.get('turn_id'), 128)),
        'completed': completed, 'total': total, 'ready': plan.get('ready') is True,
        'rows': rows, 'omitted': max(0, len(raw_steps) - _STEP_LIMIT),
    }


class ExecutionPlanPanel:
    """Update from session_status without requests, input focus or automatic expansion."""

    def __init__(self):
        self._snapshot = None
        self._read_error = False
        self.title = ft.Text('执行计划', size=12, weight=ft.FontWeight.W_600, color=p.TEXT, expand=True)
        self.count = ft.Text('', size=12, color=p.TEXT_2)
        self.current = ft.Text('', size=12, color=p.TEXT_2, max_lines=2,
                               overflow=ft.TextOverflow.ELLIPSIS)
        self.progress = ft.ProgressBar(value=0, bar_height=2, color=p.ACCENT,
                                       bgcolor=p.BORDER, border_radius=1,
                                       semantics_label='执行计划完成进度')
        self.notice = ft.Text('状态暂不可读，以下为上次记录。', size=11, color=p.WARN, visible=False)
        self.steps = ft.Column([], spacing=6, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.details = ft.ExpansionTile(
            title=ft.Row([self.title, self.count], spacing=8), subtitle=self.current,
            controls=[self.steps], expanded=False, maintain_state=True, dense=True,
            min_tile_height=42, tile_padding=0, controls_padding=ft.Padding(0, 8, 0, 4),
            expanded_cross_axis_alignment=ft.CrossAxisAlignment.STRETCH,
            shape=ft.RoundedRectangleBorder(radius=0),
            collapsed_shape=ft.RoundedRectangleBorder(radius=0),
            icon_color=p.TEXT_2, collapsed_icon_color=p.TEXT_2,
            animation_style=ft.AnimationStyle(duration=motion_duration(), reverse_duration=motion_duration()),
        )
        self.content = ft.Container(
            ft.Column([self.details, self.progress, self.notice], spacing=4, tight=True),
            padding=ft.Padding(12, 4, 12, 8), border=ft.Border.all(1, p.BORDER),
            border_radius=radius('control'), bgcolor=p.SURFACE_2, visible=False,
        )
        style_inputs(self.content)

    @staticmethod
    def _step(row, number: int):
        label, role, icon = _STATUS[row['status']]
        color = getattr(p, role)
        # Selectable text with max_lines reserves blank rows in Flutter. Character
        # limits already bound these fields; let the activity area handle scrolling.
        title = ft.Text(row['title'], size=12, weight=ft.FontWeight.W_500, color=p.TEXT,
                        selectable=True, tooltip=row['title'])
        content = [
            ft.Row([ft.Icon(icon, size=15, color=color),
                    ft.Text(f'{number:02d} · {label}', size=11, color=color)], spacing=6),
            title,
            ft.Text('验收 · ' + row['criterion'], size=11, color=p.TEXT_2, selectable=True,
                    tooltip=row['criterion']),
        ]
        if row['reason']:
            prefix = '阻塞原因 · ' if row['status'] == 'blocked' else '说明 · '
            content.append(ft.Text(prefix + row['reason'], size=11, color=p.WARN if row['status'] == 'blocked' else p.TEXT_2,
                                    selectable=True, tooltip=row['reason']))
        if row['evidence_kind'] == 'tool_receipt':
            evidence_label = '工具回执已关联'
            evidence_color = p.TEXT_2
        elif row['evidence_kind'] == 'model_statement':
            evidence_label = '模型声明'
            evidence_color = p.TEXT_2
        else:
            evidence_label = '尚未关联完成依据'
            evidence_color = p.TEXT_3
        content.append(ft.Text(evidence_label, size=11, color=evidence_color))
        if row['evidence_kind'] == 'tool_receipt':
            if row['receipts']:
                for tool, call_id, summary in row['receipts']:
                    reference = f' · {call_id}' if call_id else ''
                    content.append(ft.Text(f'{tool}{reference}\n{summary}', size=11,
                        color=p.TEXT_2, selectable=True, tooltip=summary))
            elif row['evidence']:
                content.append(ft.Text('回执 · ' + '、'.join(row['evidence']), size=11,
                                        color=p.TEXT_2, selectable=True))
        return ft.Container(ft.Column(content, spacing=4, tight=True), padding=ft.Padding(10, 8, 10, 8),
                            bgcolor=p.SURFACE, border_radius=radius('control'))

    def update(self, plan, *, read_error: bool = False) -> bool:
        snapshot = _snapshot(plan)
        payload_error = (isinstance(plan, dict) and isinstance(plan.get('error'), str)
                         and bool(plan['error'].strip()))
        if payload_error:
            same_session = (self._snapshot is not None
                            and self._snapshot['identity'][0] == _text(plan.get('session_id'), 128))
            snapshot = self._snapshot if same_session else None
        error = bool(payload_error or (read_error and snapshot))
        if snapshot == self._snapshot and error == self._read_error:
            return False
        previous = self._snapshot
        self._snapshot, self._read_error = snapshot, error
        self.content.visible = snapshot is not None or error
        self.details.visible = self.progress.visible = snapshot is not None
        self.notice.visible = error
        self.notice.value = ('状态暂不可读，以下为上次记录。' if snapshot is not None
                             else '执行计划暂不可读，请稍后重试。')
        if snapshot is None:
            self.details.expanded = False
            self.steps.controls = []
            self.count.value = self.current.value = ''
            self.current.tooltip = None
            self.progress.value = 0
            return True
        if previous is None or previous['identity'] != snapshot['identity']:
            self.details.expanded = False
        if snapshot != previous:
            completed, total = snapshot['completed'], snapshot['total']
            self.count.value = f'{completed}/{total}'
            self.progress.value = completed / total if total else 0
            self.progress.semantics_value = round(self.progress.value * 100)
            rows = snapshot['rows']
            current = next((row for state in ('in_progress', 'blocked', 'pending', 'unknown')
                            for row in rows if row['status'] == state), None)
            if current:
                self.current.value = _STATUS[current['status']][0] + ' · ' + current['title']
                self.current.color = getattr(p, _STATUS[current['status']][1])
            else:
                self.current.value = ('步骤已完成 · 可查看完成依据' if snapshot['ready']
                                      else '步骤已结束 · 请查看完成依据')
                self.current.color = p.TEXT_2
            self.current.tooltip = self.current.value
            self.steps.controls = [self._step(row, number) for number, row in enumerate(rows, 1)]
            if snapshot['omitted']:
                self.steps.controls.append(ft.Text(f'另有 {snapshot["omitted"]} 个步骤未显示。',
                                                    size=11, color=p.TEXT_3))
            self.steps.controls.append(ft.Text('完成依据记录执行过程，科研结论仍需复核。',
                                                size=11, color=p.TEXT_3))
            style_inputs(self.content)
        return True
