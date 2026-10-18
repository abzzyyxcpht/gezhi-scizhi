"""Exports keep previous results intact when generation or publication fails."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import aigc, service
from src.orchestrator.engine import TaskContext


class ArtifactExportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.out = self.root / "results"
        self.ctx = TaskContext("export-fixture", "research_pipeline", {"topic": "Fixture"})
        self.ctx.artifacts = {
            "extract": {"digest_md": "# Original digest"},
            "review_frame": {"outline_md": "# Original outline"},
            "lit_filter": {"pool": [{"id": "w1", "title": "Fixture", "year": 2024}]},
        }
        tasks = mock.patch.object(service, "_tasks", {self.ctx.task_id: self.ctx})
        tasks.start()
        self.addCleanup(tasks.stop)

    def export(self):
        return service.export_artifacts(self.ctx.task_id, self.out)

    def files(self):
        return {path.name: path.read_bytes() for path in self.out.iterdir() if path.is_file()}

    def assert_no_staging(self):
        self.assertEqual({path.name for path in self.root.iterdir()}, {"results"})

    def test_repeated_export_removes_only_previous_managed_outputs(self):
        self.export()
        (self.out / "my-notes.txt").write_text("Keep notes", encoding="utf-8")
        self.ctx.artifacts = {}
        written = self.export()
        names = {Path(path).name for path in written}
        self.assertEqual(set(self.files()), names | {"my-notes.txt"})
        self.assertEqual((self.out / "my-notes.txt").read_text(), "Keep notes")
        self.assertTrue(all(Path(path).parent == self.out for path in written))
        self.assert_no_staging()

    def test_unowned_file_with_generated_name_is_not_removed(self):
        self.out.mkdir()
        saved = self.out / "文献摘要整理稿.md"
        saved.write_text("My independent draft", encoding="utf-8")
        self.ctx.artifacts = {}
        self.export()
        self.assertEqual(saved.read_text(encoding="utf-8"), "My independent draft")

    def test_plain_manifest_or_escape_entries_cannot_authorize_deletion(self):
        self.out.mkdir()
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        notes = self.out / "notes.md"
        notes.write_text("notes", encoding="utf-8")
        digest = self.out / "文献摘要整理稿.md"
        digest.write_text("draft", encoding="utf-8")
        (self.out / "manifest.md").write_text(
            "| 文献摘要整理稿.md | personal draft |\n", encoding="utf-8")
        self.ctx.artifacts = {}
        self.export()
        self.assertEqual(digest.read_text(encoding="utf-8"), "draft")
        (self.out / "manifest.md").write_text(aigc.manifest_text([
            {"file": "../outside.txt"}, {"file": "notes.md"}]), encoding="utf-8")
        self.export()
        self.assertEqual(outside.read_text(), "outside")
        self.assertEqual(notes.read_text(), "notes")

    def test_generation_failure_keeps_previous_files_and_removes_staging(self):
        self.export()
        before = self.files()
        self.ctx.artifacts["extract"]["digest_md"] = "# Updated digest"
        original = Path.write_text

        def fail_audit(path, *args, **kwargs):
            if path.name == "科研证据审计.md":
                raise OSError("fixture disk failure")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "write_text", fail_audit):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(self.files(), before)
        self.assert_no_staging()

    def test_failed_first_export_does_not_leave_partial_results(self):
        with mock.patch("src.research_analysis.analyze_artifacts", side_effect=OSError("audit failed")):
            with self.assertRaises(OSError):
                self.export()
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_export_uses_detached_materials_when_task_changes_during_first_write(self):
        graph = self.root / "graph.png"
        graph.write_bytes(b"fixture graph")
        self.ctx.artifacts["_evidence_graph"] = {"file": str(graph), "edges": 0}
        self.ctx.artifacts["_evidence_graph_signature"] = service.evidence_graph_signature(
            self.ctx.artifacts["lit_filter"]["pool"], self.ctx.params["topic"])
        original = Path.write_text

        def mutate_live_task_after_digest(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path.name == "文献摘要整理稿.md":
                self.ctx.params["topic"] = "Updated topic"
                self.ctx.artifacts["review_frame"]["outline_md"] = "Updated outline"
                self.ctx.artifacts["lit_filter"]["pool"][0]["title"] = "Updated title"
                self.ctx.events.append({"kind": "node", "node": "extract",
                                        "message": "智能体语义抽取"})
            return result

        with mock.patch.object(Path, "write_text", mutate_live_task_after_digest):
            written = self.export()
        self.assertIn("# Original outline", (self.out / "文献综述框架.md").read_text(encoding="utf-8"))
        ledger = json.loads((self.out / "科研证据台账.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["topic"], "Fixture")
        for name in ("参考文献.txt", "文献池.csv", "科研证据台账.json", "manifest.md"):
            self.assertNotIn("Updated", (self.out / name).read_text(encoding="utf-8-sig"))
        self.assertIn("证据图谱.png", {Path(path).name for path in written})
        self.assertEqual((self.out / "证据图谱.png").read_bytes(), b"fixture graph")
        self.assertEqual(self.ctx.params["topic"], "Updated topic")

    def test_publication_failure_restores_replaced_and_removed_files(self):
        self.export()
        before = self.files()
        self.ctx.artifacts = {}
        original = os.replace

        def fail_manifest(source, destination, *args, **kwargs):
            if Path(destination) == self.out / "manifest.md":
                raise OSError("fixture manifest publish failure")
            return original(source, destination, *args, **kwargs)

        with mock.patch.object(os, "replace", fail_manifest):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(self.files(), before)
        self.assert_no_staging()

    def test_new_files_are_removed_when_publication_fails(self):
        self.ctx.artifacts = {}
        self.export()
        before = self.files()
        self.ctx.artifacts = {"extract": {"digest_md": "new digest"}}
        original = os.replace

        def fail_manifest(source, destination, *args, **kwargs):
            if Path(destination) == self.out / "manifest.md":
                raise OSError("fixture manifest publish failure")
            return original(source, destination, *args, **kwargs)

        with mock.patch.object(os, "replace", fail_manifest):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(self.files(), before)
        self.assert_no_staging()

    def test_failed_first_publication_removes_new_destination(self):
        original = os.replace

        def fail_manifest(source, destination, *args, **kwargs):
            if Path(destination) == self.out / "manifest.md":
                raise OSError("fixture manifest publish failure")
            return original(source, destination, *args, **kwargs)

        with mock.patch.object(os, "replace", fail_manifest):
            with self.assertRaises(OSError):
                self.export()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_rollback_retains_previous_bytes_for_recovery(self):
        self.export()
        before = self.files()
        self.ctx.artifacts["extract"]["digest_md"] = "updated digest"
        original = os.replace

        def fail_publish_and_restore(source, destination, *args, **kwargs):
            if Path(destination) == self.out / "manifest.md":
                raise OSError("fixture manifest publish failure")
            if Path(source).parent.name == "previous" and Path(source).name == "文献摘要整理稿.md":
                raise OSError("fixture restore failure")
            return original(source, destination, *args, **kwargs)

        with mock.patch.object(os, "replace", fail_publish_and_restore):
            with self.assertRaisesRegex(OSError, "备份保存在") as error:
                self.export()
        recovery = list(self.root.glob(".scizhi-export-*"))
        self.assertEqual(len(recovery), 1)
        self.assertIn(str(recovery[0]), str(error.exception))
        self.assertEqual((recovery[0] / "previous" / "文献摘要整理稿.md").read_bytes(),
                         before["文献摘要整理稿.md"])

    def test_symlink_output_is_rejected_without_following_target(self):
        self.out.mkdir()
        outside = self.root / "outside.md"
        outside.write_text("outside content", encoding="utf-8")
        try:
            (self.out / "文献摘要整理稿.md").symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        with self.assertRaises(OSError):
            self.export()
        self.assertEqual(outside.read_text(), "outside content")
        self.assertTrue((self.out / "文献摘要整理稿.md").is_symlink())
        self.assertEqual({path.name for path in self.root.iterdir()}, {"results", "outside.md"})

    def test_directory_conflict_is_detected_before_replacing_any_file(self):
        self.export()
        before = self.files()
        target = self.out / "文献池.csv"
        target.unlink()
        target.mkdir()
        (target / "notes.txt").write_text("keep", encoding="utf-8")
        self.ctx.artifacts["extract"]["digest_md"] = "changed digest"
        with self.assertRaises(OSError):
            self.export()
        self.assertEqual((self.out / "文献摘要整理稿.md").read_bytes(), before["文献摘要整理稿.md"])
        self.assertEqual((target / "notes.txt").read_text(), "keep")
        self.assert_no_staging()

    def test_export_does_not_mutate_task_artifacts(self):
        before = copy.deepcopy(self.ctx.artifacts)
        self.export()
        self.assertEqual(self.ctx.artifacts, before)


if __name__ == "__main__":
    unittest.main()
