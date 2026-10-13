"""Real desktop callbacks: draft identity across navigation and late results."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src import config, settings, workspace_features, session_queue
from tests import test_desktop_navigation as navigation


class DesktopDraftAuditTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='gezhi-desktop-draft-audit-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for module, name, value in ((config, 'DATA_DIR', root),
                (settings, '_PATH', root / 'settings.json'),
                (workspace_features, 'DATA_DIR', root)):
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        fixture = navigation.DesktopNavigationTest('runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture, self.ui, self.page = fixture, fixture.ui, fixture.page
        self.addCleanup(session_queue.close)

    def edit(self, text):
        self.ui.agent_request.value = text
        self.ui.agent_request.on_change(None)

    def test_switching_away_and_back_restores_each_session_draft(self):
        self.ui.select_agent_session('session-a')
        self.edit('甲的未发送推导')
        self.ui.select_agent_session('session-b')
        self.edit('乙的未发送追问')
        self.ui.select_agent_session('session-a')
        self.assertEqual('甲的未发送推导', self.ui.agent_request.value)
        self.ui.select_agent_session('session-b')
        self.assertEqual('乙的未发送追问', self.ui.agent_request.value)

    def test_reopening_current_session_preserves_unsent_request(self):
        self.ui.select_agent_session('session-b')
        self.edit('当前会话还没发出的修改')
        self.ui.select_agent_session('session-b')
        self.assertEqual('当前会话还没发出的修改', self.ui.agent_request.value)

    def test_project_switch_without_session_isolates_project_drafts(self):
        self.ui.project_changed({'id': 'project-a', 'name': '项目甲'})
        self.edit('甲项目的初始请求')
        self.ui.project_changed({'id': 'project-b', 'name': '项目乙'})
        self.assertEqual('', self.ui.agent_request.value or '')
        self.edit('乙项目的初始请求')
        self.ui.project_changed({'id': 'project-a', 'name': '项目甲'})
        self.assertEqual('甲项目的初始请求', self.ui.agent_request.value)

    def test_first_submission_cannot_take_over_later_project_view(self):
        self.ui.project_changed({'id': 'project-a', 'name': '项目甲'})
        self.edit('发送甲项目任务')
        queued = []
        self.page.run_thread.side_effect = queued.append
        self.ui.agent_go('run')
        work = queued.pop(0)
        self.ui.project_changed({'id': 'project-b', 'name': '项目乙'})
        self.edit('乙项目的新草稿')
        requests = []
        def backend(request):
            requests.append(request)
            if request['cmd'] == 'chat_new':
                return {'ok': True, 'session_id': 'new-project-a'}
            if request['cmd'] == 'chat_turn':
                return {'ok': True, 'result': {'session_id': 'new-project-a', 'status': 'done'}}
            if request['cmd'] == 'chat_get':
                return {'ok': True, 'session': {'title': '甲任务', 'archived': False, 'attachments': []}}
            raise AssertionError(request)
        with mock.patch('src.session_workspace.handle', side_effect=backend):
            work()
        self.assertEqual('project-a', next(r for r in requests if r['cmd'] == 'chat_new')['project_id'])
        self.assertEqual('project-b', self.ui.state['project_id'])
        self.assertNotEqual('new-project-a', self.ui.agent_state['session_id'])
        self.assertEqual('乙项目的新草稿', self.ui.agent_request.value)

    def test_success_cannot_clear_same_text_edited_again_after_submit(self):
        self.ui.select_agent_session('session-b')
        self.edit('同一个问题')
        queued = []
        self.page.run_thread.side_effect = queued.append
        self.ui.agent_go('run')
        work = queued.pop(0)
        self.edit('')
        self.edit('同一个问题')
        def backend(request):
            if request['cmd'] == 'chat_turn':
                return {'ok': True, 'result': {'session_id': 'session-b', 'status': 'done'}}
            if request['cmd'] == 'chat_get':
                return {'ok': True, 'session': {'title': '会话乙', 'archived': False, 'attachments': []}}
            raise AssertionError(request)
        with mock.patch('src.session_workspace.handle', side_effect=backend):
            work()
        self.assertEqual('同一个问题', self.ui.agent_request.value)

    def test_navigation_shortcut_keeps_current_chat_draft(self):
        self.ui.select_agent_session('session-b')
        self.edit('切换页面仍保留输入')
        self.ui.keyboard_shortcut(SimpleNamespace(key='N', ctrl=True, alt=False, shift=False))
        self.ui.nav_to(4)
        self.assertEqual('切换页面仍保留输入', self.ui.agent_request.value)

    def test_failed_new_project_session_does_not_leave_old_chat_selected(self):
        self.ui.select_agent_session('session-a')
        self.edit('甲项目旧会话草稿')
        def backend(request):
            if request['cmd'] == 'chat_get':
                return {'ok': True, 'session': {'project_id': 'project-a'}}
            if request['cmd'] == 'chat_sessions':
                return {'ok': True, 'sessions': []}
            if request['cmd'] == 'chat_new':
                return {'ok': False, 'error': 'fixture cannot create'}
            return {'ok': True}
        with mock.patch('src.session_workspace.handle', side_effect=backend):
            self.ui.project_changed({'id': 'project-b', 'name': '项目乙'})
        self.assertIsNone(self.ui.agent_state['session_id'])
        self.assertEqual('', self.ui.agent_request.value)
        self.ui.select_agent_session('session-a')
        self.assertEqual('甲项目旧会话草稿', self.ui.agent_request.value)


if __name__ == '__main__':
    unittest.main()
