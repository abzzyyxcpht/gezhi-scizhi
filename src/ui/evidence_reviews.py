"""Background review inbox, scoped record selection and explicit note acceptance."""
from __future__ import annotations

import asyncio
import uuid
import flet as ft

from ..bridge import handle
from ..workbench import redact
from .theme import palette as p, style_inputs

LABELS = {'queued': '排队中', 'running': '复核中', 'cancelling': '正在取消', 'ready': '待研究者核查',
          'interrupted': '已中断，需明确恢复', 'error': '复核失败', 'cancelled': '已取消',
          'accepted': '已保存复核备注', 'dismissed': '已忽略'}


class EvidenceReviewInbox:
    def __init__(self, page, *, api=None):
        self.page, self.api = page, api or handle
        self.record, self.jobs, self.view = None, [], None
        self.opened, self.busy, self.reading, self.epoch = False, False, False, 0
        self.pending_start = None
        self.scope = ft.Text('全部复核任务', size=12, color=p.TEXT_2, max_lines=2)
        self.toggle = ft.Switch(label='实验：后台证据复核', value=False, on_change=lambda e: self.set_enabled())
        self.mode = ft.Dropdown(value='local', label='复核方式', width=240, options=[
            ft.dropdown.Option('local', '本地完整性检查'), ft.dropdown.Option('model', '后台模型复核')])
        self.start_button = ft.FilledButton('开始后台复核', disabled=True, on_click=lambda e: self.start())
        self.rows = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self.notice = ft.Text('', size=12, visible=False, color=p.TEXT_2, selectable=True, max_lines=3)
        self.count = ft.Text('', size=11, color=p.TEXT_3)
        style_inputs(self.toggle, self.mode)

    def open(self, record=None):
        if self.opened:
            return
        self.opened, self.record = True, dict(record) if record else None
        self.epoch += 1
        self.busy = self.reading = False
        self.jobs, self.view, self.rows.controls = [], None, []
        self.notice.value, self.notice.visible = '', False
        self.scope.value = (f'{self.record["title"]} · 版本 {self.record["version"]}' if self.record else
                            '全部复核任务；从证据对照中选择记录后可创建复核。')
        self._buttons()
        self.page.show_dialog(ft.AlertDialog(title=ft.Text('后台证据复核收件箱'),
            content=ft.Container(width=780, height=480, content=ft.Column([
                self.scope, self.toggle, ft.Row([self.mode, self.start_button], spacing=10, wrap=True),
                self.notice, self.rows, self.count,
                ft.Text('结果需由研究者检查；保存备注不会改写证据关系或宣称科学验证。', size=11, color=p.TEXT_3, max_lines=2)], expand=True)),
            actions=[ft.TextButton('刷新记录与任务', on_click=lambda e: self.refresh(record=True)),
                     ft.TextButton('关闭', on_click=lambda e: self.close())], on_dismiss=lambda e: self._dismiss()))
        self.refresh()
        if callable(getattr(self.page, 'run_task', None)):
            self.page.run_task(self._poll, self.epoch)

    def _dismiss(self):
        self.opened = False
        self.epoch += 1

    def close(self):
        self._dismiss()
        self.page.pop_dialog()

    async def _poll(self, epoch):
        while self.opened and self.epoch == epoch:
            await asyncio.sleep(2)
            if self.opened and self.epoch == epoch:
                self.refresh()

    def _buttons(self):
        self.start_button.disabled = self.busy or not self.record or not self.toggle.value
        self.toggle.disabled = self.busy
        self.mode.disabled = self.busy or not self.record

    def _message(self, value):
        self.notice.value, self.notice.visible = str(redact(str(value))), True

    def _call(self, req):
        reply = self.api(req)
        if reply.get('ok') is not True:
            raise ValueError(reply.get('error') or '复核请求未得到有效回执')
        return reply

    def refresh(self, *, record=False):
        if not self.opened or self.busy or self.reading:
            return
        self.reading = True
        epoch = self.epoch
        current = self.record
        def work():
            try:
                updated = self._call({'cmd': 'notebook_get', 'id': current['id']})['record'] if record and current else None
                reply = self._call({'cmd': 'review_list', **({'record_id': current['id']} if current else {})})
                if not self.opened or self.epoch != epoch:
                    return
                if updated:
                    self.record = updated
                    self.scope.value = f'{updated["title"]} · 版本 {updated["version"]}'
                if reply != self.view or updated:
                    self.view, self.jobs = reply, reply['jobs']
                    self.toggle.value = bool(reply.get('enabled'))
                    self._render()
                    self.page.update()
            except Exception as error:  # noqa: BLE001
                if self.opened and self.epoch == epoch:
                    self._message(error)
                    self.page.update()
            finally:
                if self.epoch == epoch:
                    self.reading = False
        self.page.run_thread(work)

    def _mutate(self, request, message, accepted=None):
        if self.busy or not self.opened:
            return
        self.busy, self.reading = True, False
        self.epoch += 1
        epoch = self.epoch
        self._buttons()
        self.page.update()
        def work():
            try:
                reply = self._call(request)
                if not self.opened or self.epoch != epoch:
                    return
                if accepted:
                    accepted(reply)
                self._message(message)
            except Exception as error:  # noqa: BLE001
                if self.opened and self.epoch == epoch:
                    self._message(error)
            finally:
                if self.opened and self.epoch == epoch:
                    self.busy = False
                    self._buttons()
                    self.page.update()
                    self.refresh()
                    if callable(getattr(self.page, 'run_task', None)):
                        self.page.run_task(self._poll, self.epoch)
        self.page.run_thread(work)

    def set_enabled(self):
        self._mutate({'cmd': 'settings_save', 'settings': {'experimental_evidence_reviews': bool(self.toggle.value)}},
                     '设置已保存；读取收件箱不会启动任务。')

    def start(self):
        if self.start_button.disabled or not self.record:
            return
        signature = (self.record['id'], self.record['version'], self.mode.value)
        if not self.pending_start or self.pending_start[:3] != signature:
            self.pending_start = (*signature, uuid.uuid4().hex)
        key = self.pending_start[3]
        def accepted(reply):
            job = reply.get('job') or {}
            if not job.get('id') or job.get('request_id') != key or (job.get('record_id'), job.get('record_version'), job.get('mode')) != signature:
                raise ValueError('复核回执与所选记录/版本不匹配，重试将保留本次标识')
            self.pending_start = None
        self._mutate({'cmd': 'review_start', 'record_id': signature[0], 'expected_version': signature[1],
                      'mode': signature[2], 'review_request_id': key}, '复核已创建，可继续进行其他研究。', accepted)

    def action(self, command, job_id):
        messages = {'review_cancel': '已请求停止独立复核。', 'review_resume': '已明确要求恢复复核。',
                    'review_accept': '已保存为研究记录的复核备注，证据关系保持原样。', 'review_dismiss': '已忽略该复核结果。'}
        def accepted(reply):
            job = reply.get('job') or {}
            if job.get('id') != job_id or job.get('status') not in LABELS:
                raise ValueError('复核回执与目标任务不匹配，操作结果尚未确认')
            if command == 'review_accept' and job['status'] != 'accepted':
                raise ValueError('尚未确认复核备注已保存，请刷新后核查')
        self._mutate({'cmd': command, 'job_id': job_id}, messages[command], accepted)

    def _render(self):
        self.rows.controls = []
        for job in self.jobs:
            ident = job['id']
            mode = '本地检查' if job['mode'] == 'local' else '模型复核'
            status = LABELS.get(job['status'], job['status']) + (' · 原记录已变化' if job['stale'] else '')
            actions = [ft.TextButton('查看结果', on_click=lambda e, i=ident: self.show(i))]
            if job['status'] in {'queued', 'running', 'cancelling'}:
                actions.append(ft.TextButton('取消复核', on_click=lambda e, i=ident: self.action('review_cancel', i)))
            elif job['status'] == 'interrupted' and self.toggle.value:
                actions.append(ft.TextButton('明确恢复', on_click=lambda e, i=ident: self.action('review_resume', i)))
            if job.get('can_accept'):
                actions.append(ft.OutlinedButton('保存为复核备注', on_click=lambda e, i=ident: self.action('review_accept', i)))
            if job['status'] not in {'queued', 'running', 'cancelling', 'accepted', 'dismissed'}:
                actions.append(ft.TextButton('忽略', on_click=lambda e, i=ident: self.action('review_dismiss', i)))
            self.rows.controls.append(ft.Column([
                ft.Text(f'{job["title"]} · 版本 {job["record_version"]} · {mode}', size=13, weight=ft.FontWeight.W_600, max_lines=2),
                ft.Text(status, size=12, color=p.WARN if job['stale'] else p.TEXT_2, max_lines=2),
                ft.Text(str(job.get('phase') or '') + (' · ' + str(job['error']) if job.get('error') else ''),
                        size=11, color=p.TEXT_3, max_lines=2, tooltip=str(job.get('error') or '')),
                ft.Row(actions, spacing=4, wrap=True)], spacing=4, key='review-job-' + ident))
        if not self.jobs:
            self.rows.controls.append(ft.Text('当前范围尚无后台复核任务。', color=p.TEXT_2))
        warnings = len((self.view or {}).get('warnings') or [])
        self.count.value = f'显示 {len(self.jobs)} / {(self.view or {}).get("total", len(self.jobs))} 项' + (f' · {warnings} 项存档不可读，原文件保留' if warnings else '')
        self._buttons()
        style_inputs(self.rows)

    def show(self, job_id):
        epoch = self.epoch
        def work():
            try:
                job = self._call({'cmd': 'review_get', 'job_id': job_id})['job']
                if not self.opened or self.epoch != epoch:
                    return
                result = job.get('result') or {}
                text = result.get('text') or job.get('error') or '尚无复核结果。'
                self.page.show_dialog(ft.AlertDialog(title=ft.Text(f'复核结果 · 版本 {job["record_version"]}'),
                    content=ft.Container(width=720, height=420, content=ft.Column([
                        ft.Text(LABELS.get(job['status'], job['status']) + (' · 原记录已变化' if job['stale'] else ''), color=p.WARN),
                        ft.Text(str(job.get('error') or ''), color=p.DANGER, visible=bool(job.get('error')),
                                selectable=True, max_lines=4),
                        ft.Markdown(str(text), selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB),
                        ft.Text('这些是建议，未验证科学支持；未经显式保存不会修改研究记录。', size=11, color=p.TEXT_3, max_lines=2)], scroll=ft.ScrollMode.AUTO)),
                    actions=[ft.TextButton('返回收件箱', on_click=lambda e: self.page.pop_dialog())]))
            except Exception as error:  # noqa: BLE001
                if self.opened and self.epoch == epoch:
                    self._message(error)
                    self.page.update()
        self.page.run_thread(work)
