# -*- coding: utf-8 -*-
"""DAG 引擎新增能力测试：并行扇出/汇合、condition 节点真实条件求值。

打桩 load_workflow 直接注入 dict（零文件、零网络），打桩 CHECKPOINT_DIR 隔离存档，
与 tests/test_engine.py 的隔离模式一致。
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src.orchestrator import engine
from src.orchestrator.engine import PauseRequested, TaskContext

# A 扇出 B/C 两条分支，then:D 汇合；B/C 各自 next: None（分支独立结束）
FANOUT_WF = {
    "name": "fanout_demo",
    "nodes": [
        {"id": "A", "type": "tool", "action": "act_a", "next": ["B", "C"],
         "then": "D"},
        {"id": "B", "type": "tool", "action": "act_b", "next": None},
        {"id": "C", "type": "tool", "action": "act_c", "next": None},
        {"id": "D", "type": "tool", "action": "act_d", "next": None},
    ],
}

# next 单元素列表应等同字符串串行
SINGLE_LIST_WF = {
    "name": "single_list_demo",
    "nodes": [
        {"id": "A", "type": "tool", "action": "act_a", "next": ["B"]},
        {"id": "B", "type": "tool", "action": "act_b", "next": None},
    ],
}

# src 产物经 condition 选边：on_true -> yes，on_false -> no
COND_WF = {
    "name": "cond_demo",
    "nodes": [
        {"id": "src", "type": "tool", "action": "act_src", "next": "check"},
        {"id": "check", "type": "condition", "if": "src.pool",
         "on_true": "yes", "on_false": "no"},
        {"id": "yes", "type": "tool", "action": "act_yes", "next": None},
        {"id": "no", "type": "tool", "action": "act_no", "next": None},
    ],
}


class _FanoutState:
    """扇出测试的共享计数/顺序记录器（分支线程安全）。"""

    def __init__(self):
        self.calls: dict[str, int] = {}
        self.order: list[str] = []
        self.b_done = threading.Event()
        self.lock = threading.Lock()

    def record(self, nid: str) -> None:
        with self.lock:
            self.calls[nid] = self.calls.get(nid, 0) + 1
            self.order.append(nid)


def _make_actions(state: _FanoutState, pause_in_c: bool = False) -> dict:
    """构造扇出工作流的打桩动作；pause_in_c 时 C 首次执行抛 PauseRequested。"""

    def act_a(ctx, params):
        state.record("A")
        return {"ok": "a"}

    def act_b(ctx, params):
        state.record("B")
        state.b_done.set()
        return {"ok": "b"}

    def act_c(ctx, params):
        state.record("C")
        if not state.b_done.wait(timeout=5):
            raise RuntimeError("act_b 未完成，测试编排错误")
        if pause_in_c and state.calls["C"] == 1:
            raise PauseRequested()
        return {"ok": "c"}

    def act_d(ctx, params):
        # 汇合点读取两条分支的产物：读不到说明汇合顺序不对（会 KeyError 失败）
        state.record("D")
        return {"from_b": ctx.artifacts["B"]["ok"],
                "from_c": ctx.artifacts["C"]["ok"]}

    return {"act_a": act_a, "act_b": act_b, "act_c": act_c, "act_d": act_d}


class FanoutTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patcher = mock.patch.object(engine, "CHECKPOINT_DIR",
                                          Path(self._tmp.name))
        self._patcher.start()
        self._wf = mock.patch.object(engine, "load_workflow",
                                     return_value=FANOUT_WF)
        self._wf.start()

    def tearDown(self):
        self._wf.stop()
        self._patcher.stop()
        self._tmp.cleanup()

    def test_fanout_branches_run_in_parallel_and_join(self):
        state = _FanoutState()
        with mock.patch.dict(engine.ACTIONS, _make_actions(state)):
            ctx = TaskContext("fanout02", "fanout_demo", {})
            engine.run(ctx)
        self.assertEqual(ctx.status, "done")
        # 每个节点恰好执行一次，产物齐全
        for nid in ("A", "B", "C", "D"):
            self.assertEqual(state.calls.get(nid), 1, nid)
            self.assertIn(nid, ctx.artifacts)
            self.assertIn(nid, ctx.node_durations)
        # D 在 B/C 之后执行（汇合语义）
        self.assertGreater(state.order.index("D"), state.order.index("B"))
        self.assertGreater(state.order.index("D"), state.order.index("C"))
        # 事件流：扇出、分支完成（带 branch 标记）、汇合
        msgs = [e["message"] for e in ctx.events]
        self.assertTrue(any("扇出" in m and "并行执行" in m for m in msgs))
        self.assertTrue(any("汇合到 D" in m for m in msgs))
        branch_evts = [e for e in ctx.events if e.get("branch") in ("B", "C")]
        self.assertEqual(len(branch_evts), 2)

    def test_join_reads_branch_artifacts(self):
        state = _FanoutState()
        with mock.patch.dict(engine.ACTIONS, _make_actions(state)):
            ctx = TaskContext("fanout03", "fanout_demo", {})
            engine.run(ctx)
        self.assertEqual(ctx.artifacts["D"],
                         {"from_b": "b", "from_c": "c"})

    def test_resume_after_join_skips_finished_branches(self):
        """模拟崩溃在汇合边界：删掉 D 的产物、保留 B/C，重跑只补 D。"""
        state = _FanoutState()
        with mock.patch.dict(engine.ACTIONS, _make_actions(state)):
            ctx = TaskContext("fanout04", "fanout_demo", {})
            engine.run(ctx)
            self.assertEqual(ctx.status, "done")

            ctx.artifacts.pop("D")
            ctx.node_durations.pop("D")
            ctx.pointer = "D"  # 汇合后存档的指针位置（then）
            engine.run(ctx)
        self.assertEqual(ctx.status, "done")
        self.assertEqual(state.calls["B"], 1)  # 已完成分支不重复执行
        self.assertEqual(state.calls["C"], 1)
        self.assertEqual(state.calls["D"], 2)  # D 被补跑
        self.assertEqual(ctx.artifacts["D"], {"from_b": "b", "from_c": "c"})

    def test_resume_at_fanout_boundary_reenters_fanout(self):
        """崩溃在扇出边界（pointer=A）：A/B/C 全部幂等跳过，仅补 D。"""
        state = _FanoutState()
        with mock.patch.dict(engine.ACTIONS, _make_actions(state)):
            ctx = TaskContext("fanout05", "fanout_demo", {})
            engine.run(ctx)
            self.assertEqual(ctx.status, "done")

            ctx.artifacts.pop("D")
            ctx.node_durations.pop("D")
            ctx.pointer = "A"  # 并发段开始前的存档指针
            engine.run(ctx)
        self.assertEqual(ctx.status, "done")
        self.assertEqual(state.calls, {"A": 1, "B": 1, "C": 1, "D": 2})

    def test_pause_inside_branch_resumes_idempotently(self):
        state = _FanoutState()
        actions = _make_actions(state, pause_in_c=True)
        with mock.patch.dict(engine.ACTIONS, actions):
            ctx = TaskContext("fanout06", "fanout_demo", {})
            engine.run(ctx)
            # C 首次执行抛 PauseRequested：整体 paused，指针停在扇出节点
            self.assertEqual(ctx.status, "paused")
            self.assertEqual(ctx.pointer, "A")
            self.assertIn("B", ctx.artifacts)   # 已完成分支产物已落盘
            self.assertNotIn("C", ctx.artifacts)

            ctx.clear_pause()
            engine.run(ctx)
        self.assertEqual(ctx.status, "done")
        self.assertEqual(state.calls["B"], 1)  # B 幂等跳过，未重复执行
        self.assertEqual(state.calls["C"], 2)  # C 未完成，续跑重入
        self.assertEqual(state.calls["D"], 1)

    def test_cancel_terminates_branches(self):
        state = _FanoutState()

        def act_b_cancel(ctx, params):
            state.record("B")
            state.b_done.set()
            ctx.request_cancel()
            return {"ok": "b"}

        actions = _make_actions(state)
        actions["act_b"] = act_b_cancel
        with mock.patch.dict(engine.ACTIONS, actions):
            ctx = TaskContext("fanout07", "fanout_demo", {})
            engine.run(ctx)
        self.assertEqual(ctx.status, "cancelled")
        self.assertEqual(state.calls.get("B"), 1)


class SingleListNextTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patcher = mock.patch.object(engine, "CHECKPOINT_DIR",
                                          Path(self._tmp.name))
        self._patcher.start()
        self._wf = mock.patch.object(engine, "load_workflow",
                                     return_value=SINGLE_LIST_WF)
        self._wf.start()
        self.state = _FanoutState()

    def tearDown(self):
        self._wf.stop()
        self._patcher.stop()
        self._tmp.cleanup()

    def test_single_element_list_is_serial(self):
        with mock.patch.dict(engine.ACTIONS, _make_actions(self.state)):
            ctx = TaskContext("single01", "single_list_demo", {})
            engine.run(ctx)
        self.assertEqual(ctx.status, "done")
        self.assertEqual(self.state.order, ["A", "B"])


class ConditionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patcher = mock.patch.object(engine, "CHECKPOINT_DIR",
                                          Path(self._tmp.name))
        self._patcher.start()
        self._wf = mock.patch.object(engine, "load_workflow",
                                     return_value=COND_WF)
        self._wf.start()
        self.calls: list[str] = []

    def tearDown(self):
        self._wf.stop()
        self._patcher.stop()
        self._tmp.cleanup()

    def _run_cond(self, wf: dict | None = None,
                  params: dict | None = None) -> TaskContext:
        payload = (params or {}).get("_src_payload", {})

        def act_src(ctx, p):
            return payload

        def act_yes(ctx, p):
            self.calls.append("yes")
            return {"ok": "yes"}

        def act_no(ctx, p):
            self.calls.append("no")
            return {"ok": "no"}

        target = wf or COND_WF
        with mock.patch.object(engine, "load_workflow", return_value=target), \
                mock.patch.dict(engine.ACTIONS,
                                {"act_src": act_src, "act_yes": act_yes,
                                 "act_no": act_no}):
            ctx = TaskContext("cond01", target["name"], params or {})
            engine.run(ctx)
        return ctx

    def test_truthy_path_exists_takes_on_true(self):
        ctx = self._run_cond(params={"_src_payload": {"pool": [{"id": "W1"}]}})
        self.assertEqual(ctx.status, "done")
        self.assertEqual(self.calls, ["yes"])
        self.assertEqual(ctx.artifacts["check"],
                         {"branch": True, "value": [{"id": "W1"}]})

    def test_missing_path_is_falsy_takes_on_false(self):
        ctx = self._run_cond(params={"_src_payload": {"other": 1}})
        self.assertEqual(self.calls, ["no"])
        self.assertEqual(ctx.artifacts["check"]["branch"], False)
        self.assertIsNone(ctx.artifacts["check"]["value"])

    def test_non_empty_op(self):
        wf = {
            "name": "cond_non_empty",
            "nodes": [
                {"id": "src", "type": "tool", "action": "act_src", "next": "check"},
                {"id": "check", "type": "condition", "if": "src.pool",
                 "op": "non_empty", "on_true": "yes", "on_false": "no"},
                {"id": "yes", "type": "tool", "action": "act_yes", "next": None},
                {"id": "no", "type": "tool", "action": "act_no", "next": None},
            ],
        }
        ctx = self._run_cond(wf, {"_src_payload": {"pool": []}})
        self.assertEqual(self.calls, ["no"])  # 空列表 -> non_empty 为假
        ctx2_calls_before = list(self.calls)
        ctx = self._run_cond(wf, {"_src_payload": {"pool": ["x"]}})
        self.assertEqual(self.calls[len(ctx2_calls_before):], ["yes"])
        self.assertTrue(ctx.artifacts["check"]["branch"])

    def test_eq_op_with_params_prefix(self):
        wf = {
            "name": "cond_eq",
            "nodes": [
                {"id": "src", "type": "tool", "action": "act_src", "next": "check"},
                {"id": "check", "type": "condition", "if": "params.mode",
                 "op": "eq", "value": "deep",
                 "on_true": "yes", "on_false": "no"},
                {"id": "yes", "type": "tool", "action": "act_yes", "next": None},
                {"id": "no", "type": "tool", "action": "act_no", "next": None},
            ],
        }
        base = len(self.calls)
        ctx = self._run_cond(wf, {"mode": "deep", "_src_payload": {}})
        self.assertEqual(self.calls[base:], ["yes"])
        self.assertEqual(ctx.artifacts["check"],
                         {"branch": True, "value": "deep"})

        base = len(self.calls)
        ctx = self._run_cond(wf, {"mode": "fast", "_src_payload": {}})
        self.assertEqual(self.calls[base:], ["no"])
        self.assertFalse(ctx.artifacts["check"]["branch"])

    def test_unknown_op_fails_node(self):
        wf = {
            "name": "cond_bad_op",
            "nodes": [
                {"id": "src", "type": "tool", "action": "act_src", "next": "check"},
                {"id": "check", "type": "condition", "if": "params.mode",
                 "op": "bogus", "on_true": "yes", "on_false": "no"},
                {"id": "yes", "type": "tool", "action": "act_yes", "next": None},
                {"id": "no", "type": "tool", "action": "act_no", "next": None},
            ],
        }
        ctx = self._run_cond(wf, {"mode": "deep", "_src_payload": {}})
        self.assertEqual(ctx.status, "failed")


if __name__ == "__main__":
    unittest.main()
