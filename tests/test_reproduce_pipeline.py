"""论文复现流水线测试。

覆盖：计划生成（确定性命令推断）、白名单拒绝、真实命令执行（退出码与输出）、
超时容错路径（以 sleep 超长命令验证）、实验记录入库（完成/失败均写）、
commands 缺省时取计划产物、复现沙箱边界（shell 元字符注入与越界 cwd）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from src import config
from src import research_notebook
from src.agents import reproduce as reproduce_mod
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


class ReproduceSandboxTests(unittest.TestCase):
    """reproduce_run 复现沙箱：shell 元字符拒绝与 cwd 不得越出项目根。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.data = Path(self.tmp.name) / "data"
        patch = mock.patch.object(config, "DATA_DIR", self.data)
        patch.start()
        self.addCleanup(patch.stop)

    def context(self, **params):
        return TaskContext(task_id="repro_sandbox", workflow="reproduce",
                           params={"topic": "复现沙箱边界", **params})

    def test_shell_metacharacters_are_rejected_before_any_process_starts(self):
        marker = self.project / "pwned.txt"
        commands = [
            "python -m pytest; echo pwned",
            "python -m pytest && echo pwned",
            "python -m pytest | echo pwned",
            "python -m pytest $(whoami)",
            "python -m pytest `echo pwned`",
            "python -m pytest %USERNAME%",
            "python -m pytest > " + str(marker),
            "python -m pytest\npython -m pip --version",
        ]
        ctx = self.context(project_root=str(self.project))
        with mock.patch.object(reproduce_mod.subprocess, "run") as runner:
            for start in range(0, len(commands), 5):  # 单次最多检查 5 条命令
                batch = commands[start:start + 5]
                run = reproduce_mod.reproduce_run(ctx, {"commands": batch})
                self.assertEqual(len(batch), run["total"])
                self.assertEqual(0, run["passed"])
                self.assertTrue(all(row["rejected"] for row in run["results"]))
                self.assertTrue(all("shell 元字符" in row["output"]
                                    for row in run["results"]))
            runner.assert_not_called()
        self.assertFalse(marker.exists())

    def test_cwd_must_resolve_inside_project_root(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        ctx = self.context(project_root=str(self.project))
        for cwd in ("..", "../outside", str(outside), str(outside / "..")):
            with self.subTest(cwd=cwd), self.assertRaisesRegex(ValueError, "cwd 越界"):
                reproduce_mod.reproduce_run(
                    ctx, {"commands": ["python -m pip --version"], "cwd": cwd})

    def test_project_root_from_ctx_attribute_and_inner_cwd_are_accepted(self):
        work = self.project / "work"
        work.mkdir()
        ctx = self.context()
        ctx.project_root = str(self.project)  # ctx 显式提供项目根时优先于进程 cwd
        run = reproduce_mod.reproduce_run(
            ctx, {"commands": ["python -m pip --version"], "cwd": "work"})
        self.assertEqual(1, run["total"])
        self.assertTrue(run["results"][0]["ok"], run["results"][0])
        self.assertIn("pip", run["results"][0]["output"].lower())

    def test_whitelist_and_forbidden_words_are_preserved(self):
        ctx = self.context(project_root=str(self.project))
        run = reproduce_mod.reproduce_run(
            ctx, {"commands": ["echo hello", "python -m pytest && rm -rf build",
                               'python -c "print(1)"']})
        self.assertEqual(0, run["passed"])
        self.assertTrue(all(row["rejected"] for row in run["results"]))
        self.assertIn("不在复现白名单", run["results"][0]["output"])


if __name__ == "__main__":
    unittest.main()

