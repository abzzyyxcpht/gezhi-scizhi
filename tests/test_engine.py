# -*- coding: utf-8 -*-
"""引擎单元测试：工作流解析、Checkpoint 往返、HITL/暂停/取消流转、人工干预版本化。

用打桩动作跑真实 research_pipeline.yaml（离线、零网络、零 LLM）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.orchestrator import engine
from src.orchestrator.engine import PauseRequested, TaskContext, load_workflow

_REAL_ACTIONS = dict(engine.ACTIONS)


def _fake(node: str, payload: dict | None = None):
    def _fn(ctx: TaskContext, params: dict) -> dict:
        ctx.emit("node", node=node, message=f"打桩节点 {node}")
        return payload if payload is not None else {"ok": True}
    return _fn


@engine.action("literature_recursive_filter")
def _fake_lit(ctx, params):
    return _fake("lit_filter", {"pool": [{"id": "W1", "title": "T", "score": 0.9}]})(
        ctx, params)


@engine.action("extract_pool")
def _fake_extract(ctx, params):
    return _fake("extract", {"records": [], "digest_md": "# 摘要"})(ctx, params)


@engine.action("build_review_frame")
def _fake_frame(ctx, params):
    return _fake("review_frame", {"sections": [], "outline_md": "# 框架"})(ctx, params)


@engine.action("format_references")
def _fake_cite(ctx, params):
    return _fake("citation", {"entries": []})(ctx, params)


# 模块导入阶段的装饰器会把真实动作临时覆盖；测试类内再注入，避免污染
# 其它测试（尤其是端到端演示）使用的全局动作注册表。
_FAKE_ACTIONS = {
    "literature_recursive_filter": _fake_lit,
    "extract_pool": _fake_extract,
    "build_review_frame": _fake_frame,
    "format_references": _fake_cite,
}
engine.ACTIONS.clear()
engine.ACTIONS.update(_REAL_ACTIONS)


class WorkflowDefTest(unittest.TestCase):
    def test_research_pipeline_structure(self):
        wf = load_workflow("research_pipeline")
        ids = [n["id"] for n in wf["nodes"]]
        self.assertEqual(ids, ["lit_filter", "human_review", "extract",
                               "review_frame", "citation"])
        self.assertEqual(wf["nodes"][1]["type"], "human_approval")
        self.assertEqual(wf["checkpoint"]["granularity"], "node")

    def test_node_ids_helper(self):
        self.assertEqual(len(engine.workflow_node_ids("research_pipeline")), 5)


class RunFlowTest(unittest.TestCase):
    """用临时 Checkpoint 目录跑真实 DAG（打桩动作），验证流转语义。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patcher = mock.patch.object(engine, "CHECKPOINT_DIR",
                                          Path(self._tmp.name))
        self._patcher.start()
        self._actions = mock.patch.dict(engine.ACTIONS, _FAKE_ACTIONS)
        self._actions.start()

    def tearDown(self):
        self._actions.stop()
        self._patcher.stop()
        self._tmp.cleanup()

    def _new_ctx(self) -> TaskContext:
        return TaskContext("unittest01", "research_pipeline", {"topic": "t"})

    def test_hitl_pause_and_resume_to_done(self):
        ctx = self._new_ctx()
        engine.run(ctx)
        self.assertEqual(ctx.status, "waiting_human")
        self.assertEqual(ctx.pointer, "human_review")

        # 模拟 service.resume：人工确认 → 指针推进 → 续跑
        nodes = {n["id"]: n for n in load_workflow(ctx.workflow)["nodes"]}
        ctx.pointer = nodes[ctx.pointer].get("next")
        ctx.clear_pause()
        engine.run(ctx)
        self.assertEqual(ctx.status, "done")
        self.assertTrue({"lit_filter", "extract", "review_frame", "citation"}
                        <= set(ctx.artifacts))
        # 节点耗时应被记录（UI 步骤条用来标出"哪一步慢"）
        self.assertIn("lit_filter", ctx.node_durations)
        self.assertGreaterEqual(ctx.node_durations["lit_filter"], 0.0)

    def test_request_pause_before_run(self):
        ctx = self._new_ctx()
        ctx.request_pause()
        engine.run(ctx)
        self.assertEqual(ctx.status, "paused")
        # 恢复后续跑到 HITL
        ctx.clear_pause()
        engine.run(ctx)
        self.assertEqual(ctx.status, "waiting_human")

    def test_cancel_takes_priority(self):
        ctx = self._new_ctx()
        ctx.request_cancel()
        engine.run(ctx)
        self.assertEqual(ctx.status, "cancelled")

    def test_node_failure_marks_failed(self):
        ctx = self._new_ctx()
        with mock.patch.dict(engine.ACTIONS, {"literature_recursive_filter":
                                              mock.Mock(side_effect=RuntimeError("炸了"))}):
            engine.run(ctx)
        self.assertEqual(ctx.status, "failed")
        kinds = [e["kind"] for e in ctx.events]
        self.assertIn("error", kinds)

    def test_internal_pause_from_node(self):
        def boom(ctx, params):
            raise PauseRequested()
        ctx = self._new_ctx()
        with mock.patch.dict(engine.ACTIONS, {"literature_recursive_filter": boom}):
            engine.run(ctx)
        self.assertEqual(ctx.status, "paused")
        self.assertEqual(ctx.pointer, "lit_filter")  # 指针保持，续跑重入该节点


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patcher = mock.patch.object(engine, "CHECKPOINT_DIR",
                                          Path(self._tmp.name))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def test_save_load_roundtrip(self):
        ctx = TaskContext("roundtrip1", "research_pipeline",
                          {"topic": "测试方向", "cite_style": "apa"})
        ctx.artifacts = {"lit_filter": {"pool": [{"id": "W1"}]}}
        ctx.node_durations = {"lit_filter": 12.5}
        ctx.status = "waiting_human"
        ctx.pointer = "human_review"
        ctx.save()

        loaded = TaskContext.load("roundtrip1")
        self.assertEqual(loaded.node_durations, {"lit_filter": 12.5})
        self.assertEqual(loaded.params["topic"], "测试方向")
        self.assertEqual(loaded.status, "waiting_human")
        self.assertEqual(loaded.artifacts["lit_filter"]["pool"][0]["id"], "W1")

    def test_load_tolerates_legacy_checkpoint_without_durations(self):
        """旧存档没有 node_durations 字段，读档不能炸（向后兼容）。"""
        import json as _json
        from src.orchestrator.engine import CHECKPOINT_DIR as _dir
        ctx = TaskContext("legacy01", "research_pipeline", {"topic": "t"})
        ctx.save()
        raw = _json.loads((_dir / "legacy01.json").read_text(encoding="utf-8"))
        raw.pop("node_durations", None)          # 模拟旧版本存档
        (_dir / "legacy01.json").write_text(_json.dumps(raw, ensure_ascii=False),
                                            encoding="utf-8")
        loaded = TaskContext.load("legacy01")
        self.assertEqual(loaded.node_durations, {})

    def test_load_backfills_missing_created_at_from_mtime(self):
        """旧存档缺 created_at：用存档文件修改时间兜底，任务列表/时间账不再悬空。"""
        import json as _json
        import re
        import time as _time
        from src.orchestrator.engine import CHECKPOINT_DIR as _dir
        ctx = TaskContext("legacy02", "research_pipeline", {"topic": "t"})
        ctx.save()
        raw = _json.loads((_dir / "legacy02.json").read_text(encoding="utf-8"))
        raw.pop("created_at", None)             # 模拟更早版本的存档
        p = _dir / "legacy02.json"
        p.write_text(_json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        loaded = TaskContext.load("legacy02")
        self.assertRegex(loaded.created_at, re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"))
        self.assertEqual(loaded.created_at,
                         _time.strftime("%Y-%m-%d %H:%M",
                                        _time.localtime(p.stat().st_mtime)))

    def test_intervene_versions_history(self):
        ctx = TaskContext("intervene1", "research_pipeline", {"topic": "t"})
        ctx.artifacts = {"lit_filter": {"pool": [{"id": "W1"}, {"id": "W2"}]}}
        engine.intervene(ctx, "lit_filter", {"pool": [{"id": "W2"}]})
        history = ctx.artifacts["_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["node"], "lit_filter")
        self.assertEqual(len(history[0]["before"]["pool"]), 2)  # 修改前快照
        self.assertEqual(ctx.artifacts["lit_filter"]["pool"][0]["id"], "W2")


if __name__ == "__main__":
    unittest.main()
