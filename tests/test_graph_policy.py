# -*- coding: utf-8 -*-
"""证据图谱与权限策略单元测试：池内引用边、PNG 渲染、持久化策略生效。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

W1 = {"id": "W1", "title": "Paper One", "score": 0.55,
      "referenced_works": ["W2", "W9"]}
W2 = {"id": "W2", "title": "Paper Two", "score": 0.32, "referenced_works": []}
W3 = {"id": "W3", "title": "Paper Three", "score": 0.18,
      "referenced_works": ["W1"]}


class InternalEdgesTest(unittest.TestCase):
    def test_edges_dedup_and_undirected(self):
        from src.agents.graph import internal_edges
        edges = internal_edges([W1, W2, W3])
        self.assertIn((0, 1), edges)   # W1 引 W2
        self.assertIn((0, 2), edges)   # W3 引 W1（反向命中）
        self.assertEqual(len(edges), 2)

    def test_external_refs_ignored(self):
        from src.agents.graph import internal_edges
        self.assertEqual(internal_edges([{"id": "A", "referenced_works": ["X"]}]), [])


class BuildGraphTest(unittest.TestCase):
    def test_renders_png_and_stats(self):
        from src.agents.graph import build_evidence_graph
        with tempfile.TemporaryDirectory() as td:
            r = build_evidence_graph([W1, W2, W3], out_dir=Path(td), topic="测试")
            self.assertEqual(r["nodes"], 3)
            self.assertEqual(r["edges"], 2)
            self.assertTrue(Path(r["file"]).exists())
            self.assertGreater(Path(r["file"]).stat().st_size, 1000)
            self.assertEqual(r["items"][0]["title"], "Paper One")

    def test_empty_pool(self):
        from src.agents.graph import build_evidence_graph
        with tempfile.TemporaryDirectory() as td:
            r = build_evidence_graph([], out_dir=Path(td))
            self.assertEqual(r["nodes"], 0)
            self.assertEqual(r["edges"], 0)
            self.assertEqual(r["pool_total"], 0)

    def test_truncation_reports_pool_total(self):
        """大池只画 Top30：但元信息必须如实报出池的真实规模（图与文案不骗人）。"""
        from src.agents.graph import build_evidence_graph
        works = [{"id": f"W{i}", "title": f"Paper {i}",
                  "score": round(1 - i / 50, 4),
                  "referenced_works": [f"W{i + 1}"] if i < 49 else []}
                 for i in range(40)]
        with tempfile.TemporaryDirectory() as td:
            r = build_evidence_graph(works, out_dir=Path(td), topic="大池")
            self.assertEqual(r["nodes"], 30)        # 图示 Top 30
            self.assertEqual(r["pool_total"], 40)  # 真实池规模


class PersistedPolicyTest(unittest.TestCase):
    def test_deny_pattern_blocks_write_tools(self):
        """持久化策略（settings.tool_permissions 的形状）经构造参数生效。"""
        from src.agent_runtime import AgentRuntime
        rt = AgentRuntime(offline=True, llm_enabled=False,
                          permissions={"research.*": "deny"})
        r = rt.start("调研一个方向", "agent")
        self.assertEqual(r["status"], "blocked")
        self.assertIn("已被策略禁用", r.get("reason", ""))

    def test_allow_pattern_skips_approval(self):
        from src.agent_runtime import AgentRuntime
        rt = AgentRuntime(offline=True, llm_enabled=False,
                          permissions={"research.*": "allow"})
        r = rt.start("调研一个方向", "agent")
        self.assertEqual(r["status"], "done")  # 全程无审批卡点

    def test_settings_defaults_contain_new_keys(self):
        from src import settings as app_settings
        d = app_settings._DEFAULTS
        self.assertIn("enable_crossref", d)
        self.assertIn("tool_permissions", d)
        self.assertEqual(d["enable_crossref"], True)
        self.assertEqual(d["tool_permissions"], {})


if __name__ == "__main__":
    unittest.main()
