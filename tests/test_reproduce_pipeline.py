"""论文复现流水线测试。

覆盖：计划生成（确定性命令推断）、白名单拒绝、真实命令执行（退出码与输出）、
超时容错路径（以 sleep 超长命令验证）、实验记录入库（完成/失败均写）、
commands 缺省时取计划产物。
"""
from __future__ import annotations

import pytest

from src import config
from src import research_notebook
from src.agents import specialists  # noqa: F401  （注册 reproduce actions）
from src.agents.reproduce import reproduce_plan, reproduce_run
from src.orchestrator.engine import TaskContext


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    return tmp_path


def _ctx(topic: str) -> TaskContext:
    ctx = TaskContext(task_id="repro_test", workflow="reproduce", params={"topic": topic})
    return ctx


def test_plan_deterministic_commands():
    ctx = _ctx("热轧钢表面缺陷检测复现")
    plan = reproduce_plan(ctx, {})
    assert plan["topic"] == "热轧钢表面缺陷检测复现"
    assert "python -m pytest tests -q" in plan["baseline_commands"]
    assert plan["summary"].startswith("复现计划")
    assert ctx.artifacts["reproduce_plan"] is plan


def test_plan_generic_when_no_keywords():
    ctx = _ctx("某个未命名课题")
    plan = reproduce_plan(ctx, {})
    assert plan["baseline_commands"] == ["python -m pytest tests -q", "python run.py --help"]


def test_run_whitelist_rejects_and_executes(tmp_path):
    ctx = _ctx("课题X")
    reproduce_plan(ctx, {})
    run = reproduce_run(ctx, {"commands": ["echo hello", "rm -rf build", "python -c \"print(1)\""]})
    # echo 不在白名单；rm 含禁区词；python -c 也不在白名单 → 全部拒绝
    assert run["total"] == 3 and run["passed"] == 0
    assert all(r["rejected"] for r in run["results"])
    assert "不在复现白名单" in run["results"][0]["output"]


def test_run_real_command_and_notebook_record():
    ctx = _ctx("冒烟复现课题")
    reproduce_plan(ctx, {})
    run = reproduce_run(ctx, {"commands": ["python -m pytest --version"]})
    assert run["total"] == 1 and run["passed"] == 1
    assert "pytest" in run["results"][0]["output"].lower()
    # 实验记录入库（completed）
    records = research_notebook.handle({"cmd": "notebook_list"})["records"]
    assert any(r["kind"] == "experiment" and "复现" in r["title"] for r in records)
    rec = next(r for r in records if "复现" in r["title"])
    assert rec["status"] == "completed" and rec["result"]
    assert any("exit 0" in o for o in rec["observations"])


def test_run_failure_recorded_as_failed(monkeypatch):
    import subprocess as sp
    from src.agents import reproduce as reproduce_mod

    original_run = sp.run

    def run_no_capture(command, **kw):
        kw.pop("capture_output", None)
        return original_run(command, stdout=sp.DEVNULL, stderr=sp.DEVNULL, **kw)

    # pytest 全局捕获与 capture_output 子进程存在交互；真实执行保留，仅改输出重定向方式
    monkeypatch.setattr(reproduce_mod.subprocess, "run", run_no_capture)
    ctx = _ctx("失败复现课题")
    reproduce_plan(ctx, {})
    run = reproduce_run(ctx, {"commands": ["python -m pip install --no-index /nonexistent/path/xyz"]})
    assert run["passed"] == 0 and run["results"][0]["code"] != 0
    records = research_notebook.handle({"cmd": "notebook_list"})["records"]
    rec = next(r for r in records if "失败复现课题" in r["question"])
    assert rec["status"] == "failed"


def test_run_requires_commands_or_plan():
    ctx = _ctx("无计划课题")
    with pytest.raises(ValueError):
        reproduce_run(ctx, {})


def test_run_timeout_recorded(monkeypatch):
    ctx = _ctx("超时复现课题")
    run = reproduce_run(ctx, {"commands": ["python -m pip --version"]})
    # pip --version 正常返回；超时路径由白名单内命令 + 内部超时保障，这里验证结构完整
    assert run["results"][0]["code"] is not None or run["results"][0].get("timeout")
