"""Independent lifecycle review using real draft files and isolated UI controls."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import flet as ft

from src.composer_drafts import DraftConflict, DraftStore
from src.ui.composer_drafts import ComposerDrafts


class DraftLifecycleReviewTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="draft-lifecycle-review-")
        self.addCleanup(self.temporary.cleanup)
        self.store = DraftStore(Path(self.temporary.name))
        self.page = mock.MagicMock()
        self.ui = self.new_ui()
        self.ui.select(None, "project-a")

    def new_ui(self):
        ui = ComposerDrafts(self.page, ft.TextField(), ft.Dropdown(value="research"),
                            ft.Dropdown(value="agent"), store=self.store)
        self.addCleanup(ui.close)
        return ui

    def edit(self, text, *, touched=False):
        self.ui.request.value = text
        self.ui.capture(touched=touched)

    def test_explicit_clear_invalidates_old_empty_disk_revision(self):
        blank = self.store.save("session:one", "", expected_revision="")
        self.ui.select("one")
        self.edit("typed just before clearing")
        self.ui.clear()
        self.assertEqual("", self.ui.request.value)
        with self.assertRaises(DraftConflict):
            self.store.save("session:one", "old window writes back", expected_revision=blank["revision"])

    def test_close_flushes_latest_control_value_when_scheduler_is_disconnected(self):
        self.ui.request.value = "last input before disconnect"
        with mock.patch.object(self.page, "run_task", side_effect=RuntimeError("session disconnected")):
            self.ui.close()
        self.assertEqual("last input before disconnect", self.store.load("project:project-a")["text"])
        self.assertTrue(self.ui._closed)

    def test_close_flushes_latest_control_value_when_page_update_is_disconnected(self):
        self.ui.request.value = "last input before disconnect"
        with mock.patch.object(self.page, "update", side_effect=RuntimeError("session disconnected")):
            self.ui.close()
        self.assertEqual("last input before disconnect", self.store.load("project:project-a")["text"])
        self.assertTrue(self.ui._closed)

    def test_first_submission_failure_after_adoption_recovers_in_created_session(self):
        self.edit("retry the first request")
        self.ui.agent.value, self.ui.mode.value = "coder", "review"
        ticket = self.ui.submitted()
        self.ui.adopt_session("created", ticket)
        self.ui.finish(ticket, accepted=False)
        self.ui.close()
        reopened = self.new_ui()
        reopened.select("created")
        self.assertEqual("retry the first request", reopened.request.value)
        self.assertEqual(("coder", "review"), (reopened.agent.value, reopened.mode.value))
        self.assertEqual("", self.store.load("project:project-a")["text"])

    def test_failed_adoption_write_preserves_origin_and_visible_text(self):
        from src import state_store

        self.edit("not yet submitted successfully")
        ticket = self.ui.submitted()
        target_name = hashlib.sha256(b"session:created").hexdigest() + ".json"
        replace = state_store.os.replace

        def fail_target(source, target):
            if Path(target).name == target_name:
                raise OSError("target disk write failed")
            return replace(source, target)

        with mock.patch.object(state_store.os, "replace", side_effect=fail_target):
            self.ui.adopt_session("created", ticket)
            self.ui.finish(ticket, accepted=False)
            self.assertEqual("not yet submitted successfully", self.ui.request.value)
            self.assertEqual("not yet submitted successfully", self.store.load("project:project-a")["text"])
            self.assertEqual("", self.store.load("session:created")["text"])

    def test_external_revision_aba_prevents_success_from_clearing_new_disk_version(self):
        self.edit("same request")
        ticket = self.ui.submitted()
        first = self.store.load("project:project-a")
        peer = DraftStore(Path(self.temporary.name))
        middle = peer.save("project:project-a", "different request", expected_revision=first["revision"])
        latest = peer.save("project:project-a", "same request", expected_revision=middle["revision"])
        self.ui.finish(ticket, accepted=True)
        self.assertEqual(latest, peer.load("project:project-a"))
        self.assertTrue(self.ui.resolve_button.visible)
        self.ui.close()
        self.assertEqual(latest, peer.load("project:project-a"))

    def test_same_text_input_event_after_submit_preserves_new_edit(self):
        self.edit("same request")
        ticket = self.ui.submitted()
        self.edit("same request", touched=True)
        self.ui.finish(ticket, accepted=True)
        self.assertEqual("same request", self.ui.request.value)
        self.ui.flush()
        self.assertEqual("same request", self.store.load("project:project-a")["text"])

    def test_stale_timer_after_close_never_writes_or_updates_controls(self):
        self.edit("pending input")
        callback, scope, generation = self.page.run_task.call_args.args
        timer = self.page.run_task.return_value
        self.ui.close()
        timer.cancel.assert_called()
        row = self.store.load("project:project-a")
        with mock.patch.object(self.store, "save", wraps=self.store.save) as save:
            self.page.update.reset_mock()
            asyncio.run(callback(scope, generation, delay=0))
            save.assert_not_called()
            self.page.update.assert_not_called()
        self.assertEqual(row, self.store.load("project:project-a"))

    def test_late_finish_after_close_does_not_update_disconnected_page(self):
        self.ui.changed = lambda: self.page.update()
        self.edit("accepted after disconnect")
        ticket = self.ui.submitted()
        self.ui.close()
        with mock.patch.object(self.page, "update", side_effect=RuntimeError("session disconnected")):
            self.ui.finish(ticket, accepted=True)
        self.assertEqual("", self.store.load("project:project-a")["text"])

    def conflict_dialog(self):
        self.edit("local unsent text")
        self.ui.agent.value, self.ui.mode.value = "coder", "plan"
        saved = self.store.save("project:project-a", "peer saved text", agent="reviewer", mode="review",
                                expected_revision="")
        self.assertFalse(self.ui.flush())
        self.ui.resolve_button.on_click(None)
        return saved, self.page.show_dialog.call_args.args[0]

    def test_conflict_dialog_save_current_uses_displayed_disk_revision(self):
        peer, dialog = self.conflict_dialog()
        dialog.actions[0].on_click(None)
        saved = self.store.load("project:project-a")
        self.assertEqual(("local unsent text", "coder", "plan"),
                         (saved["text"], saved["agent"], saved["mode"]))
        self.assertNotEqual(saved["revision"], peer["revision"])
        self.assertEqual("local unsent text", self.ui.request.value)
        self.assertFalse(self.ui.resolve_button.visible)
        self.page.pop_dialog.assert_called_once()

    def test_conflict_dialog_restore_disk_restores_text_and_options(self):
        peer, dialog = self.conflict_dialog()
        dialog.actions[1].on_click(None)
        self.assertEqual(("peer saved text", "reviewer", "review"),
                         (self.ui.request.value, self.ui.agent.value, self.ui.mode.value))
        self.assertEqual(peer, self.store.load("project:project-a"))
        self.assertFalse(self.ui.resolve_button.visible)
        self.assertTrue(self.ui.flush())

    def test_conflict_dialog_cancel_keeps_local_and_disk_versions(self):
        peer, dialog = self.conflict_dialog()
        dialog.actions[2].on_click(None)
        self.assertEqual("local unsent text", self.ui.request.value)
        self.assertEqual(peer, self.store.load("project:project-a"))
        self.assertTrue(self.ui.resolve_button.visible)
        self.page.pop_dialog.assert_called_once()

    def test_conflict_dialog_rejects_either_resolution_after_disk_changes(self):
        for action in (0, 1):
            with self.subTest(action=action):
                scope = f"session:dialog-{action}"
                self.ui.select(f"dialog-{action}")
                self.edit("keep local")
                old = self.store.save(scope, "shown disk", expected_revision="")
                self.assertFalse(self.ui.flush())
                self.ui.resolve_button.on_click(None)
                dialog = self.page.show_dialog.call_args.args[0]
                latest = self.store.save(scope, "new disk", expected_revision=old["revision"])
                self.page.pop_dialog.reset_mock()
                dialog.actions[action].on_click(None)
                self.assertEqual("keep local", self.ui.request.value)
                self.assertEqual(latest, self.store.load(scope))
                self.assertTrue(self.ui.resolve_button.visible)
                self.page.pop_dialog.assert_not_called()
                # Reopen to explicitly review the new disk version, then retry.
                self.ui.resolve_button.on_click(None)
                self.page.show_dialog.call_args.args[0].actions[1].on_click(None)
                self.assertEqual("new disk", self.ui.request.value)

    def test_conflict_dialog_retry_after_atomic_write_failure_keeps_local(self):
        from src import state_store

        peer, dialog = self.conflict_dialog()
        with mock.patch.object(state_store.os, "replace", side_effect=OSError("disk temporarily full")):
            for _ in range(2):
                dialog.actions[0].on_click(None)
                self.assertEqual("local unsent text", self.ui.request.value)
                self.assertEqual(peer, self.store.load("project:project-a"))
        dialog.actions[0].on_click(None)
        self.assertEqual("local unsent text", self.store.load("project:project-a")["text"])
        self.assertFalse(self.ui.resolve_button.visible)

    def test_unreadable_draft_repeated_checks_then_repair_can_restore_disk(self):
        saved = self.store.save("session:damaged", "recover original", agent="reviewer", mode="review",
                                expected_revision="")
        path = Path(self.temporary.name) / (hashlib.sha256(b"session:damaged").hexdigest() + ".json")
        original_bytes = path.read_bytes()
        path.write_text("{bad JSON", encoding="utf-8")
        self.ui.select("damaged")
        self.edit("keep new local text")
        for _ in range(2):
            self.ui.resolve_button.on_click(None)
            self.assertEqual("keep new local text", self.ui.request.value)
            self.assertEqual("{bad JSON", path.read_text(encoding="utf-8"))
        path.write_bytes(original_bytes)
        self.ui.resolve_button.on_click(None)
        self.page.show_dialog.call_args.args[0].actions[1].on_click(None)
        self.assertEqual("recover original", self.ui.request.value)
        self.assertEqual(("reviewer", "review"), (self.ui.agent.value, self.ui.mode.value))
        self.assertEqual(saved, self.store.load("session:damaged"))
        self.edit("continue after recovery")
        self.assertTrue(self.ui.flush())

    def test_unreadable_draft_can_save_local_after_original_file_is_repaired(self):
        self.store.save("session:damaged", "recover original", expected_revision="")
        path = Path(self.temporary.name) / (hashlib.sha256(b"session:damaged").hexdigest() + ".json")
        original_bytes = path.read_bytes()
        path.write_text("{bad JSON", encoding="utf-8")
        self.ui.select("damaged")
        self.edit("preserve local instead")
        self.ui.agent.value, self.ui.mode.value = "coder", "plan"
        path.write_bytes(original_bytes)
        self.ui.resolve_button.on_click(None)
        self.page.show_dialog.call_args.args[0].actions[0].on_click(None)
        saved = self.store.load("session:damaged")
        self.assertEqual(("preserve local instead", "coder", "plan"),
                         (saved["text"], saved["agent"], saved["mode"]))
        self.assertTrue(self.ui.flush())

    def test_clean_cached_scope_reloads_peer_update_when_selected_again(self):
        first = self.store.save("session:shared", "original", expected_revision="")
        self.ui.select("shared")
        self.ui.select("elsewhere")
        peer = DraftStore(Path(self.temporary.name))
        latest = peer.save("session:shared", "peer update", agent="coder", mode="review",
                           expected_revision=first["revision"])
        self.ui.select("shared")
        self.assertEqual(("peer update", "coder", "review"),
                         (self.ui.request.value, self.ui.agent.value, self.ui.mode.value))
        self.assertEqual(latest, self.ui._drafts["session:shared"].row)

    def test_dirty_cached_scope_keeps_unsaved_text_when_peer_changes(self):
        first = self.store.save("session:shared", "original", expected_revision="")
        self.ui.select("shared")
        self.edit("must keep local")
        peer = DraftStore(Path(self.temporary.name))
        latest = peer.save("session:shared", "peer update", expected_revision=first["revision"])
        self.ui.select("elsewhere")
        self.ui.select("shared")
        self.assertEqual("must keep local", self.ui.request.value)
        self.assertEqual(latest, peer.load("session:shared"))
        self.assertTrue(self.ui.resolve_button.visible)

    def test_clean_cached_scope_read_error_keeps_cached_text_and_can_recover(self):
        saved = self.store.save("session:shared", "cached recovery text", agent="coder", mode="review",
                                expected_revision="")
        self.ui.select("shared")
        self.ui.select("elsewhere")
        path = Path(self.temporary.name) / (hashlib.sha256(b"session:shared").hexdigest() + ".json")
        original_bytes = path.read_bytes()
        path.write_text("{bad JSON", encoding="utf-8")
        self.ui.select("shared")
        self.assertEqual(("cached recovery text", "coder", "review"),
                         (self.ui.request.value, self.ui.agent.value, self.ui.mode.value))
        self.assertTrue(self.ui.resolve_button.visible)
        self.assertEqual("{bad JSON", path.read_text(encoding="utf-8"))
        path.write_bytes(original_bytes)
        self.ui.resolve_button.on_click(None)
        self.page.show_dialog.call_args.args[0].actions[1].on_click(None)
        self.assertEqual("cached recovery text", self.ui.request.value)
        self.assertFalse(self.ui.resolve_button.visible)
        self.assertEqual(saved, self.store.load("session:shared"))

    def test_new_interpreter_reads_persisted_draft_after_writer_exits(self):
        code = (
            "import json, sys; from src.composer_drafts import DraftStore; "
            "store = DraftStore(sys.argv[1]); "
            "row = (store.save('session:restart', '\\u4e2d\\u6587\\r\\n\\U0001f600', "
            "agent='coder', mode='review', expected_revision='') "
            "if sys.argv[2] == 'write' else store.load('session:restart')); "
            "print(json.dumps(row, ensure_ascii=True))"
        )
        rows = []
        for operation in ("write", "read"):
            result = subprocess.run([sys.executable, "-c", code, self.temporary.name, operation],
                                    cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, timeout=15, check=True,
                                    env={**os.environ, "GEZHI_DATA_DIR": self.temporary.name})
            rows.append(json.loads(result.stdout))
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(("中文\r\n😀", "coder", "review"),
                         (rows[1]["text"], rows[1]["agent"], rows[1]["mode"]))


class DesktopDraftLifecycleReviewTest(unittest.TestCase):
    def setUp(self):
        isolated = tempfile.TemporaryDirectory(prefix="desktop-draft-lifecycle-")
        self.addCleanup(isolated.cleanup)
        environment = mock.patch.dict(os.environ, {"GEZHI_DATA_DIR": isolated.name})
        environment.start()
        self.addCleanup(environment.stop)
        from tests.test_desktop_draft_audit import DesktopDraftAuditTests

        fixture = DesktopDraftAuditTests("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.ui = fixture.ui
        self.addCleanup(self.ui.drafts.close)

    def test_first_session_for_material_keeps_text_agent_and_mode(self):
        self.ui.agent_request.value = "draft to accompany the material"
        self.ui.agent_pick.value = "coder"
        self.ui.agent_mode.value = "review"
        self.ui.draft_changed()
        with mock.patch("src.ui.session_composer.handle", return_value={
                "ok": True, "session_id": "created-for-material"}), \
                mock.patch.object(self.ui.composer, "refresh"):
            self.assertEqual("created-for-material", self.ui.composer.ensure_session())
        self.assertEqual("draft to accompany the material", self.ui.agent_request.value)
        self.assertEqual(("coder", "review"), (self.ui.agent_pick.value, self.ui.agent_mode.value))

    def test_first_material_session_write_failure_preserves_origin_backup(self):
        from src import state_store

        self.ui.agent_request.value = "material request must remain recoverable"
        self.ui.agent_pick.value, self.ui.agent_mode.value = "coder", "review"
        self.ui.draft_changed()
        original_scope = self.ui.drafts.scope
        target_name = hashlib.sha256(b"session:created-for-material").hexdigest() + ".json"
        replace = state_store.os.replace

        def fail_target(source, target):
            if Path(target).name == target_name:
                raise OSError("new session draft cannot be written")
            return replace(source, target)

        with mock.patch("src.ui.session_composer.handle", return_value={
                "ok": True, "session_id": "created-for-material"}), \
                mock.patch.object(self.ui.composer, "refresh"), \
                mock.patch.object(state_store.os, "replace", side_effect=fail_target):
            self.assertEqual("created-for-material", self.ui.composer.ensure_session())
            self.assertEqual("material request must remain recoverable", self.ui.agent_request.value)
            self.assertEqual(("coder", "review"), (self.ui.agent_pick.value, self.ui.agent_mode.value))
            self.ui.drafts.close()
            source = self.ui.drafts.store.load(original_scope)
            self.assertEqual("material request must remain recoverable", source["text"])
            self.assertEqual(("coder", "review"), (source["agent"], source["mode"]))


if __name__ == "__main__":
    unittest.main()
