# -*- coding: utf-8 -*-
"""AIGC 标识与用量统计单元测试：标识区、导出头、manifest、usage_summary 聚合。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src import aigc


class AigcMarkTest(unittest.TestCase):
    def test_footer_contains_mark_and_provenance(self):
        f = aigc.footer_md(llm_used=True, task_id="abc123", topic="大模型智能体")
        self.assertIn(aigc.MARK, f)
        self.assertIn("LLM 语义抽取", f)
        self.assertIn("abc123", f)
        self.assertIn("大模型智能体", f)
        self.assertIn("提示词版本", f)
        self.assertIn("人工复核", f)

    def test_footer_rule_mode(self):
        f = aigc.footer_md(llm_used=False)
        self.assertIn("确定性规则引擎", f)

    def test_export_header(self):
        h = aigc.export_header(kind="参考文献排版", llm_used=False, task_id="t1")
        self.assertIn(aigc.MARK, h)
        self.assertIn("参考文献排版", h)

    def test_manifest_lists_ai_participation(self):
        text = aigc.manifest_text(
            [{"file": "a.md", "kind": "摘要", "ai": True, "note": "n1"},
             {"file": "b.txt", "kind": "引用排版", "ai": False, "note": "n2"}],
            task_id="tid", topic="RAG")
        self.assertIn("| a.md | 摘要 | 是 | n1 |", text)
        self.assertIn("| b.txt | 引用排版 | 否（确定性排版） | n2 |", text)
        self.assertIn("tid", text)


class UsageSummaryTest(unittest.TestCase):
    def test_aggregates_model_and_tool_logs(self):
        import src.logging_util as lu
        with tempfile.TemporaryDirectory() as td:
            mlog = Path(td) / "model_calls.jsonl"
            tlog = Path(td) / "tool_calls.jsonl"
            mlog.write_text("\n".join(json.dumps(r) for r in [
                {"trace_id": "1", "node": "score", "model_route": "remote(m)",
                 "prompt_tokens": 100, "completion_tokens": 50,
                 "latency_ms": 800, "status": "success"},
                {"trace_id": "2", "node": "extract", "model_route": "remote(m)",
                 "prompt_tokens": 200, "completion_tokens": 100,
                 "latency_ms": 1200, "status": "success"},
                {"trace_id": "3", "node": "score", "model_route": "remote(m)",
                 "prompt_tokens": 10, "completion_tokens": 0,
                 "latency_ms": 100, "status": "failed"},
            ]) + "\n", encoding="utf-8")
            tlog.write_text(json.dumps(
                {"node": "search", "tool": "openalex", "latency_ms": 300,
                 "status": "success"}) + "\n", encoding="utf-8")
            old_m, old_t = lu._MODEL_LOG, lu._TOOL_LOG
            lu._MODEL_LOG, lu._TOOL_LOG = mlog, tlog
            try:
                u = lu.usage_summary()
                text = lu.format_usage_text()
            finally:
                lu._MODEL_LOG, lu._TOOL_LOG = old_m, old_t
        self.assertEqual(u["model"]["calls"], 3)
        self.assertEqual(u["model"]["prompt_tokens"], 310)
        self.assertEqual(u["model"]["completion_tokens"], 150)
        self.assertEqual(u["model"]["total_tokens"], 460)
        self.assertEqual(u["model"]["failures"], 1)
        self.assertEqual(u["model"]["by_node"]["score"]["calls"], 2)
        self.assertEqual(u["tool"]["calls"], 1)
        self.assertIn("460", text)
        self.assertIn("工具调用：1 次", text)


if __name__ == "__main__":
    unittest.main()
