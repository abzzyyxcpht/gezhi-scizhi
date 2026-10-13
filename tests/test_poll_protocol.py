# -*- coding: utf-8 -*-
"""轮询增量协议单元测试：artifacts_rev 语义与 poll 的重字段按需下发。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import service
from src.bridge import handle
from src.orchestrator.engine import TaskContext


class ArtifactsRevTest(unittest.TestCase):
    def setUp(self):
        self.ctx = TaskContext("revtest01", "research_pipeline", {"topic": "t"})
        self.ctx.artifacts = {"lit_filter": {"pool": [{"id": "W1"}, {"id": "W2"}],
                                             "rounds": [{"round": 1}]}}

    def test_stable_when_unchanged(self):
        a = service.artifacts_rev(self.ctx)
        b = service.artifacts_rev(self.ctx)
        self.assertEqual(a, b)

    def test_changes_on_pool_size(self):
        a = service.artifacts_rev(self.ctx)
        self.ctx.artifacts["lit_filter"]["pool"].append({"id": "W3"})
        self.assertNotEqual(a, service.artifacts_rev(self.ctx))

    def test_changes_on_node_completion(self):
        a = service.artifacts_rev(self.ctx)
        self.ctx.artifacts["extract"] = {"digest_md": "# 摘要", "records": [1, 2]}
        self.assertNotEqual(a, service.artifacts_rev(self.ctx))

    def test_changes_on_drop_intervention(self):
        a = service.artifacts_rev(self.ctx)
        self.ctx.artifacts["lit_filter"]["pool"] = [{"id": "W1"}]  # 人工剔除一篇
        self.assertNotEqual(a, service.artifacts_rev(self.ctx))


class PollProtocolTest(unittest.TestCase):
    HEAVY = ("pool", "digest_md", "outline_md", "citations")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch("src.orchestrator.engine.CHECKPOINT_DIR",
                                 Path(self._tmp.name))
        self._patch.start()
        self.ctx = TaskContext("polltest01", "research_pipeline", {"topic": "轮询"})
        # 贴近真实规模：多篇文献（每篇带摘要）+ 长摘要稿，才能体现增量下发的价值
        self.ctx.artifacts = {
            "lit_filter": {
                "pool": [{"id": f"W{i}", "title": f"Paper {i}",
                          "abstract": f"abstract {i} " + "词" * 600,
                          "score": 0.4} for i in range(5)],
                "rounds": [{"round": 1}, {"round": 2}],
            },
            "extract": {"digest_md": "# 摘要整理稿\n" + "内容" * 1500,
                        "records": [1, 2, 3, 4, 5]},
            "review_frame": {"outline_md": "# 综述框架\n" + "要点" * 400},
            "citation": {"entries": [{"text": f"[{i}] 参考文献条目" * 8}
                                     for i in range(5)]},
        }
        service._tasks[self.ctx.task_id] = self.ctx

    def tearDown(self):
        service._tasks.pop(self.ctx.task_id, None)
        self._patch.stop()
        self._tmp.cleanup()

    def _poll(self, **kw):
        return handle({"cmd": "poll", "task_id": self.ctx.task_id, "since": 0, **kw})

    def test_first_poll_carries_everything(self):
        r = self._poll()
        self.assertTrue(r["ok"])
        self.assertIn("art_rev", r)
        for field in self.HEAVY:
            self.assertIn(field, r)

    def test_steady_state_omits_heavy_fields(self):
        rev = self._poll()["art_rev"]
        r = self._poll(art_rev=rev)
        for field in self.HEAVY:
            self.assertNotIn(field, r, f"稳态不该回传 {field}")
        # 轻字段仍在（状态/进度/事件）
        for field in ("status", "status_text", "pointer", "done_nodes", "events"):
            self.assertIn(field, r)

    def test_rev_change_triggers_resend(self):
        rev = self._poll()["art_rev"]
        self.ctx.artifacts["lit_filter"]["pool"].append({"id": "W9"})
        r = self._poll(art_rev=rev)
        self.assertIn("pool", r)
        self.assertEqual(len(r["pool"]), 6)

    def test_full_flag_forces_heavy_fields(self):
        rev = self._poll()["art_rev"]
        r = self._poll(art_rev=rev, full=True)
        for field in self.HEAVY:
            self.assertIn(field, r)

    def test_payload_size_drop(self):
        import json
        full = len(json.dumps(self._poll(), ensure_ascii=False).encode())
        steady = len(json.dumps(self._poll(art_rev=self._poll()["art_rev"]),
                                ensure_ascii=False).encode())
        self.assertLess(steady * 10, full, f"稳态 {steady}B 应远小于全量 {full}B")


if __name__ == "__main__":
    unittest.main()
