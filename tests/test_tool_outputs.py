"""Bounded, session-scoped archival of complete tool outputs."""
from __future__ import annotations

import hashlib
import importlib
import json
import multiprocessing
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


def _publish_in_process(root, barrier, queue):
    from src.tool_outputs import ToolOutputStore
    barrier.wait(timeout=10)
    queue.put(ToolOutputStore(root).publish("shared", "same", "demo", "中文😀" * 100))


class ToolOutputStoreTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("src.tool_outputs")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "sessions"
        self.store = self.module.ToolOutputStore(self.root)

    def publish(self, text="hello 中文😀", session="one", call="call-1", **kwargs):
        return self.store.publish(session, call, "test.tool", text, **kwargs)

    def data_path(self, ref, session="one"):
        return self.root / ".tool-outputs" / session / (ref["output_id"] + ".txt")

    def test_publish_complete_utf8_reference_and_idempotent_restart(self):
        content = "first\r\n中文😀\x00tail"
        ref = self.publish(content)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        expected = "out_" + hashlib.sha256(("call-1\0json\0" + digest).encode()).hexdigest()[:32]
        self.assertEqual(ref["output_id"], expected)
        self.assertEqual(ref["total_bytes"], len(content.encode("utf-8")))
        self.assertEqual(ref["sha256"], digest)
        self.assertEqual((ref["tool"], ref["call_id"], ref["kind"]), ("test.tool", "call-1", "json"))
        self.assertTrue(ref["created_at"])
        self.assertNotIn(str(self.root), json.dumps(ref))
        path = self.data_path(ref)
        self.assertEqual(path.read_bytes(), content.encode("utf-8"))
        stamp = path.stat().st_mtime_ns
        restarted = self.module.ToolOutputStore(self.root)
        self.assertEqual(restarted.publish("one", "call-1", "test.tool", content), ref)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.assertEqual(restarted.read("one", expected)["text"], content)

    def test_changed_content_and_kind_get_new_ids_without_removing_old_outputs(self):
        refs = [self.publish("a"), self.publish("b"), self.publish("b", kind="text")]
        self.assertEqual(len({row["output_id"] for row in refs}), 3)
        self.assertEqual([self.store.read("one", row["output_id"])["text"] for row in refs], ["a", "b", "b"])

    def test_session_and_root_isolation(self):
        ref = self.publish()
        for store, sid in ((self.store, "two"), (self.module.ToolOutputStore(self.root / "other"), "one")):
            with self.assertRaises(FileNotFoundError):
                store.read(sid, ref["output_id"])
            self.assertEqual(store.list(sid), {"items": [], "total": 0, "next_offset": None})

    def test_unknown_output_in_existing_session_does_not_disclose_disk_path(self):
        self.publish()
        with self.assertRaises(FileNotFoundError) as caught:
            self.store.read("one", "out_" + "0" * 32)
        self.assertNotIn(self.root.parent.name, str(caught.exception))
        self.assertNotIn(".tool-outputs", str(caught.exception))

    def test_copied_metadata_cannot_make_another_sessions_output_readable(self):
        ref = self.publish("private")
        self.publish("other", session="two")
        target = self.data_path(ref, "two")
        target.write_bytes(self.data_path(ref).read_bytes())
        target.with_suffix(".json").write_bytes(self.data_path(ref).with_suffix(".json").read_bytes())
        with self.assertRaises(ValueError):
            self.store.read("two", ref["output_id"])

    def test_invalid_sessions_and_ids_are_rejected_without_writing(self):
        for sid in ("", "../x", "a/b", "a\\b", "a:b", "中文", "a\n", "a" * 129, None, True):
            for operation in (lambda: self.publish(session=sid), lambda: self.store.list(sid),
                              lambda: self.store.read(sid, "out_" + "0" * 32)):
                with self.subTest(sid=sid), self.assertRaises(ValueError):
                    operation()
        for output_id in ("../x", "out_" + "0" * 31, "out_" + "A" * 32, "out_" + "0" * 32 + "/x", None):
            with self.subTest(output_id=output_id), self.assertRaises(ValueError):
                self.store.read("one", output_id)
        self.assertFalse(self.root.exists())

    def test_session_id_boundaries_and_empty_output(self):
        for sid in ("_-", "a" * 128):
            ref = self.publish("", session=sid)
            page = self.store.read(sid, ref["output_id"])
            self.assertEqual((page["text"], page["offset"], page["next_offset"], page["eof"]), ("", 0, None, True))

    def test_byte_pagination_preserves_unicode_and_can_reach_long_single_line_tail(self):
        content = "a" * 32769 + "中文😀" * 900 + "FINAL_TAIL"
        ref = self.publish(content)
        pages, offset = [], 0
        while True:
            page = self.store.read("one", ref["output_id"], offset=offset, limit=4000)
            pages.append(page["text"])
            self.assertLessEqual(len(page["text"].encode("utf-8")), 4000)
            self.assertEqual(page["offset"], offset)
            self.assertEqual(page["sha256"], ref["sha256"])
            self.assertEqual(page["total_bytes"], len(content.encode("utf-8")))
            if page["eof"]:
                self.assertIsNone(page["next_offset"])
                break
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
        self.assertEqual("".join(pages), content)

    def test_byte_page_rejects_continuation_offsets_and_too_small_page(self):
        ref = self.publish("A中😀Z")
        page = self.store.read("one", ref["output_id"], limit=3)
        self.assertEqual((page["text"], page["next_offset"]), ("A", 1))
        for offset in (2, 3, 5, 6, 7):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.store.read("one", ref["output_id"], offset=offset)
        with self.assertRaises(ValueError):
            self.store.read("one", ref["output_id"], offset=1, limit=1)
        for kwargs in ({"offset": -1}, {"offset": True}, {"offset": 100}, {"limit": 0}, {"limit": 4001}, {"limit": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.read("one", ref["output_id"], **kwargs)

    def test_literal_case_sensitive_search_returns_readable_byte_offsets_and_continuation(self):
        content = "前言" + "😀MATCH[x]中文" * 23 + "match[x]"
        ref = self.publish(content)
        matches, offset = [], 0
        while True:
            page = self.store.search("one", ref["output_id"], "MATCH[x]", offset=offset, limit=10)
            self.assertLessEqual(len(page["matches"]), 10)
            matches.extend(page["matches"])
            if page["eof"]:
                self.assertIsNone(page["next_offset"])
                break
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
        self.assertEqual(len(matches), 23)
        for match in matches:
            self.assertLessEqual(len(match["preview"]), 240)
            self.assertTrue(self.store.read("one", ref["output_id"], offset=match["offset"])["text"].startswith("MATCH[x]"))
        self.assertEqual(self.store.search("one", ref["output_id"], "missing")["matches"], [])

    def test_search_validation_and_bounded_preview(self):
        ref = self.publish("中文" + "A" * 200 + "x" * 1000)
        page = self.store.search("one", ref["output_id"], "A" * 200, limit=1)
        self.assertLessEqual(len(page["matches"][0]["preview"]), 240)
        for query in ("", "a" * 201, None):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.store.search("one", ref["output_id"], query)
        for kwargs in ({"offset": 1}, {"offset": -1}, {"limit": 0}, {"limit": 11}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.search("one", ref["output_id"], "A", **kwargs)

    def test_search_handles_chunk_boundaries_nul_and_nonoverlapping_matches(self):
        content = "x" * 65534 + "中文QUERY" + "x" * 65533 + "中文QUERY" + "\0\0aaaaa"
        ref = self.publish(content)
        hits = self.store.search("one", ref["output_id"], "中文QUERY")["matches"]
        self.assertEqual([hit["offset"] for hit in hits], [65534, 65534 + len("中文QUERY".encode()) + 65533])
        self.assertEqual(len(self.store.search("one", ref["output_id"], "aa")["matches"]), 2)
        self.assertEqual(len(self.store.search("one", ref["output_id"], "\0")["matches"]), 2)

    def test_metadata_labels_are_bounded_and_invalid_inputs_do_not_write(self):
        for args in (("a" * 201, "tool", "json"), ("call", "t" * 161, "json"),
                     ("call", "tool", "html"), ("c\0all", "tool", "text"), ("call", None, "json")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.store.publish("one", args[0], args[1], "text", kind=args[2])
        self.assertFalse(self.root.exists())
        ref = self.store.publish("one", "中" * 200, "😀" * 160, "text", kind="text")
        self.assertEqual(self.store.list("one")["items"], [ref])
        for kwargs in ({"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 21}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.list("one", **kwargs)

    def test_list_paginates_and_does_not_expose_filesystem_paths(self):
        refs = [self.publish(str(index), call=str(index)) for index in range(25)]
        first = self.store.list("one")
        self.assertEqual((len(first["items"]), first["total"], first["next_offset"]), (20, 25, 20))
        second = self.store.list("one", offset=first["next_offset"])
        self.assertEqual((len(second["items"]), second["total"], second["next_offset"]), (5, 25, None))
        self.assertEqual({row["output_id"] for row in first["items"] + second["items"]}, {row["output_id"] for row in refs})
        self.assertNotIn(str(self.root), json.dumps(first))

    def test_maximum_is_utf8_bytes_and_oversize_does_not_write(self):
        with self.assertRaises(ValueError):
            self.publish("😀" * (16 * 1024 * 1024 // 4 + 1))
        self.assertFalse(self.root.exists())
        content = "x" * (16 * 1024 * 1024)
        ref = self.publish(content)
        self.assertEqual(ref["total_bytes"], 16 * 1024 * 1024)
        self.assertEqual(self.store.read("one", ref["output_id"], offset=len(content) - 1)["text"], "x")

    def test_tampering_invalidates_verified_content_and_list_reports_damage(self):
        ref = self.publish("original")
        self.store.read("one", ref["output_id"])
        path = self.data_path(ref)
        path.write_bytes(b"tampered")
        for operation in (lambda: self.store.read("one", ref["output_id"]),
                          lambda: self.store.search("one", ref["output_id"], "tampered"),
                          lambda: self.publish("original")):
            with self.assertRaises(ValueError):
                operation()
        listing = self.store.list("one")
        self.assertEqual(listing["items"], [])
        self.assertEqual(listing["errors"][0]["output_id"], ref["output_id"])
        self.assertEqual(path.read_bytes(), b"tampered")

    def test_same_size_tamper_with_restored_mtime_is_reverified(self):
        ref = self.publish("original")
        self.store.read("one", ref["output_id"])
        path = self.data_path(ref)
        previous = path.stat()
        path.write_bytes(b"tampered")
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        with self.assertRaises(ValueError):
            self.store.read("one", ref["output_id"])

    def test_invalid_metadata_scope_and_missing_data_are_explicit_errors(self):
        ref = self.publish()
        path = self.data_path(ref)
        metadata = path.with_suffix(".json")
        original = json.loads(metadata.read_text(encoding="utf-8"))
        for damaged in ("{", json.dumps({**original, "session_id": "other"}), json.dumps({**original, "total_bytes": -1})):
            metadata.write_text(damaged, encoding="utf-8")
            with self.assertRaises(ValueError):
                self.store.read("one", ref["output_id"])
            self.assertTrue(self.store.list("one")["errors"])
        metadata.write_text(json.dumps(original), encoding="utf-8")
        path.unlink()
        with self.assertRaises(ValueError):
            self.store.read("one", ref["output_id"])
        self.assertTrue(self.store.list("one")["errors"])

    def test_metadata_write_failure_never_publishes_success_and_can_retry(self):
        with mock.patch.object(self.module, "write_json", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(self.store.list("one")["items"], [])
        self.assertEqual(list((self.root / ".tool-outputs" / "one").glob("*.json")), [])
        ref = self.publish()
        self.assertEqual(self.store.read("one", ref["output_id"])["text"], "hello 中文😀")

    def test_atomic_data_replace_failure_keeps_existing_output(self):
        ref = self.publish("first")
        with mock.patch.object(self.module.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.publish("second")
        self.assertEqual(self.store.read("one", ref["output_id"])["text"], "first")
        self.assertEqual(len(self.store.list("one")["items"]), 1)
        self.assertEqual(list(self.data_path(ref).parent.glob("*.tmp")), [])

    def test_symlink_directories_and_files_cannot_escape(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        self.root.mkdir()
        link = self.root / ".tool-outputs"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symbolic links requires platform privileges")
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(list(outside.iterdir()), [])
        link.unlink()
        ref = self.publish()
        path = self.data_path(ref)
        target = outside / "secret.txt"
        target.write_text("secret", encoding="utf-8")
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.read("one", ref["output_id"])

    @unittest.skipUnless(os.name == "nt", "Windows junction protection")
    def test_windows_directory_junction_cannot_redirect_archive_or_session(self):
        import _winapi
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        self.root.mkdir()
        archive = self.root / ".tool-outputs"
        _winapi.CreateJunction(str(outside), str(archive))
        try:
            for operation in (lambda: self.publish(), lambda: self.store.list("one")):
                with self.assertRaises(ValueError):
                    operation()
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            archive.rmdir()
        archive.mkdir()
        session = archive / "one"
        _winapi.CreateJunction(str(outside), str(session))
        try:
            with self.assertRaises(ValueError):
                self.publish()
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            session.rmdir()

    def test_corrupt_list_errors_are_bounded_and_valid_outputs_still_listed(self):
        ref = self.publish()
        directory = self.data_path(ref).parent
        for index in range(23):
            (directory / ("out_" + f"{index:032x}" + ".json")).write_text("{", encoding="utf-8")
        page = self.store.list("one", limit=5)
        self.assertEqual(page["items"], [ref])
        self.assertEqual((len(page["errors"]), page["errors_total"]), (5, 23))

    def test_concurrent_instances_are_idempotent_and_keep_all_outputs(self):
        barrier = threading.Barrier(8)
        def write(index):
            store = self.module.ToolOutputStore(self.root)
            barrier.wait(timeout=10)
            common = store.publish("one", "common", "test", "same")
            store.publish("one", str(index), "test", str(index))
            return common
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(write, range(8)))
        self.assertTrue(all(row == results[0] for row in results))
        self.assertEqual(self.store.list("one")["total"], 9)

    def test_concurrent_processes_publish_one_stable_reference(self):
        context = multiprocessing.get_context("spawn")
        barrier, queue = context.Barrier(3), context.Queue()
        workers = [context.Process(target=_publish_in_process, args=(str(self.root), barrier, queue)) for _ in range(3)]
        try:
            for worker in workers:
                worker.start()
            results = [queue.get(timeout=20) for _ in workers]
            for worker in workers:
                worker.join(timeout=5)
                self.assertEqual(worker.exitcode, 0)
            self.assertTrue(all(row == results[0] for row in results))
            self.assertEqual(self.store.list("shared")["total"], 1)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                if worker.pid is not None:
                    worker.join(timeout=2)
            queue.close()
            queue.join_thread()


if __name__ == "__main__":
    unittest.main()
