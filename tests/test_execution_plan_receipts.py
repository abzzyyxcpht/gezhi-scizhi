"""D：执行计划回执口径统一——无 ok 字段的成功工具也能成为完成依据。"""
from __future__ import annotations

import pytest

from src.agent_runtime import AgentRuntime, ToolCall
from src.agents import stats
from src.execution_plan import (acceptable_receipts, completion_issue,
                                read_plan)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    # 统计图表写入临时产物目录，避免在仓库 data/ 下留文件。
    monkeypatch.setattr(stats, "ARTIFACT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(stats, "log_tool", lambda *args, **kwargs: None)
    return AgentRuntime(llm_enabled=False, session_root=tmp_path / "sessions")


def _session(runtime: AgentRuntime) -> str:
    sid = "receipts"
    runtime.sessions.append(sid, "user_message", {"text": "分析实验数据", "mode": "agent"})
    runtime.sessions.append(sid, "turn_started", {"turn_id": "receipt-turn"})
    return sid


def _step(status: str = "in_progress", evidence: tuple[str, ...] = ()) -> dict:
    return {"id": "analyze", "title": "分析实验数据", "criterion": "返回可复核的统计结果",
            "kind": "execution", "status": status, "evidence": list(evidence), "reason": ""}


def _plan_update(runtime: AgentRuntime, sid: str, call_id: str,
                 revision: int, step: dict) -> dict:
    return runtime._execute_call(sid, ToolCall(call_id, "plan.update", {
        "expected_turn_id": "receipt-turn", "expected_revision": revision,
        "steps": [step]}, "write"))


def test_execute_call_normalizes_results_without_ok(runtime):
    sid = _session(runtime)
    runtime._register("fixture.plain", "read", "无 ok 的成功结果", lambda _: {"items": [1]})
    runtime._register("fixture.error", "read", "无 ok 的失败结果", lambda _: {"error": "nope"})
    ok = runtime._execute_call(sid, ToolCall("plain-call", "fixture.plain", {}, "read"))
    failed = runtime._execute_call(sid, ToolCall("error-call", "fixture.error", {}, "read"))
    assert ok["ok"] is True
    assert failed["ok"] is False
    assert ok["executed"] is True
    # 归一化结果落在会话日志里，执行计划读到的是同一份口径
    logged = [event["payload"]["result"] for event in runtime.sessions.read(sid)
              if event["type"] == "tool_result"]
    assert [row["ok"] for row in logged] == [True, False]


def test_stats_analyze_without_ok_supports_plan_completion(runtime, tmp_path):
    csv = tmp_path / "experiment.csv"
    csv.write_text("value\n1\n2\n3\n", encoding="utf-8")
    sid = _session(runtime)
    result = runtime._execute_call(
        sid, ToolCall("stats-call", "stats.analyze", {"path": str(csv)}, "read"))
    assert result["ok"] is True
    assert result["executed"] is True
    receipts = acceptable_receipts(runtime.sessions, sid)
    assert [row["call_id"] for row in receipts] == ["stats-call"]
    created = _plan_update(runtime, sid, "plan-create", 0, _step())
    assert created["ok"] is True, created
    finished = _plan_update(runtime, sid, "plan-finish", 1,
                            _step("completed", ("stats-call",)))
    assert finished["ok"] is True, finished
    assert read_plan(runtime.sessions, sid)["ready"] is True
    assert completion_issue(runtime.sessions, sid) == ""


def test_failed_or_timed_out_tools_cannot_support_completion(runtime):
    sid = _session(runtime)
    runtime._register("fixture.failure", "read", "失败夹具", lambda _: {"error": "boom"})
    runtime._register("fixture.timeout", "read", "超时夹具",
                      lambda _: {"timed_out": True, "status": "done"})
    failed = runtime._execute_call(
        sid, ToolCall("fail-call", "fixture.failure", {}, "read"))
    timed = runtime._execute_call(
        sid, ToolCall("timeout-call", "fixture.timeout", {}, "read"))
    assert failed["ok"] is False
    # 超时结果虽被归一化为 ok=True，计划回执仍必须拒绝它
    assert timed["ok"] is True
    assert acceptable_receipts(runtime.sessions, sid) == []
    assert _plan_update(runtime, sid, "plan-create", 0, _step())["ok"] is True
    for call_id in ("fail-call", "timeout-call"):
        rejected = _plan_update(runtime, sid, f"plan-{call_id}", 1,
                                _step("completed", (call_id,)))
        assert rejected["ok"] is False, rejected
    plan = read_plan(runtime.sessions, sid)
    assert plan["ready"] is False
    assert plan["completed"] == 0
    assert completion_issue(runtime.sessions, sid)
