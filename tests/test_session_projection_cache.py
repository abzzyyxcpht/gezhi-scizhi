"""Projection caching must stay bounded and never cache approval log reads."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from src.agent_runtime import SessionStore
from src import session_projection


class SessionProjectionCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = SessionStore(self.root)

    def seed(self, sid="one", text="Alpha"):
        self.store.append(sid, "user_message", {"text": text, "mode": "agent"})
        return sid

    def test_snapshot_and_list_share_projection_without_rereading(self):
        sid = self.seed()
        with mock.patch.object(session_projection, "read_projection", wraps=session_projection.read_projection) as read:
            self.assertEqual(self.store.snapshot(sid)["request"], "Alpha")
            self.assertEqual(self.store.list()[0]["summary"], "Alpha")
            self.store.snapshot(sid)
            self.store.list()
        self.assertEqual(read.call_count, 1)

    def test_own_append_invalidates_snapshot_and_list_immediately(self):
        sid = self.seed()
        self.assertEqual(self.store.snapshot(sid)["status"], "running")
        self.store.append(sid, "completed", {"status": "done"})
        self.assertEqual(self.store.snapshot(sid)["status"], "done")
        self.assertEqual(self.store.list()[0]["events"], 2)

    def test_other_store_append_is_visible_even_if_mtime_is_unchanged(self):
        sid = self.seed()
        path = self.root / f"{sid}.jsonl"
        previous = path.stat()
        self.store.list()
        SessionStore(self.root).append(sid, "completed", {"status": "done"})
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertEqual(self.store.list()[0]["status"], "done")
        self.assertEqual(self.store.snapshot(sid)["status"], "done")

    def test_mtime_change_invalidates_same_size_log(self):
        sid = self.seed()
        self.store.snapshot(sid)
        path = self.root / f"{sid}.jsonl"
        before = path.stat()
        text = path.read_text(encoding="utf-8").replace("Alpha", "Bravo")
        path.write_text(text, encoding="utf-8")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))
        self.assertEqual(path.stat().st_size, before.st_size)
        self.assertEqual(self.store.snapshot(sid)["request"], "Bravo")

    def test_deletion_and_new_session_from_another_store_are_visible(self):
        sid = self.seed()
        self.store.snapshot(sid)
        (self.root / f"{sid}.jsonl").unlink()
        self.assertEqual(self.store.snapshot(sid)["status"], "unknown")
        self.assertEqual(self.store.list(), [])
        SessionStore(self.root).append("two", "user_message", {"text": "New"})
        self.assertEqual([r["session_id"] for r in self.store.list()], ["two"])

    def test_real_child_process_append_and_delete_are_visible(self):
        sid = self.seed()
        path = self.root / f"{sid}.jsonl"
        self.store.list()
        writer = ("import json,sys; from pathlib import Path; "
                  "p=Path(sys.argv[1]); "
                  "event={'type':'completed','session_id':'one','payload':{'status':'done'}}; "
                  "f=p.open('a',encoding='utf-8'); f.write(json.dumps(event)+'\\n'); f.close()")
        subprocess.run([sys.executable, "-c", writer, str(path)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(self.store.snapshot(sid)["status"], "done")
        self.assertEqual(self.store.list()[0]["events"], 2)
        subprocess.run([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).unlink()",
                        str(path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.snapshot(sid)["status"], "unknown")

    def test_returned_nested_values_cannot_poison_cached_projection(self):
        sid = self.seed()
        self.store.append(sid, "plan_created", {"plan": {"steps": [{"tool": "safe"}]}})
        self.store.append(sid, "permission_requested", {
            "tool_call_id": "call", "tool": "safe", "arguments": {"nested": ["original"]}})
        snapshot = self.store.snapshot(sid)
        snapshot["plan"]["steps"][0]["tool"] = "poisoned"
        snapshot["pending"]["arguments"]["nested"][0] = "poisoned"
        rows = self.store.list()
        rows[0]["summary"] = "poisoned"
        fresh = self.store.snapshot(sid)
        self.assertEqual(fresh["plan"]["steps"][0]["tool"], "safe")
        self.assertEqual(fresh["pending"]["arguments"]["nested"], ["original"])
        self.assertEqual(self.store.list()[0]["summary"], "Alpha")

    def test_raw_read_always_reads_disk_even_with_unchanged_signature(self):
        sid = self.seed()
        path = self.root / f"{sid}.jsonl"
        previous = path.stat()
        self.store.snapshot(sid)
        self.store.read(sid)
        text = path.read_text(encoding="utf-8").replace("Alpha", "Bravo")
        path.write_text(text, encoding="utf-8")
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertEqual(self.store.read(sid)[0]["payload"]["text"], "Bravo")

    def test_cache_has_lru_bound(self):
        with mock.patch.object(SessionStore, "PROJECTION_CACHE_LIMIT", 2, create=True):
            for sid in ("one", "two", "three"):
                self.seed(sid)
            with mock.patch.object(session_projection, "read_projection", wraps=session_projection.read_projection) as read:
                self.store.snapshot("one")
                self.store.snapshot("two")
                self.store.snapshot("one")  # Keep one; evict two next.
                self.store.snapshot("three")
                self.store.snapshot("one")
                self.assertEqual(read.call_count, 3)
                self.store.snapshot("two")
                self.assertEqual(read.call_count, 4)

    def test_concurrent_readers_share_one_projection_without_mutation(self):
        sid = self.seed()
        barrier = threading.Barrier(12)

        def read_one(_):
            barrier.wait(timeout=5)
            result = self.store.snapshot(sid)
            result["request"] = "local mutation"
            return result

        with mock.patch.object(session_projection, "read_projection", wraps=session_projection.read_projection) as read, \
                ThreadPoolExecutor(max_workers=12) as workers:
            list(workers.map(read_one, range(12)))
        self.assertEqual(read.call_count, 1)
        self.assertEqual(self.store.snapshot(sid)["request"], "Alpha")

    def test_different_sessions_can_project_concurrently(self):
        self.seed("one")
        self.seed("two")
        original_read = session_projection.read_projection
        barrier = threading.Barrier(2)

        def overlapping_read(path, sid, previous=None):
            events = original_read(path, sid, previous)
            barrier.wait(timeout=2)
            return events

        with mock.patch.object(session_projection, "read_projection", side_effect=overlapping_read), \
                ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(self.store.snapshot, ("one", "two")))
        self.assertEqual({r["session_id"] for r in results}, {"one", "two"})

    def test_append_during_external_read_is_not_cached_under_stale_signature(self):
        sid = self.seed()
        original_read = session_projection.read_projection
        first = True

        def append_after_read(path, session_id, previous=None):
            nonlocal first
            events = original_read(path, session_id, previous)
            if first:
                first = False
                SessionStore(self.root).append(sid, "completed", {"status": "done"})
            return events

        with mock.patch.object(session_projection, "read_projection", side_effect=append_after_read):
            self.assertEqual(self.store.snapshot(sid)["status"], "done")
        self.assertEqual(self.store.snapshot(sid)["status"], "done")

    def test_concurrent_append_and_list_leave_complete_log(self):
        sid = self.seed()

        def append_event(index):
            self.store.append(sid, "tool_call", {"id": str(index), "name": "fixture"})
            self.store.list()

        with ThreadPoolExecutor(max_workers=8) as workers:
            list(workers.map(append_event, range(40)))
        self.assertEqual(len(self.store.read(sid)), 41)
        self.assertEqual(self.store.list()[0]["events"], 41)
        for line in (self.root / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines():
            self.assertIsInstance(json.loads(line), dict)


if __name__ == "__main__":
    unittest.main()
