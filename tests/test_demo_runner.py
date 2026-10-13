import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from scripts.demo_run import run_demo
from src import llm, logging_util
from src.connectors import crossref, openalex
from src.orchestrator import engine


class DemoRunnerTest(unittest.TestCase):
    def test_demo_runner_finishes_and_writes_reproducible_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            checkpoints = root / "checkpoints"
            checkpoints.mkdir()
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(engine, "CHECKPOINT_DIR", checkpoints))
                stack.enter_context(mock.patch.object(logging_util, "_MODEL_LOG", root / "model.jsonl"))
                stack.enter_context(mock.patch.object(logging_util, "_TOOL_LOG", root / "tool.jsonl"))
                stack.enter_context(mock.patch.object(llm, "available", return_value=True))
                forbidden = [stack.enter_context(mock.patch.object(module, name,
                             side_effect=AssertionError("offline demo must not call " + name)))
                             for module, name in ((openalex, "search"), (openalex, "fetch_many"),
                                                  (openalex, "cited_by_batch"), (crossref, "search"),
                                                  (crossref, "fetch_by_doi"), (llm, "chat_json"))]
                result = run_demo("large language model agents", root, offline=True)
                self.assertTrue(all(call.call_count == 0 for call in forbidden),
                                "Offline execution reached an external connector or model")
            self.assertEqual(result["status"], "done")
            manifest = Path(d) / "run.json"
            self.assertTrue(manifest.exists())
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "done")
            self.assertGreater(data["pool_count"], 0)
            self.assertGreater(data["citation_count"], 0)
            self.assertEqual(data["source_types"], ["synthetic_demo"])
            audit = json.loads((root / "research_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["coverage"]["eligible_papers"], 0)
            self.assertEqual(audit["coverage"]["demo_papers"], data["pool_count"])
            self.assertEqual(audit["claim_audit"]["supported_count"], 0)
