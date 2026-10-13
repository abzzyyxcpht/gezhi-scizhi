"""Notebook persistence, scientific traceability, and invalid-state boundaries."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from src import config


class NotebookContractTest(unittest.TestCase):
    def test_notebook_service_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("src.research_notebook"))


class NotebookTest(unittest.TestCase):
    def setUp(self):
        from src import research_notebook
        self.notebook = research_notebook
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        patch = mock.patch.object(config, "DATA_DIR", self.data_dir)
        patch.start()
        self.addCleanup(patch.stop)

    def call(self, cmd, **kwargs):
        return self.notebook.handle({"cmd": "notebook_" + cmd, **kwargs})

    def save(self, **fields):
        response = self.call("save", record={
            "title": "A reproducible test", "question": "Does A exceed B?", **fields})
        self.assertTrue(response["ok"], response)
        return response["record"]

    @staticmethod
    def evidence(**fields):
        return {"relation": "challenges", "source": {
            "id": "paper-1", "title": "A study", "doi": "10.1234/test",
            "url": "https://example.org/paper", "source_type": "abstract",
            "synthetic": False},
            "quote": "No significant improvement was observed.",
            "scope": "Small dataset; not a test of the full population.",
            "locator": "Abstract, sentence 2", **fields}

    def test_partial_update_preserves_plan_evidence_and_created_at(self):
        record = self.save(kind="experiment", prediction="A > B",
                           method="Compare seeds 1–5", metrics="Mean accuracy",
                           evidence=[self.evidence()], next_action="Run baseline")
        changed = self.call("save", record={"id": record["id"], "status": "running"})
        self.assertTrue(changed["ok"], changed)
        updated = changed["record"]
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["created_at"], record["created_at"])
        for field in ("method", "metrics", "evidence", "next_action"):
            self.assertEqual(updated[field], record[field])
        self.assertEqual(self.call("get", id=record["id"])["record"], updated)
        self.assertEqual(self.call("list")["records"], [updated])
        self.assertEqual(updated["provenance"]["scientific_verification"], "not_performed")

    def test_completed_requires_result_and_preserves_failure_observations(self):
        record = self.save(kind="experiment", method="Run baseline", metrics="Accuracy")
        denied = self.call("save", record={"id": record["id"], "status": "completed"})
        self.assertFalse(denied["ok"])
        self.assertEqual(self.call("get", id=record["id"])["record"]["status"], "planned")
        failed = self.call("save", record={"id": record["id"], "status": "failed",
                            "observations": ["Run terminated before producing metrics."],
                            "limitations": "Insufficient device memory", "next_action": "Reduce batch size"})
        self.assertTrue(failed["ok"], failed)
        completed = self.call("save", record={"id": record["id"], "status": "completed",
                                             "result": "Mean accuracy: A=0.70, B=0.72 across 5 seeds."})
        self.assertTrue(completed["ok"], completed)
        self.assertEqual(completed["record"]["observations"], failed["record"]["observations"])

    def test_evidence_is_user_asserted_and_synthetic_remains_explicit(self):
        entry = self.evidence(relation="supports")
        entry["source"]["source_type"] = "synthetic_demo"
        record = self.save(evidence=[entry])
        self.assertTrue(record["evidence"][0]["source"]["synthetic"])
        self.assertEqual(record["provenance"]["evidence_relations"], "user_asserted")
        self.assertTrue(record["provenance"]["contains_synthetic_evidence"])
        self.assertFalse(record["synthetic"])

    def test_invalid_evidence_does_not_create_a_record(self):
        for field, value in (("relation", "verified"), ("quote", ""), ("scope", ""),
                             ("source", {"title": "Title alone is not stable identity"}),
                             ("source", {"id": "x", "url": "file:///private/file"}),
                             ("source", {"id": "x", "url": "https://user:secret@example.org"}),
                             ("source", {"id": "x", "doi": "not-a-doi"}),
                             ("source", {"id": "x", "api_key": "secret-value"})):
            with self.subTest(field=field, value=value):
                response = self.call("save", record={"title": "Test", "question": "Why?",
                                                     "evidence": [self.evidence(**{field: value})]})
                self.assertFalse(response["ok"], response)
        self.assertEqual(self.call("list")["records"], [])

    def test_related_ids_resolve_and_referenced_records_cannot_be_deleted(self):
        hypothesis = self.save()
        missing = self.call("save", record={"title": "Test", "question": "Why?",
                                           "related_ids": ["missing"]})
        self.assertFalse(missing["ok"])
        experiment = self.save(kind="experiment", related_ids=[hypothesis["id"]])
        self.assertFalse(self.call("delete", id=hypothesis["id"])["ok"])
        self.assertFalse(self.call("save", record={"id": experiment["id"],
                                                  "related_ids": [experiment["id"]]})["ok"])
        self.assertTrue(self.call("delete", id=experiment["id"])["ok"])
        self.assertTrue(self.call("delete", id=hypothesis["id"])["ok"])
        self.assertFalse(self.call("get", id=hypothesis["id"])["ok"])

    def test_filters_and_invalid_requests(self):
        self.save(kind="hypothesis", task_id="task_one")
        expected = self.save(kind="experiment", task_id="task_two", status="running")
        filtered = self.call("list", kind="experiment", task_id="task_two", status="running")
        self.assertEqual(filtered["records"], [expected])
        for request in (None, [], {"cmd": []}, {"cmd": "unsupported"}, {"cmd": "notebook_save", "record": []},
                        {"cmd": "notebook_get", "id": "../../settings"},
                        {"cmd": "notebook_list", "status": "verified"},
                        {"cmd": "notebook_save", "record": {"title": "No question"}}):
            with self.subTest(request=request):
                self.assertFalse(self.notebook.handle(request)["ok"])

    def test_schema_templates_can_be_saved_after_required_fields_are_filled(self):
        result = self.call("schema")
        self.assertTrue(result["ok"], result)
        self.assertEqual(set(result["templates"]), {"hypothesis", "experiment"})
        self.assertEqual(result["schema"]["properties"]["status"]["enum"],
                         ["planned", "running", "completed", "failed"])
        for kind, template in result["templates"].items():
            payload = {**template, "title": kind, "question": "What would falsify this?"}
            saved = self.call("save", record=payload)
            self.assertTrue(saved["ok"], saved)

    def test_exports_are_fixed_location_include_provenance_and_escape_markdown(self):
        record = self.save(title="<script>alert(1)</script>", evidence=[self.evidence()],
                           synthetic=True, result="Simulation produced accuracy=0.72",
                           status="completed", observations=["Simulator seed 7"],
                           limitations="Simulation only", next_action="Collect independent measurements")
        for fmt, suffix in (("json", ".json"), ("markdown", ".md")):
            exported = self.call("export", id=record["id"], format=fmt)
            self.assertTrue(exported["ok"], exported)
            path = Path(exported["path"])
            self.assertTrue(path.resolve().is_relative_to((self.data_dir / "exports").resolve()))
            self.assertEqual(path.suffix, suffix)
            text = path.read_text(encoding="utf-8")
            self.assertIn(record["updated_at"], text)
            self.assertIn(record["evidence"][0]["quote"], text)
            self.assertIn("not_performed", text)
            self.assertIn("synthetic", text)
            if fmt == "json":
                self.assertEqual(json.loads(text)["record"], record)
            else:
                self.assertNotIn("<script>", text)
        self.assertFalse(self.call("export", id=record["id"], format="html")["ok"])
        self.assertFalse(self.call("export", id=record["id"], format="json", path="../escape")["ok"])

    def test_concurrent_partial_updates_do_not_drop_independent_fields(self):
        record = self.save()
        updates = {"prediction": "A exceeds B", "method": "Fixed seeds", "metrics": "Accuracy",
                   "result": "Pilot output", "limitations": "Pilot only", "next_action": "Replicate"}
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda item: self.call("save", record={"id": record["id"], item[0]: item[1]}),
                                    updates.items()))
        self.assertTrue(all(r["ok"] for r in results), results)
        final = self.call("get", id=record["id"])["record"]
        for key, value in updates.items():
            self.assertEqual(final[key], value)
        self.assertEqual(final["version"], 7)

    def test_fresh_process_reads_persistent_records(self):
        record = self.save(title="Persistent question")
        script = ("import json; from src.research_notebook import handle; "
                  "print(json.dumps(handle({'cmd':'notebook_list'})))")
        process = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parents[1],
                                 env={**os.environ, "GEZHI_DATA_DIR": str(self.data_dir)},
                                 capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        response = json.loads(process.stdout)
        self.assertEqual(response["records"][0]["id"], record["id"])

    def test_cross_process_partial_updates_are_serialized(self):
        record = self.save()
        updates = {"prediction": "A > B", "method": "seed=1", "metrics": "accuracy", "result": "pilot=0.5"}
        script = ("import json,sys; from src.research_notebook import handle; "
                  "print(json.dumps(handle(json.loads(sys.argv[1]))))")
        processes = [subprocess.Popen(
            [sys.executable, "-c", script, json.dumps({"cmd": "notebook_save", "record": {
                "id": record["id"], key: value}})], cwd=Path(__file__).parents[1],
            env={**os.environ, "GEZHI_DATA_DIR": str(self.data_dir)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            for key, value in updates.items()]
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertTrue(json.loads(stdout)["ok"], stdout)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        saved = self.call("get", id=record["id"])["record"]
        self.assertEqual(saved["version"], 5)
        for key, value in updates.items():
            self.assertEqual(saved[key], value)

    def test_corrupt_store_is_reported_without_overwriting_existing_file(self):
        self.save()
        path = self.data_dir / "research_notebook.json"
        for broken in ('{"records":', '{"schema_version":"future","records":{}}'):
            with self.subTest(broken=broken):
                path.write_text(broken, encoding="utf-8")
                response = self.call("save", record={"title": "Test", "question": "Why?"})
                self.assertFalse(response["ok"], response)
                self.assertEqual(response["code"], "corrupt_store")
                self.assertEqual(path.read_text(encoding="utf-8"), broken)

    def test_readonly_provenance_cannot_be_forged_and_stale_version_is_rejected(self):
        record = self.save(provenance={"scientific_verification": "verified", "api_key": "private"},
                           version=99, updated_at="not a real timestamp")
        self.assertEqual(record["version"], 1)
        self.assertNotIn("private", json.dumps(record))
        self.assertEqual(record["provenance"]["scientific_verification"], "not_performed")
        updated = self.call("save", record={"id": record["id"], "prediction": "A > B"}, expected_version=1)
        self.assertTrue(updated["ok"], updated)
        stale = self.call("save", record={"id": record["id"], "prediction": "stale text"}, expected_version=1)
        self.assertEqual(stale["code"], "version_conflict")
        self.assertEqual(self.call("get", id=record["id"])["record"]["prediction"], "A > B")

    def test_only_trusted_actor_argument_marks_agent_records(self):
        payload = {"cmd": "notebook_save", "record": {"title": "Agent draft", "question": "Why?",
                   "provenance": {"origin": "user_recorded", "scientific_verification": "verified"}}}
        saved = self.notebook.handle(payload, actor="agent")
        self.assertTrue(saved["ok"], saved)
        record = self.call("get", id=saved["record"]["id"])["record"]
        self.assertEqual(record["provenance"]["origin"], "agent_recorded")
        self.assertEqual(record["provenance"]["scientific_verification"], "not_performed")
        spoofed = self.notebook.handle({**payload, "actor": "agent"})
        self.assertEqual(spoofed["record"]["provenance"]["origin"], "user_recorded")
        edited = self.call("save", record={"id": record["id"], "next_action": "User edit"})
        self.assertEqual(edited["record"]["provenance"]["origin"], "user_recorded")


if __name__ == "__main__":
    unittest.main()
