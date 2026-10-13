"""Final draft review: real callbacks, committed errors, and failed navigation."""
from __future__ import annotations

import unittest
from unittest import mock

from src import config, session_workspace
from src.agent_runtime import AgentRuntime
from tests import test_desktop_draft_audit as audit


class DesktopDraftReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = audit.DesktopDraftAuditTests('runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture, self.ui, self.page = fixture, fixture.ui, fixture.page
        self.addCleanup(self.ui.drafts.close)

    def edit(self, text):
        self.fixture.edit(text)

    def test_runtime_error_and_blocked_receipts_have_already_committed_user_message(self):
        for expected in ('error', 'blocked'):
            with self.subTest(status=expected):
                runtime = AgentRuntime(session_root=config.DATA_DIR / 'sessions', offline=True,
                    permissions={'workspace.read': 'deny'})
                sid = session_workspace.handle({'cmd': 'chat_new'})['session_id']
                tool = 'missing.fixture.tool' if expected == 'error' else 'workspace.read'
                with mock.patch('src.bridge._runtime', return_value=runtime), \
                        mock.patch.object(runtime, 'plan', return_value={'steps': [{'tool': tool, 'arguments': {}}]}):
                    self.ui.select_agent_session(sid)
                    self.edit('用户提交：' + expected)
                    self.ui.agent_go('run')
                self.assertEqual(expected, runtime.sessions.snapshot(sid)['status'])
                self.assertEqual('', self.ui.agent_request.value)
                self.assertEqual('用户提交：' + expected, session_workspace.transcript(sid)[0]['content'])

    def test_direct_selection_read_failure_preserves_previous_identity_and_draft(self):
        self.ui.select_agent_session('session-a')
        self.edit('甲的未发送草稿')
        epoch = self.ui.agent_state['view_epoch']
        with mock.patch.object(self.fixture.fixture.session_store, 'snapshot', side_effect=OSError('读取失败')):
            with self.assertRaises(OSError):
                self.ui.select_agent_session('session-b')
        self.assertEqual('session-a', self.ui.agent_state['session_id'])
        self.assertEqual(epoch, self.ui.agent_state['view_epoch'])
        self.assertEqual('session:session-a', self.ui.drafts.scope)
        self.assertEqual('甲的未发送草稿', self.ui.agent_request.value)

    def test_project_selection_read_failure_cannot_leave_old_chat_as_new_project_destination(self):
        self.ui.project_changed({'id': 'project-a', 'name': '项目甲'})
        self.ui.select_agent_session('session-b')  # A completed session accepts another turn.
        self.edit('甲的未发送草稿')
        original_snapshot = self.fixture.fixture.session_store.snapshot
        def snapshot(sid):
            if sid == 'session-c':
                raise OSError('乙会话读取暂时失败')
            return original_snapshot(sid)
        def backend(request):
            if request['cmd'] == 'chat_get':
                return {'ok': True, 'session': {'project_id': 'project-a'}}
            if request['cmd'] == 'chat_sessions':
                return {'ok': True, 'sessions': [{'session_id': 'session-c', 'project_id': 'project-b'}]}
            raise AssertionError(request)
        with mock.patch('src.session_workspace.handle', side_effect=backend), \
                mock.patch.object(self.fixture.fixture.session_store, 'snapshot', side_effect=snapshot):
            try:
                self.ui.project_changed({'id': 'project-b', 'name': '项目乙'})
            except OSError:
                pass  # ProjectStudio catches and displays this exception after adopting B.
        self.assertFalse(self.ui.state['project_id'] == 'project-b'
                         and self.ui.agent_state['session_id'] == 'session-b',
                         'Project B must not retain project A as its implicit submission destination')

    def test_project_round_trip_while_first_turn_is_pending_keeps_reedited_text(self):
        self.ui.project_changed({'id': 'project-a', 'name': '项目甲'})
        self.edit('甲的首次请求')
        work = []
        self.page.run_thread.side_effect = work.append
        self.ui.agent_go('run')
        send = work.pop(0)
        self.ui.project_changed({'id': 'project-b', 'name': '项目乙'})
        self.edit('乙的草稿')
        self.ui.project_changed({'id': 'project-a', 'name': '项目甲'})
        self.edit('')
        self.edit('甲的首次请求')
        def backend(request):
            if request['cmd'] == 'chat_new':
                return {'ok': True, 'session_id': 'new-a'}
            if request['cmd'] == 'chat_turn':
                return {'ok': True, 'result': {'session_id': 'new-a', 'status': 'done'}}
            raise AssertionError(request)
        with mock.patch('src.session_workspace.handle', side_effect=backend):
            send()
        self.assertIsNone(self.ui.agent_state['session_id'])
        self.assertEqual('甲的首次请求', self.ui.agent_request.value)
        self.assertEqual('project:project-a', self.ui.drafts.scope)
        self.ui.project_changed({'id': 'project-b', 'name': '项目乙'})
        self.assertEqual('乙的草稿', self.ui.agent_request.value)

    def test_project_round_trip_while_first_turn_is_pending_clears_only_unedited_submission(self):
        self.ui.project_changed({'id': 'project-a', 'name': '项目甲'})
        self.edit('甲的首次请求')
        work = []
        self.page.run_thread.side_effect = work.append
        self.ui.agent_go('run')
        send = work.pop(0)
        self.ui.project_changed({'id': 'project-b', 'name': '项目乙'})
        self.edit('乙的草稿')
        self.ui.project_changed({'id': 'project-a', 'name': '项目甲'})
        def backend(request):
            if request['cmd'] == 'chat_new':
                return {'ok': True, 'session_id': 'new-a'}
            if request['cmd'] == 'chat_turn':
                return {'ok': True, 'result': {'session_id': 'new-a', 'status': 'done'}}
            raise AssertionError(request)
        with mock.patch('src.session_workspace.handle', side_effect=backend):
            send()
        self.assertEqual('', self.ui.agent_request.value)
        self.assertIsNone(self.ui.agent_state['session_id'])
        self.ui.project_changed({'id': 'project-b', 'name': '项目乙'})
        self.assertEqual('乙的草稿', self.ui.agent_request.value)

    def test_late_queue_receipt_preserves_current_session_and_scoped_drafts(self):
        from tests.test_session_queue_ui import acceptance, queue
        self.ui.select_agent_session('session-b')
        self.edit('乙会话后续请求')
        work = []
        self.page.run_thread.side_effect = work.append
        panel = self.ui.session_queue
        panel.api = mock.Mock(side_effect=lambda request: {'ok': True, 'queue': queue('session-b')}
            if request['cmd'] == 'queue_list' else acceptance(request))
        panel.refresh(force=True)
        work.pop(0)()
        panel.add()
        submit = work.pop(0)
        self.ui.select_agent_session('session-a')
        self.edit('甲会话不能被清空')
        submit()
        self.assertEqual('甲会话不能被清空', self.ui.agent_request.value)
        self.assertEqual('session:session-a', self.ui.drafts.scope)
        self.assertIsNotNone(panel._pending_submission)
        self.ui.select_agent_session('session-b')
        self.assertEqual('乙会话后续请求', self.ui.agent_request.value)
        # Original idempotency key survives the ignored UI receipt: retrying
        # confirms the existing ticket rather than adding a duplicate turn.
        self.assertEqual('session-b', panel._pending_submission[0])

    def test_late_steer_receipt_preserves_current_session_and_original_turn_identity(self):
        self.ui.select_agent_session('session-b')
        self.edit('乙会话本轮补充')
        work = []
        self.page.run_thread.side_effect = work.append
        panel = self.ui.session_steering
        status = {'session_id': 'session-b', 'turn_id': 'turn-b', 'state': 'open', 'enabled': True,
                  'status': 'running', 'items': []}
        def backend(request):
            response = {'ok': True, 'steering': dict(status)}
            if request['cmd'] == 'steer_add':
                response['ticket'] = {'id': 'ticket', 'request_id': request['steer_request_id'], 'state': 'pending'}
            return response
        panel.api = mock.Mock(side_effect=backend)
        panel.refresh(force=True)
        work.pop(0)()
        panel.add()
        submit = work.pop(0)
        self.ui.select_agent_session('session-a')
        self.edit('甲会话不能被清空')
        submit()
        self.assertEqual('甲会话不能被清空', self.ui.agent_request.value)
        self.assertEqual('session:session-a', self.ui.drafts.scope)
        self.assertEqual(('session-b', '乙会话本轮补充', 'turn-b'), panel.pending[:3])
        self.ui.select_agent_session('session-b')
        self.assertEqual('乙会话本轮补充', self.ui.agent_request.value)


if __name__ == '__main__':
    unittest.main()
