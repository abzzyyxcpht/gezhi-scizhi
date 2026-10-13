"""Read-only support/challenge comparison with explicit source and scope labels."""
from __future__ import annotations

import flet as ft

from ..bridge import handle
from ..workbench import redact
from .theme import palette as p, style_inputs

RELATIONS = {'supports': '支持', 'challenges': '反驳', 'context': '背景'}
SCOPES = {'metadata': '元数据', 'abstract': '摘要', 'full_text': '全文', 'fulltext': '全文', 'user_observation': '实验观察'}


class EvidenceBriefPanel:
    def __init__(self, page, request, task_id, *, api=None, on_draft_changed=None, open_records=None, open_reviews=None):
        self.page, self.request, self.task_id = page, request, task_id
        self.api, self.on_draft_changed, self.open_records = api or handle, on_draft_changed, open_records
        self.records, self.position, self.brief = [], 0, {}
        self.open_reviews = open_reviews
        self._opened, self._epoch, self._task = False, 0, None
        self.scope_label = ft.Text('证据对照')
        self.scope = ft.Dropdown(label='记录范围', value='all', on_select=lambda e: self._load())
        self.relation = ft.Dropdown(label='证据关系', value='all',
            options=[ft.dropdown.Option('all', '全部关系')] + [ft.dropdown.Option(k, v) for k, v in RELATIONS.items()],
            on_select=lambda e: self._render_and_update())
        self.rows = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self.position_label = ft.Text('', size=11, color=p.TEXT_2)
        self.boundary = ft.Text('', size=11, color=p.TEXT_3, max_lines=3,
                                overflow=ft.TextOverflow.ELLIPSIS)
        self.notice = ft.Text('', size=12, color=p.TEXT_2, visible=False)
        self.previous = ft.TextButton('上一条', disabled=True, on_click=lambda e: self.move(-1))
        self.following = ft.TextButton('下一条', disabled=True, on_click=lambda e: self.move(1))
        self.next_question = ft.OutlinedButton('作为下一问', disabled=True, on_click=lambda e: self.as_next_question())
        style_inputs(self.scope, self.relation)

    def open(self):
        if self._opened:
            return
        self._opened, self._task = True, self.task_id() or None
        self.scope.options = ([ft.dropdown.Option('task', '当前科研任务')] if self._task else []) + [ft.dropdown.Option('all', '全部记录')]
        self.scope.value = 'task' if self._task else 'all'
        self.relation.value = 'all'
        self.notice.value, self.notice.visible = '', False
        actions = [self.next_question]
        if self.open_reviews:
            actions.append(ft.TextButton('后台复核', on_click=lambda e: self._open_reviews()))
        if self.open_records:
            actions.append(ft.TextButton('研究记录', on_click=lambda e: self._open_records()))
        actions.append(ft.TextButton('关闭', on_click=lambda e: self.close()))
        self.page.show_dialog(ft.AlertDialog(title=self.scope_label,
            content=ft.Container(width=760, height=480, content=ft.Column([
                ft.Row([self.scope, self.relation], wrap=True, spacing=12), self.rows,
                ft.Row([self.previous, self.following, self.position_label], wrap=True, spacing=6),
                self.boundary, self.notice], expand=True)), actions=actions,
            on_dismiss=lambda e: self._dismiss()))
        self._load()

    def _dismiss(self):
        self._opened = False
        self._epoch += 1

    def close(self):
        self._dismiss()
        self.page.pop_dialog()

    def _open_records(self):
        self.close()
        self.open_records()

    def _open_reviews(self):
        if self.records:
            record = self.records[self.position]
            self.close()
            self.open_reviews(record)

    def _load(self):
        self._epoch += 1
        epoch = self._epoch
        task = self._task if self.scope.value == 'task' else None
        self.scope_label.value = '证据对照 · ' + (f'当前任务 {task}' if task else '全部记录')
        self.records, self.position, self.brief = [], 0, {}
        self.rows.controls = [ft.Text('正在读取已保存的科研记录…', color=p.TEXT_2)]
        self.next_question.disabled = self.previous.disabled = self.following.disabled = True
        self.position_label.value = ''
        self.page.update()
        query = {'cmd': 'evidence_brief', **({'task_id': task} if task else {})}
        def work():
            try:
                response = self.api(query)
                if not self._opened or epoch != self._epoch:
                    return
                if not response.get('ok'):
                    raise ValueError(response.get('error') or '证据记录读取失败')
                brief = response.get('brief') or {}
                if (brief.get('scope') or {}).get('task_id') != task:
                    raise ValueError('返回的证据范围不匹配')
                self.brief, self.records = brief, list(brief.get('records') or [])[:20]
                self._render_and_update()
            except Exception as error:  # noqa: BLE001
                if self._opened and epoch == self._epoch:
                    self.rows.controls = [ft.Text(str(redact(str(error))), color=p.DANGER, width=680)]
                    self.page.update()
        self.page.run_thread(work)

    def _render_and_update(self):
        self.rows.controls = []
        total = self.brief.get('total_records', len(self.records))
        self.boundary.value = str(self.brief.get('boundary') or '支持与反驳关系来自录入者判断，尚未执行科学验证。')
        if not self.records:
            self.rows.controls = [ft.Text('此范围没有科研记录；可到“研究记录”录入或关联任务。', color=p.TEXT_2)]
            self.position_label.value = '0 条记录'
            self.next_question.disabled = self.previous.disabled = self.following.disabled = True
            self.page.update()
            return
        self.position = max(0, min(self.position, len(self.records) - 1))
        record = self.records[self.position]
        self.position_label.value = f'第 {self.position + 1}/{len(self.records)} 条 · 范围内共 {total} 条'
        if self.brief.get('records_truncated'):
            self.position_label.value += ' · 仅展示部分记录'
        self.previous.disabled = self.position == 0
        self.following.disabled = self.position == len(self.records) - 1
        self.next_question.disabled = not bool(record.get('followup_draft'))
        self.rows.controls.extend([
            ft.Text(str(record.get('title') or record.get('id') or ''), size=15, weight=ft.FontWeight.W_600, width=680),
            ft.Text(f"记录 {record.get('id')} · 版本 {record.get('version', '未记录')}", size=11, color=p.TEXT_3),
            ft.Text(str(record.get('question') or '未录入研究问题'), selectable=True, width=680),
        ])
        counts = record.get('counts') or {}
        kind = {'hypothesis': '研究假设', 'experiment': '实验记录'}.get(record.get('kind'), '科研记录')
        state = {'planned': '计划中', 'running': '进行中', 'completed': '已录入结果', 'failed': '未成功'}.get(record.get('status'), '状态未记录')
        self.rows.controls.append(ft.Text(f'{kind} · {state} · 录入支持 {counts.get("supports", "未记录")} / '
            f'反驳 {counts.get("challenges", "未记录")} / 背景 {counts.get("context", "未记录")} · '
            f'来源 {counts.get("unique_sources", "未记录")} 个 · 合成 {counts.get("synthetic", "未记录")} 条', size=12, color=p.TEXT_2))
        gaps = record.get('gaps') or []
        if gaps:
            self.rows.controls.append(ft.Text('待核对：' + '；'.join(str(gap) for gap in gaps), size=12, color=p.WARN))
        if record.get('evidence_truncated'):
            self.rows.controls.append(ft.Text('证据仅展示部分条目，完整内容见原始研究记录或导出。', size=11, color=p.TEXT_3))
        groups = record.get('groups') or {}
        for key, label in RELATIONS.items():
            if self.relation.value not in {'all', key}:
                continue
            evidence = list(groups.get(key) or [])[:20]
            self.rows.controls.append(ft.Text(f'{label} · {len(evidence)} 条可见材料', size=13, weight=ft.FontWeight.W_600))
            if not evidence:
                self.rows.controls.append(ft.Text('尚未录入此类材料。', size=12, color=p.TEXT_3))
            for entry in evidence:
                source = entry.get('source') or {}
                identity = ' · '.join(str(value) for value in [source.get('title'), source.get('doi') or source.get('url') or source.get('id')] if value)
                scope = SCOPES.get(entry.get('scope'), entry.get('scope') or '范围未记录')
                flags = [scope, str(entry.get('locator') or '')]
                if entry.get('synthetic') or source.get('synthetic'):
                    flags.append('合成/演示材料')
                if entry.get('quote_truncated'):
                    flags.append('片段已截断')
                if entry.get('quote_modified'):
                    flags.append('片段已做安全处理')
                self.rows.controls.append(ft.Container(padding=10, border=ft.Border.only(left=ft.BorderSide(2, p.BORDER)),
                    content=ft.Column([ft.Text(identity or '来源未记录', size=12, color=p.ACCENT_TEXT, selectable=True, width=640),
                        ft.Text(' · '.join(flag for flag in flags if flag), size=11, color=p.TEXT_3, width=640),
                        ft.Text(str(entry.get('quote') or '未录入原文'), size=12, selectable=True, width=640),
                        ft.Text(str(entry.get('note') or ''), size=11, visible=bool(entry.get('note')), width=640)], spacing=4)))
        style_inputs(self.rows)
        self.page.update()

    def move(self, delta):
        self.position += delta
        self._render_and_update()

    def as_next_question(self):
        if not self.records:
            return
        if (self.request.value or '').strip():
            self.notice.value = '输入区已有草稿，已保留；清空后再选择“作为下一问”。'
        else:
            text = self.records[self.position].get('followup_draft')
            if not text:
                return
            self.request.value = str(text)
            self.notice.value = '已放入输入区，尚未发送或加入后续队列。'
            if self.on_draft_changed:
                self.on_draft_changed()
        self.notice.visible = True
        self.page.update()
