"""Real desktop callbacks find bounded sidechain approvals without running tools."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from tests import test_desktop_navigation as navigation
from tests.test_desktop_theme import walk
from src.ui import desktop


class SidechainApprovalUITest(unittest.TestCase):
    def setUp(self):
        self.fixture = navigation.DesktopNavigationTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.ui, self.page = self.fixture.session_store, self.fixture.ui, self.fixture.page
        for row in self.store.list():
            self.store.append(row['session_id'], 'completed', {'status': 'done'})
        self.ui.agent_state.update(approval_dialog_id=None, approval_open_id=None,
                                   approval_dismissed=None, approval_session=None)
        self.ui.approval_scan_at[0] = 0

    def child(self, child='child', parent='parent', call='shared-call', meta_status='waiting_approval'):
        self.store.append(parent, 'user_message', {'text': 'Parent', 'mode': 'agent'})
        self.store.append(parent, 'driver_paused', {'status': 'waiting_children'})
        self.store.register_sidechain(child, parent)
        self.store.append(child, 'user_message', {'text': 'Child', 'mode': 'agent', 'parent': parent})
        self.store.append(child, 'permission_requested', {
            'tool_call_id': call, 'tool': 'fixture.write', 'capability': 'write',
            'arguments': {'path': 'fixture.txt'}})
        path = self.store.root / parent / 'children' / f'{child}.meta.json'
        path.write_text(json.dumps({'child': child, 'parent': parent, 'status': meta_status}), encoding='utf-8')

    def scan(self):
        self.ui.approval_scan_at[0] = 0
        self.ui.check_global_approvals()

    def test_child_pending_opens_precise_modal_without_approving_or_resuming(self):
        self.child()
        self.assertNotIn('child', {row['session_id'] for row in self.store.list()})
        self.scan()
        self.assertEqual(self.ui.agent_state.get('approval_session'), 'child')
        self.assertEqual(self.ui.agent_state.get('approval_dialog_id'), 'shared-call')
        self.fixture.runtime.approve.assert_not_called()

    def test_stale_terminal_or_corrupt_metadata_does_not_hide_journal_approval(self):
        for metadata in ['{"status":"done"}', '{broken metadata']:
            with self.subTest(metadata=metadata):
                self.child()
                path = self.store.root / 'parent' / 'children' / 'child.meta.json'
                path.write_text(metadata, encoding='utf-8')
                self.ui.agent_state.update(approval_dialog_id=None, approval_open_id=None,
                                           approval_dismissed=None, approval_session=None)
                self.scan()
                self.assertEqual(self.ui.agent_state.get('approval_session'), 'child')

    def test_explicit_approve_button_targets_child_session_and_call(self):
        self.child()
        self.scan()
        button = next(control for control in walk(self.ui.agent_approval_card)
                      if getattr(control, 'content', None) == '批准执行')
        with mock.patch.object(desktop.service, 'resume') as resume:
            button.on_click(None)
        self.fixture.runtime.approve.assert_called_once_with('child', 'shared-call', 'allow')
        resume.assert_not_called()

    def test_dismissed_pair_stays_quiet_but_same_call_in_another_session_opens(self):
        self.child('child-a')
        self.scan()
        self.ui._approval_dismissed(None)
        self.child('child-b')
        self.scan()
        self.assertEqual(self.ui.agent_state.get('approval_session'), 'child-b')
        self.ui._approval_dismissed(None)
        self.page.show_dialog.reset_mock()
        for _ in range(3):
            self.scan()
        self.page.show_dialog.assert_not_called()

    def test_open_dialog_and_time_gate_skip_reads(self):
        self.child()
        with mock.patch.object(self.store, 'list', wraps=self.store.list) as listed, \
                mock.patch.object(self.store, 'snapshot', wraps=self.store.snapshot) as snap:
            self.ui.agent_state['approval_dialog_id'] = 'open'
            self.scan()
            listed.assert_not_called()
            snap.assert_not_called()
            self.ui.agent_state['approval_dialog_id'] = None
            self.ui.approval_scan_at[0] = desktop.time.monotonic()
            self.ui.check_global_approvals()
            listed.assert_not_called()
            snap.assert_not_called()

    def test_child_discovery_survives_runtime_restart(self):
        self.child()
        self.store._sidechain_parents.clear()
        self.scan()
        self.assertEqual(self.ui.agent_state.get('approval_session'), 'child')

    def test_scan_bound_is_reported_and_remaining_metadata_is_checked_later(self):
        for index in range(101):
            child = f'child-{index:03d}'
            self.child(child, call=child)
            if index < 100:
                self.store.append(child, 'completed', {'status': 'done'})
        with mock.patch.object(self.store, 'snapshot', wraps=self.store.snapshot) as snap:
            self.scan()
            self.assertLessEqual(snap.call_count, 100)
            self.assertIn('100', self.ui.agent_state.get('approval_scan_warning', ''))
        self.scan()
        self.assertEqual(self.ui.agent_state.get('approval_session'), 'child-100')

    def test_completed_top_level_sessions_are_not_reprojected_for_approvals(self):
        self.child()
        with mock.patch.object(self.store, 'snapshot', wraps=self.store.snapshot) as snap:
            self.scan()
        self.assertEqual([call.args[0] for call in snap.call_args_list], ['child'])


if __name__ == '__main__':
    unittest.main()
