"""项目工作室关键分支边界：附件预算与数量、损坏存储、乐观锁、引用删除、调度暴露、远端地址。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from src import config, settings


class AttachmentBudgetTests(unittest.TestCase):
    """附件上下文字符预算与附件数量的拒绝路径。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patch = mock.patch.object(config, 'DATA_DIR', self.root / 'data')
        patch.start()
        self.addCleanup(patch.stop)
        from src import project_workspace, session_workspace
        self.project_api = project_workspace
        self.session_api = session_workspace
        self.project_dir = self.root / 'research'
        self.project_dir.mkdir(parents=True)
        self.project = self.project_api.handle({'cmd': 'project_open', 'path': str(self.project_dir)})['project']

    def test_attachment_context_rejects_budget_overflow(self):
        for name in ('a.md', 'b.md'):
            (self.project_dir / name).write_text('x' * 12000, encoding='utf-8')
        with self.assertRaises(ValueError) as caught:
            self.project_api.attachment_context(self.project['id'], ['a.md', 'b.md'])
        self.assertIn('预算', str(caught.exception))
        with self.assertRaises(ValueError):
            self.project_api.attachment_context(self.project['id'], ['a.md', 'b.md'], max_chars=100)
        with self.assertRaises(ValueError):
            self.project_api.attachment_context(self.project['id'], ['a.md'], max_chars=0)

    def test_attachment_context_rejects_more_than_twenty_paths(self):
        (self.project_dir / 'one.md').write_text('内容', encoding='utf-8')
        paths = ['one.md'] * 21
        with self.assertRaises(ValueError) as caught:
            self.project_api.attachment_context(self.project['id'], paths)
        self.assertIn('20', str(caught.exception))
        with self.assertRaises(ValueError):
            self.project_api.attachment_context(self.project['id'], 'one.md')

    def test_chat_attach_rejects_thirteenth_attachment(self):
        for index in range(13):
            (self.project_dir / f'note{index}.md').write_text(f'材料 {index}', encoding='utf-8')
        sid = self.session_api.handle({'cmd': 'chat_new', 'project_id': self.project['id']})['session_id']
        for index in range(12):
            result = self.session_api.handle({'cmd': 'chat_attach', 'session_id': sid, 'path': f'note{index}.md'})
            self.assertTrue(result['ok'], result)
        rejected = self.session_api.handle({'cmd': 'chat_attach', 'session_id': sid, 'path': 'note12.md'})
        self.assertFalse(rejected['ok'])
        self.assertIn('12', rejected['error'])
        session = self.session_api.handle({'cmd': 'chat_get', 'session_id': sid})['session']
        self.assertEqual(12, len(session['attachments']))


class NotebookEdgeTests(unittest.TestCase):
    """损坏存储的读取路径、乐观锁冲突码与被引用记录的删除阻断。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(config, 'DATA_DIR', Path(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)
        from src import research_notebook
        self.notebook = research_notebook

    def call(self, cmd, **kwargs):
        return self.notebook.handle({'cmd': 'notebook_' + cmd, **kwargs})

    def save(self, **fields):
        response = self.call('save', record={'title': '边界测试', 'question': '假设是否成立？', **fields})
        self.assertTrue(response['ok'], response)
        return response['record']

    def test_corrupt_store_is_reported_on_read_paths_and_file_is_kept(self):
        record = self.save()
        path = Path(self.tmp.name) / 'research_notebook.json'
        for broken in ('{"records":', 'not json at all'):
            with self.subTest(broken=broken):
                path.write_text(broken, encoding='utf-8')
                for cmd, kwargs in (('list', {}), ('get', {'id': record['id']})):
                    response = self.call(cmd, **kwargs)
                    self.assertFalse(response['ok'], response)
                    self.assertEqual('corrupt_store', response['code'])
                    self.assertIn('损坏', response['error'])
                self.assertEqual(path.read_text(encoding='utf-8'), broken)

    def test_version_conflict_is_reported_for_stale_and_non_integer_versions(self):
        record = self.save()
        stale = self.call('save', record={'id': record['id'], 'prediction': 'A > B'},
                          expected_version=record['version'] + 5)
        self.assertFalse(stale['ok'])
        self.assertEqual('version_conflict', stale['code'])
        self.assertIn('已变更', stale['error'])
        invalid = self.call('save', record={'id': record['id'], 'prediction': 'A > B'},
                            expected_version='2')
        self.assertFalse(invalid['ok'])
        self.assertEqual('version_conflict', invalid['code'])
        self.assertEqual(record['prediction'], self.call('get', id=record['id'])['record']['prediction'])

    def test_referenced_record_delete_is_blocked_with_referenced_by(self):
        hypothesis = self.save()
        experiment = self.save(kind='experiment', related_ids=[hypothesis['id']])
        blocked = self.call('delete', id=hypothesis['id'])
        self.assertFalse(blocked['ok'])
        self.assertEqual('record_referenced', blocked['code'])
        self.assertEqual([experiment['id']], blocked['referenced_by'])
        self.assertTrue(self.call('get', id=hypothesis['id'])['ok'])
        self.assertTrue(self.call('delete', id=experiment['id'])['ok'])
        self.assertTrue(self.call('delete', id=hypothesis['id'])['ok'])


class PlanningEdgeTests(unittest.TestCase):
    """调度器错误暴露、requires_resume 视图与错过触发不补跑。"""

    def setUp(self):
        import importlib
        self.module = importlib.import_module('src.research_planning')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        from datetime import datetime, timezone
        self.now = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
        self.task_rows, self.launches = [], []
        self.planner = self.make_planner()

    def dispatch(self, req):
        self.launches.append(req)
        tid = f'task-{len(self.launches)}'
        self.task_rows.append({'task_id': tid, 'status': 'running', 'topic': req['topic']})
        return {'ok': True, 'task_id': tid}

    def make_planner(self, **kwargs):
        return self.module.ResearchPlanning(
            self.root, dispatch=kwargs.pop('dispatch', self.dispatch),
            tasks=lambda: list(self.task_rows), notebook=lambda: [],
            audit=lambda tid: {'task_id': tid, 'source': 'recorded'},
            clock=lambda: self.now, sessions=lambda req: [], **kwargs)

    def schedule(self, **kwargs):
        response = self.planner.handle({'cmd': 'schedule_save', 'schedule': {
            'title': '定时跟进', 'workflow_id': 'literature_scan', 'topic': 'research agents',
            'run_at': self.now.isoformat(), 'offline': True, 'enabled': True, **kwargs}})
        self.assertTrue(response['ok'], response)
        return response['schedule']

    def test_schedule_list_exposes_scheduler_error_and_requires_resume_view(self):
        self.schedule(interval_seconds=60, max_runs=2)
        self.planner.tick()
        restarted = self.make_planner(host_id='another-host')
        restarted.last_scheduler_error = '磁盘写入失败'
        listing = restarted.handle({'cmd': 'schedule_list'})
        self.assertTrue(listing['ok'], listing)
        self.assertEqual('磁盘写入失败', listing['scheduler_error'])
        self.assertFalse(listing['scheduler_running'])
        row = listing['schedules'][0]
        self.assertTrue(row['requires_resume'])
        self.assertEqual('interrupted', row['status'])
        self.assertIn('显式恢复', row['last_error'])
        self.assertNotIn('owner_host', row)

    def test_missed_interval_trigger_is_never_caught_up(self):
        self.schedule(interval_seconds=60, max_runs=5)
        self.planner.tick()
        self.assertEqual(1, len(self.launches))
        self.task_rows[0]['status'] = 'done'
        self.planner.tick()   # 任务完成，排定下一次触发（当前时刻 + 60 秒）
        self.now += timedelta(hours=2)   # 停机两小时，错过触发窗口
        self.planner.tick()
        self.assertEqual(1, len(self.launches), '错过触发时间不得立即补跑')
        row = self.planner.handle({'cmd': 'schedule_list'})['schedules'][0]
        self.assertEqual('missed', row['status'])
        self.assertFalse(row['enabled'])
        explicit = self.planner.handle({'cmd': 'schedule_run_now', 'id': row['id']})
        self.assertTrue(explicit['ok'], explicit)
        self.assertEqual(2, len(self.launches), '错过窗口后仅允许显式立即运行')


class SubagentsParallelTests(unittest.TestCase):
    """subagents parallel 的数字与非数字分支。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        patches = [(settings, '_PATH', root / 'settings.json'), (config, 'DATA_DIR', root)]
        for obj, key, value in patches:
            patch = mock.patch.object(obj, key, value)
            patch.start()
            self.addCleanup(patch.stop)
        from src.command_center import execute
        self.execute = execute

    def test_parallel_accepts_integer_and_persists(self):
        result = self.execute('/subagents parallel 3')
        self.assertTrue(result['ok'], result)
        self.assertEqual(3, settings.load()['subagent_max_parallel'])

    def test_parallel_rejects_non_integer_with_friendly_message(self):
        for body in ('abc', '2.5', ''):
            with self.subTest(body=body):
                result = self.execute('/subagents parallel ' + body)
                self.assertFalse(result['ok'])
                self.assertIn('并行数须为正整数', result['error'])
        self.assertEqual(4, settings.load()['subagent_max_parallel'], '拒绝时不应改动设置')


class RemoteUrlValidationTests(unittest.TestCase):
    """project_workspace._validate_remote_url 的地址形态分支。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from src.project_workspace import _validate_remote_url
        self.validate = _validate_remote_url

    def test_scp_style_and_standard_urls_are_accepted(self):
        local = Path(self.tmp.name) / 'bare.git'
        local.mkdir()
        for url in ('git@github.com:user/repo.git', 'git@host.example:group/proj.git',
                    'host:path/to/repo', 'https://example.com/user/repo.git',
                    'https://user@example.com/repo.git', 'ssh://git@host.example/repo',
                    'file:///tmp/repo.git', 'file://localhost/tmp/repo.git', str(local)):
            with self.subTest(url=url):
                self.validate(url)

    def test_unsafe_or_malformed_urls_are_rejected(self):
        local_file = Path(self.tmp.name) / 'plain.txt'
        local_file.write_text('x', encoding='utf-8')
        for url in ('ftp://host/repo.git', 'git://host/repo.git',
                    'file://remote-server/share/repo.git',
                    '-oProxyCommand=evil', 'just-text-no-colon',
                    'git@host:pa th', 'git@host:re"po', 'git@host:pa::th',
                    'https://example.com/repo\x07.git', str(local_file)):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    self.validate(url)


if __name__ == '__main__':
    unittest.main()
