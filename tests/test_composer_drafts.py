"""Local composer drafts: isolated persistence, validation, and real CAS races."""
from __future__ import annotations

import importlib
import json
import multiprocessing
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest import mock


def _race_save(root, barrier, output, text):
    from src.composer_drafts import DraftConflict, DraftStore

    barrier.wait(timeout=10)
    try:
        row = DraftStore(root).save("session:race", text, expected_revision="")
        output.put(("saved", row))
    except DraftConflict as exc:
        output.put(("conflict", exc.current))


class DraftStoreTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("src.composer_drafts")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "drafts"
        self.store = self.module.DraftStore(self.root)

    def save(self, scope="session:one", text="original", **kwargs):
        return self.store.save(scope, text, expected_revision=kwargs.pop("expected_revision", ""), **kwargs)

    def stored_path(self):
        paths = list(self.root.glob("*.json"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def test_missing_scope_returns_blank_row_without_writing_a_draft(self):
        self.assertEqual(self.store.load("session:one"), {
            "schema": 1, "scope": "session:one", "revision": "", "text": "",
            "agent": "research", "mode": "agent", "updated_at": "",
        })
        self.assertEqual(list(self.root.glob("*.json")), [])

    def test_default_root_is_configured_data_subdirectory(self):
        isolated_data = Path(self.temporary.name) / "data"
        with mock.patch.dict(os.environ, {"GEZHI_DATA_DIR": str(isolated_data)}):
            config = importlib.import_module("src.config")
        with mock.patch.object(config, "DATA_DIR", isolated_data):
            store = self.module.DraftStore()
            row = store.save("project:", "unassigned", expected_revision="")
        self.assertEqual(len(list((isolated_data / "composer_drafts").glob("*.json"))), 1)
        self.assertEqual(self.module.DraftStore(isolated_data / "composer_drafts").load("project:"), row)

    def test_scopes_are_independent_and_reload_after_restart(self):
        expected = {}
        for scope in ("session:same", "project:same", "project:", "session:other"):
            expected[scope] = self.save(scope, "  中文\r\n😀 " + scope, agent="reviewer", mode="review")
        reopened = self.module.DraftStore(self.root)
        self.assertEqual({scope: reopened.load(scope) for scope in expected}, expected)
        self.assertEqual(len(list(self.root.glob("*.json"))), 4)
        for row in expected.values():
            self.assertTrue(row["revision"])
            self.assertIsNotNone(datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")).tzinfo)

    def test_files_use_only_hashed_scopes_in_the_explicit_root(self):
        row = self.save("session:PRIVATE_session-1", "private")
        path = self.stored_path()
        self.assertRegex(path.name, r"^[0-9a-f]{64}\.json$")
        self.assertEqual(path.parent, self.root)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), row)

    def test_safe_id_boundaries_accept_leading_underscore_and_hyphen(self):
        for scope in ("session:_-", "session:" + "a" * 128, "project:" + "b" * 128):
            self.assertEqual(self.save(scope, "valid", agent="_-" + "x" * 126)["scope"], scope)

    def test_invalid_scopes_are_rejected_before_creating_files(self):
        for scope in (None, 3, "", "session:", "workspace:one", "SESSION:one", "project:../escape",
                      "session:a/b", "session:a\\b", "project:a:b", "session:a\n", "session:中文",
                      "session:" + "a" * 129, "project:" + "a" * 129):
            for operation in (lambda: self.store.load(scope),
                              lambda: self.store.save(scope, "x", expected_revision=""),
                              lambda: self.store.clear(scope, expected_revision="")):
                with self.subTest(scope=scope), self.assertRaises(ValueError):
                    operation()
        self.assertFalse(self.root.exists())

    def test_two_instances_conflict_without_losing_the_first_writer(self):
        peer = self.module.DraftStore(self.root)
        stale = peer.load("session:one")
        saved = self.save()
        with self.assertRaises(self.module.DraftConflict) as caught:
            peer.save("session:one", "stale", expected_revision=stale["revision"])
        self.assertEqual(caught.exception.current, saved)
        self.assertEqual(self.store.load("session:one"), saved)

    def test_successful_update_changes_revision(self):
        first = self.save()
        second = self.save(text="updated", agent="coder", mode="plan", expected_revision=first["revision"])
        self.assertNotEqual(second["revision"], first["revision"])
        self.assertEqual(self.store.load("session:one"), second)

    def test_same_payload_is_noop_only_with_current_revision(self):
        first = self.save()
        path = self.stored_path()
        before = path.stat().st_mtime_ns
        with mock.patch.object(self.module, "write_json", side_effect=AssertionError("unnecessary write")):
            self.assertEqual(self.save(expected_revision=first["revision"]), first)
            with self.assertRaises(self.module.DraftConflict):
                self.save(expected_revision="")
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_clear_keeps_preferences_and_tombstone_blocks_old_writer(self):
        first = self.save(agent="coder", mode="plan")
        cleared = self.store.clear("session:one", expected_revision=first["revision"])
        self.assertEqual((cleared["text"], cleared["agent"], cleared["mode"]), ("", "coder", "plan"))
        self.assertTrue(cleared["revision"])
        self.assertNotEqual(cleared["revision"], first["revision"])
        self.assertEqual(self.module.DraftStore(self.root).load("session:one"), cleared)
        for old_revision in ("", first["revision"]):
            with self.subTest(old_revision=old_revision), self.assertRaises(self.module.DraftConflict):
                self.save(text="revived", expected_revision=old_revision)
        with self.assertRaises(self.module.DraftConflict):
            self.store.clear("session:one", expected_revision=first["revision"])

    def test_clear_missing_scope_writes_tombstone(self):
        row = self.store.clear("project:", expected_revision="")
        self.assertEqual(row["text"], "")
        self.assertTrue(row["revision"])
        self.assertEqual(self.store.load("project:"), row)
        with self.assertRaises(self.module.DraftConflict):
            self.save("project:", "stale", expected_revision="")

    def test_save_empty_missing_scope_has_a_persisted_revision(self):
        row = self.save(text="")
        self.assertTrue(row["revision"])
        self.assertEqual(self.store.load("session:one"), row)

    def test_clearing_an_already_empty_draft_invalidates_old_revision(self):
        first = self.save(text="")
        cleared = self.store.clear("session:one", expected_revision=first["revision"])
        self.assertNotEqual(cleared["revision"], first["revision"])
        with self.assertRaises(self.module.DraftConflict):
            self.save(text="edited in an old window", expected_revision=first["revision"])

    def test_length_limit_counts_python_characters_and_never_truncates(self):
        text = "😀" * 31_997 + "\r\n中"
        self.assertEqual(len(text), 32_000)
        first = self.save(text=text)
        self.assertEqual(self.store.load("session:one")["text"], text)
        with self.assertRaises(ValueError):
            self.save(text=text + "x", expected_revision=first["revision"])
        self.assertEqual(self.store.load("session:one"), first)

    def test_invalid_payloads_and_revision_types_preserve_current_row(self):
        first = self.save()
        for overrides in ({"text": None}, {"text": 1}, {"agent": ""}, {"agent": "../coder"},
                          {"agent": "a" * 129}, {"agent": ["research"]}, {"mode": "unknown"},
                          {"mode": None}, {"mode": ["agent"]}, {"expected_revision": None},
                          {"expected_revision": 1}):
            kwargs = {"text": "changed", "agent": "research", "mode": "agent",
                      "expected_revision": first["revision"], **overrides}
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.store.save("session:one", **kwargs)
        with self.assertRaises(TypeError):
            self.store.save("session:one", "unguarded")
        with self.assertRaises(TypeError):
            self.store.clear("session:one")
        with self.assertRaises(ValueError):
            self.store.clear("session:one", expected_revision=None)
        self.assertEqual(self.store.load("session:one"), first)

    def test_corrupt_json_and_incompatible_schema_are_never_overwritten(self):
        original = self.save()
        path = self.stored_path()
        invalid_rows = ["{unfinished", "[]", "null", json.dumps({**original, "schema": 2}),
                        json.dumps({**original, "schema": True}),
                        json.dumps({**original, "scope": "session:wrong"}),
                        json.dumps({**original, "revision": ""}),
                        json.dumps({**original, "text": None}),
                        json.dumps({**original, "text": "x" * 32_001}),
                        json.dumps({**original, "agent": "../escape"}),
                        json.dumps({**original, "mode": "unknown"}),
                        json.dumps({**original, "updated_at": "yesterday"}),
                        json.dumps({**original, "updated_at": "2026-01-01T01:00:00"}),
                        json.dumps({key: value for key, value in original.items() if key != "text"})]
        for payload in invalid_rows:
            path.write_text(payload, encoding="utf-8")
            for operation in (lambda: self.store.load("session:one"),
                              lambda: self.save(expected_revision=original["revision"]),
                              lambda: self.store.clear("session:one", expected_revision=original["revision"])):
                with self.subTest(payload=payload[:100]), self.assertRaises(ValueError):
                    operation()
                self.assertEqual(path.read_text(encoding="utf-8"), payload)

    def test_atomic_replace_failure_keeps_old_file_and_cleans_temporary_file(self):
        state_store = importlib.import_module("src.state_store")
        first = self.save()
        path = self.stored_path()
        before = path.read_bytes()
        with mock.patch.object(state_store.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.save(text="new", expected_revision=first["revision"])
            with self.assertRaises(OSError):
                self.store.clear("session:one", expected_revision=first["revision"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.store.load("session:one"), first)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_invalid_encoding_reports_corrupt_store_and_preserves_bytes(self):
        self.save()
        path = self.stored_path()
        damaged = b"\xff\xfe{\x00"
        path.write_bytes(damaged)
        with self.assertRaises(self.module.DraftError) as caught:
            self.store.load("session:one")
        self.assertEqual(caught.exception.code, "corrupt_store")
        self.assertEqual(path.read_bytes(), damaged)

    def test_old_scopes_are_retained(self):
        first = self.save("session:old", "old unsent text")
        for index in range(40):
            self.save(f"project:new-{index}", "new")
        self.assertEqual(self.module.DraftStore(self.root).load("session:old"), first)

    def test_concurrent_threads_have_exactly_one_winner(self):
        barrier = threading.Barrier(8)

        def write(index):
            store = self.module.DraftStore(self.root)
            barrier.wait(timeout=10)
            try:
                return "saved", store.save("session:race", str(index), expected_revision="")
            except self.module.DraftConflict as exc:
                return "conflict", exc.current

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(write, range(8)))
        self.assertEqual(sum(status == "saved" for status, _ in results), 1)
        current = self.store.load("session:race")
        self.assertTrue(all(row == current for _, row in results))

    def test_concurrent_processes_have_exactly_one_winner(self):
        context = multiprocessing.get_context("spawn")
        barrier, output = context.Barrier(3), context.Queue()
        workers = [context.Process(target=_race_save, args=(str(self.root), barrier, output, str(index)))
                   for index in range(3)]
        try:
            for worker in workers:
                worker.start()
            results = [output.get(timeout=20) for _ in workers]
            for worker in workers:
                worker.join(timeout=5)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(sum(status == "saved" for status, _ in results), 1)
            current = self.store.load("session:race")
            self.assertTrue(all(row == current for _, row in results))
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                if worker.pid is not None:
                    worker.join(timeout=2)
            output.close()
            output.join_thread()


if __name__ == "__main__":
    unittest.main()
