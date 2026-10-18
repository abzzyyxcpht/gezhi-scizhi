"""Checkpoint failures and concurrent deletion must preserve committed state."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src import service, state_store
from src.orchestrator import engine
from src.orchestrator.engine import TaskContext


class CheckpointPersistenceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.ctx = TaskContext("checkpoint-fixture", "research_pipeline", {"topic": "研究"})
        self.ctx.status, self.ctx.pointer = "paused", "lit_filter"
        self.ctx.artifacts = {"lit_filter": {"pool": [{"id": "W1"}]}}
        self.path = self.root / f"{self.ctx.task_id}.json"
        for patcher in (
            mock.patch.object(engine, "CHECKPOINT_DIR", self.root),
            mock.patch.object(service, "CHECKPOINT_DIR", self.root),
            mock.patch.object(service, "_tasks", {self.ctx.task_id: self.ctx}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ctx.save()
        self.committed = self.path.read_bytes()

    def assert_committed(self):
        self.assertEqual(self.path.read_bytes(), self.committed)
        self.assertEqual(TaskContext.load(self.ctx.task_id).artifacts,
                         {"lit_filter": {"pool": [{"id": "W1"}]}})
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_partial_write_preserves_last_complete_checkpoint(self):
        original = Path.write_text

        def partial_write(path, text, **kwargs):
            original(path, text[:20], **kwargs)
            raise OSError("fixture disk full")

        self.ctx.artifacts["lit_filter"]["pool"].append({"id": "W2"})
        with mock.patch.object(Path, "write_text", partial_write):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.ctx.save()
        self.assert_committed()

    def test_replace_failure_preserves_last_complete_checkpoint(self):
        self.ctx.artifacts["lit_filter"]["pool"].append({"id": "W2"})
        with mock.patch.object(state_store.os, "replace", side_effect=OSError("fixture disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.ctx.save()
        self.assert_committed()

    def test_unsupported_artifact_does_not_stringify_or_damage_checkpoint(self):
        self.ctx.artifacts["invalid"] = object()
        with self.assertRaises(TypeError):
            self.ctx.save()
        self.assert_committed()

    def test_delete_waits_for_inflight_save_and_prevents_recreation(self):
        entered, release, cancelled = (threading.Event() for _ in range(3))
        original_write, original_cancel = Path.write_text, self.ctx.request_cancel
        errors, result = [], []

        def delayed_write(path, text, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("fixture save barrier")
            return original_write(path, text, **kwargs)

        def cancel():
            original_cancel()
            cancelled.set()

        def save():
            try:
                self.ctx.save()
            except Exception as exc:
                errors.append(exc)

        def delete():
            try:
                result.append(service.delete_task(self.ctx.task_id))
            except Exception as exc:
                errors.append(exc)

        saver, deleter = threading.Thread(target=save), threading.Thread(target=delete)
        with mock.patch.object(Path, "write_text", delayed_write), \
                mock.patch.object(self.ctx, "request_cancel", cancel):
            try:
                saver.start()
                self.assertTrue(entered.wait(3))
                deleter.start()
                self.assertTrue(cancelled.wait(3))
            finally:
                release.set()
                for worker in (saver, deleter):
                    if worker.ident is not None:
                        worker.join(5)
                        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result, [True])
        self.assertFalse(self.path.exists())
        self.ctx.save()
        service.restore_tasks()
        self.assertIsNone(service.get(self.ctx.task_id))

    def test_save_waiting_for_lock_observes_completed_deletion(self):
        entered, release = threading.Event(), threading.Event()
        lock = threading.Lock()
        errors = []

        class DelayedWriterLock:
            def __enter__(inner):
                if threading.current_thread().name == "fixture-writer":
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("fixture lock barrier")
                lock.acquire()

            def __exit__(inner, *args):
                lock.release()

        def save():
            try:
                self.ctx.save()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=save, name="fixture-writer")
        with mock.patch.object(self.ctx, "_save_lock", DelayedWriterLock()):
            try:
                worker.start()
                self.assertTrue(entered.wait(3))
                self.assertTrue(service.delete_task(self.ctx.task_id))
            finally:
                release.set()
                worker.join(5)
                self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse(self.path.exists())

    def test_failed_delete_keeps_task_available_for_retry(self):
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("fixture sharing violation")):
            with self.assertRaises(PermissionError):
                service.delete_task(self.ctx.task_id)
        self.assertIs(service.get(self.ctx.task_id), self.ctx)
        self.assertFalse(getattr(self.ctx, "_deleted", False))
        self.assertTrue(self.ctx.cancel_requested)
        self.assert_committed()
        self.ctx.save()
        self.assertTrue(service.delete_task(self.ctx.task_id))
        self.assertIsNone(service.get(self.ctx.task_id))
        self.assertFalse(self.path.exists())

    def test_restore_cannot_re_register_task_during_delete(self):
        entered, release, restored = (threading.Event() for _ in range(3))
        original = Path.unlink
        errors = []

        def delayed_unlink(path, *args, **kwargs):
            if path == self.path:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("fixture delete barrier")
            return original(path, *args, **kwargs)

        def delete():
            try:
                service.delete_task(self.ctx.task_id)
            except Exception as exc:
                errors.append(exc)

        def restore():
            try:
                service.restore_tasks()
            except Exception as exc:
                errors.append(exc)
            finally:
                restored.set()

        deleter, restorer = threading.Thread(target=delete), threading.Thread(target=restore)
        with mock.patch.object(Path, "unlink", delayed_unlink):
            try:
                deleter.start()
                self.assertTrue(entered.wait(3))
                restorer.start()
                self.assertFalse(restored.wait(.1))
            finally:
                release.set()
                for worker in (deleter, restorer):
                    if worker.ident is not None:
                        worker.join(5)
                        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertIsNone(service.get(self.ctx.task_id))
        self.assertFalse(self.path.exists())

    def test_atomic_writer_keeps_default_string_conversion(self):
        path = self.root / "settings.json"
        state_store.write_json(path, {"path": Path("local")})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"path": "local"})

    def test_atomic_writer_supports_explicit_serializer(self):
        path = self.root / "settings.json"
        state_store.write_json(path, {"path": Path("local")}, default=lambda value: value.name.upper())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"path": "LOCAL"})


if __name__ == "__main__":
    unittest.main()
