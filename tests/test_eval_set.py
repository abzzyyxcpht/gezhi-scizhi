"""离线评测集单测：判定纯函数 + 最小数据集冒烟（隔离 data/，参照 test_demo_runner 模式）。"""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from scripts.run_eval_set import check_expectations, run_eval_set
from src import llm, logging_util
from src.connectors import crossref, openalex
from src.orchestrator import engine

MINIMAL_TOPICS = """
topics:
  - id: smoke
    topic: "graph neural networks 图神经网络"
    keywords: "graph neural networks, GNN"
    expect:
      pool_min: 1
      anchor_min: 60
      min_rounds: 1
off_topic_control:
  id: pottery
  topic: "古陶器修复"
  keywords: "ancient pottery restoration"
  expect:
    off_topic: true
"""


class CheckExpectationsTest(unittest.TestCase):
    """期望特征判定纯函数：满足 / 不满足 / 跑题口径。"""

    EXPECT = {"pool_min": 5, "anchor_min": 60, "min_rounds": 1}

    def test_all_expectations_met(self):
        metrics = {"pool_size": 6, "anchor_rate": 100, "rounds": 1, "demo_flagged": True}
        verdict = check_expectations(metrics, self.EXPECT)
        self.assertTrue(verdict["passed"])
        self.assertEqual([c["feature"] for c in verdict["checks"]],
                         ["pool_size>=pool_min", "anchor_rate>=anchor_min", "rounds>=min_rounds"])
        self.assertTrue(all(c["passed"] for c in verdict["checks"]))

    def test_expectation_violations_fail(self):
        cases = [
            {"pool_size": 4, "anchor_rate": 100, "rounds": 1},   # 池不足
            {"pool_size": 6, "anchor_rate": 40, "rounds": 1},    # 锚定率不足
            {"pool_size": 6, "anchor_rate": 100, "rounds": 0},   # 轮次不足
        ]
        for metrics in cases:
            with self.subTest(metrics=metrics):
                self.assertFalse(check_expectations(metrics, self.EXPECT)["passed"])

    def test_off_topic_empty_pool_passes(self):
        verdict = check_expectations({"pool_size": 0, "demo_flagged": True}, {"off_topic": True})
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["checks"][0]["feature"], "off_topic_control")

    def test_off_topic_demo_flagged_pool_passes_with_note(self):
        # 口径：离线 fixtures 全部为合成样例，池非空时要求全部带离线样例标记
        verdict = check_expectations({"pool_size": 6, "demo_flagged": True}, {"off_topic": True})
        self.assertTrue(verdict["passed"])
        self.assertIn("样例", verdict["checks"][0]["note"])

    def test_off_topic_unflagged_item_fails(self):
        verdict = check_expectations({"pool_size": 6, "demo_flagged": False}, {"off_topic": True})
        self.assertFalse(verdict["passed"])


class EvalSetSmokeTest(unittest.TestCase):
    """最小评测集冒烟：不触网（连接器与 chat_json 禁调），报告结构含 eval_set 键。"""

    def test_run_eval_set_smoke_and_report_structure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            topics_path = root / "topics.yaml"
            topics_path.write_text(MINIMAL_TOPICS, encoding="utf-8")
            checkpoints = root / "checkpoints"
            checkpoints.mkdir()
            out_path = root / "eval_report.json"
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(engine, "CHECKPOINT_DIR", checkpoints))
                stack.enter_context(mock.patch.object(logging_util, "_MODEL_LOG", root / "model.jsonl"))
                stack.enter_context(mock.patch.object(logging_util, "_TOOL_LOG", root / "tool.jsonl"))
                stack.enter_context(mock.patch.object(llm, "available", return_value=True))
                # 注：test_dual_source.py 会 `del crossref.search`（历史遗留的全局污染），
                # 因此按属性是否存在做条件打桩，避免被测试顺序影响
                forbidden = []
                for module, name in ((openalex, "search"), (openalex, "fetch_many"),
                                     (openalex, "cited_by_batch"), (crossref, "search"),
                                     (crossref, "fetch_by_doi"), (llm, "chat_json")):
                    if hasattr(module, name):
                        forbidden.append(stack.enter_context(mock.patch.object(
                            module, name,
                            side_effect=AssertionError("offline eval must not call " + name))))
                report = run_eval_set(topics_path, out_path)
                self.assertTrue(all(call.call_count == 0 for call in forbidden),
                                "Offline eval reached an external connector or model")

            # eval_retrieval 原结构保留
            for key in ("generated_at", "topic", "candidates", "tau", "modes"):
                self.assertIn(key, report)
            # 新增 eval_set 键
            eval_set = report["eval_set"]
            self.assertEqual(eval_set["summary"]["total"], 2)
            self.assertEqual(eval_set["summary"]["failed"], 0)
            records = eval_set["records"]
            self.assertEqual(len(records), 2)
            for record in records:
                for key in ("topic", "status", "pool_size", "citation_count", "rounds",
                            "anchor_rate", "elapsed_ms", "funnel_tier", "passed", "checks"):
                    self.assertIn(key, record)
                self.assertEqual(record["status"], "done")
                self.assertTrue(record["passed"])
            # 普通课题与跑题对照都通过；对照课题池为空或全部带样例标记
            control = next(r for r in records if r["control"])
            self.assertTrue(control["pool_size"] == 0 or control["demo_flagged"])
            normal = next(r for r in records if not r["control"])
            self.assertGreaterEqual(normal["pool_size"], 1)
            self.assertGreaterEqual(normal["anchor_rate"], 60)
            # 落盘文件与内存报告一致
            data = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertIn("eval_set", data)
            self.assertIn("modes", data)
            self.assertEqual(data["eval_set"]["summary"], eval_set["summary"])


if __name__ == "__main__":
    unittest.main()
