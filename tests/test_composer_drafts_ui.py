"""Draft lifecycle tests with real local storage and isolated Flet controls."""
import asyncio
import tempfile
from pathlib import Path
from unittest import TestCase, mock

import flet as ft

from src.composer_drafts import DraftStore
from src.ui.composer_drafts import ComposerDrafts


class ComposerDraftsUITest(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DraftStore(Path(self.temp.name))
        self.page = mock.MagicMock()
        self.request = ft.TextField()
        self.agent = ft.Dropdown(value='research')
        self.mode = ft.Dropdown(value='agent')
        self.ui = ComposerDrafts(self.page, self.request, self.agent, self.mode, store=self.store)
        self.ui.select(None, 'project-a')
        self.addCleanup(self.ui.close)

    def type(self, text):
        self.request.value = text
        self.ui.capture()

    def test_switch_and_restart_restore_scoped_text_and_options(self):
        self.type('项目甲\n  未发送')
        self.mode.value = 'review'
        self.ui.capture()
        self.ui.select('session-b', 'project-b')
        self.assertEqual('', self.request.value)
        self.type('会话乙')
        self.ui.select(None, 'project-a')
        self.assertEqual('项目甲\n  未发送', self.request.value)
        self.assertEqual('review', self.mode.value)
        self.ui.close()
        other = ComposerDrafts(self.page, ft.TextField(), ft.Dropdown(value='research'), ft.Dropdown(value='agent'), store=self.store)
        self.addCleanup(other.close)
        other.select('session-b', 'project-b')
        self.assertEqual('会话乙', other.request.value)

    def test_late_success_clears_only_original_draft_after_switch(self):
        self.type('send A')
        ticket = self.ui.submitted()
        self.ui.select('session-b')
        self.type('keep B')
        self.ui.finish(ticket, accepted=True)
        self.assertEqual('keep B', self.request.value)
        self.ui.select(None, 'project-a')
        self.assertEqual('', self.request.value)

    def test_same_text_edited_again_survives_old_completion(self):
        self.type('A')
        ticket = self.ui.submitted()
        self.type('B')
        self.type('A')
        self.ui.finish(ticket, accepted=True)
        self.assertEqual('A', self.request.value)
        self.ui.flush()
        self.assertEqual('A', self.store.load('project:project-a')['text'])

    def test_failed_submission_keeps_draft(self):
        self.type('retry me')
        ticket = self.ui.submitted()
        self.ui.finish(ticket, accepted=False)
        self.assertEqual('retry me', self.request.value)

    def test_new_session_adoption_keeps_new_edit_and_cleans_origin(self):
        self.type('sent')
        ticket = self.ui.submitted()
        self.type('next draft')
        self.ui.adopt_session('created', ticket)
        self.assertEqual('next draft', self.request.value)
        self.ui.finish(ticket, accepted=True)
        self.assertEqual('next draft', self.request.value)
        self.ui.select(None, 'project-a')
        self.assertEqual('', self.request.value)
        self.ui.select('created')
        self.assertEqual('next draft', self.request.value)

    def test_first_turn_adoption_clears_unchanged_sent_text(self):
        self.type('sent')
        ticket = self.ui.submitted()
        self.ui.adopt_session('created', ticket)
        self.ui.finish(ticket, accepted=True)
        self.assertEqual('', self.request.value)
        self.assertEqual('', self.store.load('project:project-a')['text'])
        self.assertEqual('', self.store.load('session:created')['text'])

    def test_conflict_keeps_both_versions_and_survives_navigation(self):
        self.type('local')
        self.store.save('project:project-a', 'other window', expected_revision='')
        self.assertFalse(self.ui.flush())
        self.assertEqual('local', self.request.value)
        self.assertEqual('other window', self.store.load('project:project-a')['text'])
        self.assertTrue(self.ui.resolve_button.visible)
        self.ui.select('another')
        self.ui.select(None, 'project-a')
        self.assertEqual('local', self.request.value)

    def test_disk_failure_does_not_block_switch_or_destroy_input(self):
        self.type('local')
        with mock.patch.object(self.store, 'save', side_effect=OSError('disk full')):
            self.assertFalse(self.ui.flush())
            self.ui.select('another')
        self.ui.select(None, 'project-a')
        self.assertEqual('local', self.request.value)

    def test_delayed_save_never_overwrites_another_scope(self):
        self.type('A')
        callback, scope, generation = self.page.run_task.call_args.args
        self.ui.select('session-b')
        self.type('B')
        asyncio.run(callback(scope, generation, delay=0))
        self.assertEqual('B', self.request.value)
        self.assertEqual('A', self.store.load('project:project-a')['text'])

    def test_clear_is_explicit_and_persistent(self):
        self.type('remove')
        self.ui.clear()
        self.assertEqual('', self.request.value)
        self.assertEqual('', self.store.load('project:project-a')['text'])

    def test_overlong_input_stays_visible_and_unsaved(self):
        self.type('x' * 32001)
        self.assertFalse(self.ui.flush())
        self.assertEqual(32001, len(self.request.value))
        self.assertEqual('', self.store.load('project:project-a')['text'])

    def test_no_save_on_unmodified_selection(self):
        with mock.patch.object(self.store, 'save', wraps=self.store.save) as save:
            self.ui.select('a')
            self.ui.select('b')
            self.ui.flush()
        save.assert_not_called()

    def test_native_close_failure_keeps_editor_open_for_resolution(self):
        self.type('unsaved before close')
        with mock.patch.object(self.store, 'save', side_effect=OSError('disk full')):
            self.assertFalse(self.ui.close(force=False))
        self.assertFalse(self.ui._closed)
        self.assertEqual('unsaved before close', self.request.value)
        self.assertTrue(self.ui.close(force=False))
        self.assertTrue(self.ui._closed)
        self.assertEqual('unsaved before close', self.store.load('project:project-a')['text'])

    def test_reopen_clean_current_scope_sees_peer_version(self):
        self.type('original')
        self.ui.flush()
        saved = self.store.load('project:project-a')
        self.store.save('project:project-a', 'peer update', expected_revision=saved['revision'])
        self.ui.select(None, 'project-a')
        self.assertEqual('peer update', self.request.value)
