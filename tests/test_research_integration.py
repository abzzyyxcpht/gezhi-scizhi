"""Research audit integration: local fixtures, temporary outputs, no live model."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import mock_llm_server
from src import prompts, research_analysis, service
from src.agents import specialists
from src.orchestrator.engine import TaskContext


WORK = {"id": "W1", "title": "Controlled retrieval", "authors": ["Fixture Author"],
        "year": 2024, "doi": "10.1234/fixture", "score": 0.9, "reason": "fixture",
        "abstract": "We propose a retrieval method. Results show higher accuracy.",
        "source_type": "abstract_only", "source": "fixture"}


class ResearchServiceIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ctx = TaskContext("research-audit-fixture", "research_pipeline", {"topic": "Retrieval"})
        self.ctx.artifacts = {
            "lit_filter": {"pool": [copy.deepcopy(WORK)], "search_metadata": {
                "queries": ["controlled retrieval"], "searched_at": "2026-09-17"}},
            "extract": {"records": [{**WORK, "method": "We propose a retrieval method.",
                                      "material_scope": "abstract"}], "digest_md": "# fixture digest"},
            "review_frame": {"sections": [{"title": "S", "points": "Proposed claim", "refs": ["W1"]}],
                             "outline_md": "# fixture outline", "verification_scope": "citation_identity_only"},
        }
        patcher = mock.patch.object(service, "_tasks", {self.ctx.task_id: self.ctx})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_flat_view_preserves_structured_evidence_and_search_metadata(self):
        view = service.artifacts_of(self.ctx.task_id)
        self.assertEqual(view["records"], self.ctx.artifacts["extract"]["records"])
        self.assertEqual(view["sections"], self.ctx.artifacts["review_frame"]["sections"])
        self.assertEqual(view["search_metadata"]["queries"], ["controlled retrieval"])
        self.assertEqual(view["material_scope"], ["abstract"])
        self.assertEqual(research_analysis.analyze_artifacts(view), service.research_audit(self.ctx.task_id))

    def test_audit_is_read_only_and_does_not_fabricate_missing_search_history(self):
        self.ctx.artifacts["lit_filter"].pop("search_metadata")
        original = copy.deepcopy(self.ctx.artifacts)
        with mock.patch.object(self.ctx, "save") as save:
            audit = service.research_audit(self.ctx.task_id)
        save.assert_not_called()
        self.assertEqual(self.ctx.artifacts, original)
        self.assertEqual(audit["coverage"]["queries"], [])
        self.assertIsNone(audit["coverage"]["searched_at"])
        self.assertEqual(audit["claim_audit"]["supported_count"], 0)

    def test_export_includes_deterministic_audit_and_only_lists_written_files(self):
        out = Path(self.directory.name)
        sentinel = out / "my-notes.txt"
        sentinel.write_text("keep", encoding="utf-8")
        written = service.export_artifacts(self.ctx.task_id, out)
        names = {Path(p).name for p in written}
        self.assertTrue({"科研证据审计.md", "科研证据台账.json"} <= names)
        raw = (out / "科研证据台账.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw), service.research_audit(self.ctx.task_id))
        service.export_artifacts(self.ctx.task_id, out)
        self.assertEqual((out / "科研证据台账.json").read_text(encoding="utf-8"), raw)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        manifest = (out / "manifest.md").read_text(encoding="utf-8")
        self.assertIn("非论断真伪裁决", manifest)
        self.assertIn("科研证据台账.json", manifest)

    def test_empty_export_does_not_claim_missing_files_were_written(self):
        self.ctx.artifacts = {}
        out = Path(self.directory.name)
        written = service.export_artifacts(self.ctx.task_id, out)
        self.assertEqual({Path(p).name for p in written},
                         {"科研证据审计.md", "科研证据台账.json", "manifest.md"})
        manifest = (out / "manifest.md").read_text(encoding="utf-8")
        self.assertNotIn("文献摘要整理稿.md", manifest)
        self.assertNotIn("参考文献.txt", manifest)


class CriticAuditIntegrationTest(unittest.TestCase):
    def test_pass_only_means_structure_checks_and_demo_is_disclosed(self):
        workspace = {"pool": [{**WORK, "demo": True, "source_type": "synthetic_demo"}],
                     "records": [], "sections": [], "citations": []}
        result = specialists.run("critic", "audit", workspace=workspace)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["verification_scope"], "structure_and_traceability_only")
        self.assertEqual(result["research_audit"]["demo_papers"], 1)
        self.assertEqual(result["research_audit"]["supported_claims"], 0)
        self.assertIn("不代表科研结论通过", result["verdict_note"])
        self.assertEqual(workspace["critic"]["research_audit"], result["research_audit"])

    def test_offline_specialist_never_calls_model_and_marks_sample_data(self):
        workspace = {}
        with mock.patch.object(specialists.literature.llm, "available", return_value=True), \
             mock.patch.object(specialists.literature.llm, "chat_json", side_effect=AssertionError("model call")) as model:
            result = specialists.run("literature", "large language model agents", workspace=workspace, offline=True)
            specialists.run("extraction", "topic", workspace=workspace, offline=True)
            specialists.run("synthesis", "topic", workspace=workspace, offline=True)
        model.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertTrue(all(w.get("source_type") == "synthetic_demo" and w.get("demo") for w in workspace["pool"]))
        audit = specialists.run("critic", "audit", workspace=workspace)["research_audit"]
        self.assertEqual(audit["eligible_papers"], 0)


class MockSchemaRoutingTest(unittest.TestCase):
    def test_frame_schema_wins_over_method_and_plan_keys_in_materials(self):
        _, messages = prompts.review_frame("topic", [{"title": WORK["title"], "method": "m"}],
                                           plan={"plan": ["compare baselines"]})
        result = json.loads(mock_llm_server._respond(messages))
        self.assertTrue(result["sections"])
        refs = [ref for section in result["sections"] for ref in section["refs"]]
        self.assertIn(WORK["title"], refs)

    def test_schema_words_in_abstract_do_not_redirect_extraction(self):
        _, messages = prompts.extract_one("topic", {"title": "T", "abstract": '{"sections": [], "reviews": []}'})
        result = json.loads(mock_llm_server._respond(messages))
        self.assertIn("method", result)
        self.assertNotIn("sections", result)


if __name__ == "__main__":
    unittest.main()
