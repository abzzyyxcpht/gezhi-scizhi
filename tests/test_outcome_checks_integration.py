"""Outcome checks must be reachable, read-only, and honest about the record."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import bridge, service
from src.orchestrator import engine
from src.orchestrator.engine import TaskContext


class OutcomeCheckServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patcher = mock.patch.object(engine, "CHECKPOINT_DIR", Path(self._tmp.name))
        self._patcher.start()
        self.ctx = TaskContext("outcome01", "research_pipeline", {"topic": "t"})
        self.ctx.artifacts = {
            "lit_filter": {"pool": [{"id": "W1", "title": "A", "sha256": ""}]},
            "citation": {"entries": [{"title": "A"}]},
            "digest": "摘要",
            "review_frame": {"sections": []},
        }
        service._tasks[self.ctx.task_id] = self.ctx
        self.data = tempfile.TemporaryDirectory()
        self._data_patcher = mock.patch("src.config.DATA_DIR", Path(self.data.name))
        self._data_patcher.start()

    def tearDown(self):
        self._data_patcher.stop()
        self._tmp.cleanup()
        self.data.cleanup()
        service._tasks.pop(self.ctx.task_id, None)

    def test_service_returns_a_checklist_that_is_not_all_green(self):
        report = service.outcome_checks(self.ctx.task_id)
        self.assertGreater(report["total"], 0)
        self.assertNotEqual(report["verdict"], "all_satisfied")

    def test_service_refuses_an_unknown_task(self):
        with self.assertRaises(ValueError):
            service.outcome_checks("no-such-task")

    def test_bridge_outcome_checks_returns_the_same_report(self):
        reply = bridge.handle({"cmd": "outcome_checks", "task_id": self.ctx.task_id})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["checks"]["total"],
                         service.outcome_checks(self.ctx.task_id)["total"])

    def test_bridge_outcome_checks_rejects_an_unknown_task(self):
        reply = bridge.handle({"cmd": "outcome_checks", "task_id": "nope"})
        self.assertFalse(reply["ok"])
        self.assertIn("研究任务", reply["error"])

    def test_review_record_closes_the_manual_item_only_after_being_written(self):
        before = bridge.handle({"cmd": "outcome_checks", "task_id": self.ctx.task_id})
        manual_before = next(item for item in before["checks"]["items"]
                             if item["id"] == "review:manual")
        self.assertEqual(manual_before["state"], "pending")

        reply = bridge.handle({"cmd": "outcome_review", "task_id": self.ctx.task_id,
                               "record": {"by": "张三", "note": "已核对引用与图表"}})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["review"]["by"], "张三")

        after = bridge.handle({"cmd": "outcome_checks", "task_id": self.ctx.task_id})
        manual_after = next(item for item in after["checks"]["items"]
                            if item["id"] == "review:manual")
        self.assertEqual(manual_after["state"], "satisfied")
        self.assertEqual(manual_after["evidence"][0]["by"], "张三")

    def test_review_requires_a_reviewer_and_a_note(self):
        for payload in ({"by": "", "note": "n"}, {"by": "张三", "note": ""}):
            reply = bridge.handle({"cmd": "outcome_review", "task_id": self.ctx.task_id,
                                   "record": payload})
            self.assertFalse(reply["ok"])
            self.assertTrue(reply["error"])

    def test_review_is_stored_outside_the_task_artifacts(self):
        bridge.handle({"cmd": "outcome_review", "task_id": self.ctx.task_id,
                       "record": {"by": "张三", "note": "核对完成"}})
        stored = Path(self.data.name) / "outcome_reviews.json"
        self.assertTrue(stored.is_file())
        payload = json.loads(stored.read_text(encoding="utf-8"))
        self.assertIn(self.ctx.task_id, payload)
        self.assertNotIn("outcome_review", self.ctx.artifacts)


class OutcomeReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self._patcher = mock.patch("src.config.DATA_DIR", Path(self.data.name))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.data.cleanup()

    def test_missing_task_id_is_refused(self):
        from src import outcome_review
        with self.assertRaises(ValueError):
            outcome_review.record("", "张三", "核对完成")

    def test_record_stores_a_timestamp_when_not_supplied(self):
        from src import outcome_review
        entry = outcome_review.record("t1", "张三", "核对完成")
        self.assertTrue(entry["at"])
        self.assertEqual(entry["source"], "user")

    def test_read_returns_empty_for_unknown_task(self):
        from src import outcome_review
        self.assertEqual(outcome_review.read("nope"), {})

    def test_handled_read_states_the_boundary(self):
        from src import outcome_review
        reply = outcome_review.handle({"cmd": "outcome_review", "task_id": "t1"})
        self.assertTrue(reply["ok"])
        self.assertIn("不能替代", reply["boundary"])


if __name__ == "__main__":
    unittest.main()
