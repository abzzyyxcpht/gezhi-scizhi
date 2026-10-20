# -*- coding: utf-8 -*-
"""发布自检门禁的回归测试：在 ``tmp_path`` 复刻最小仓库结构，绝不读写真实仓库。

旧版第二个用例会删除并回写真实仓库的 ``workflows/research_pipeline.yaml``，
测试中断就会丢文件；现在缺失、空文件、坏 JSON 与版本漂移都在临时目录里构造。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts.release_check import run_checks

REPO_ROOT = Path(__file__).resolve().parents[1]

# run_checks 的硬性资源：从真实仓库复制到 tmp_path，避免把结构写死在测试里。
REQUIRED_FILES = (
    "src/version.py",
    "src/agent_runtime.py",
    "src/agents/specs.py",
    "workflows/research_pipeline.yaml",
    "examples/experiment_demo.csv",
    "tui/package.json",
)
REQUIRED_DIRS = ("plugins", "skills")


def _minimal_root(tmp_path: Path) -> Path:
    """复刻 run_checks 所需的最小结构；所有写入都发生在 tmp_path 内。"""
    for rel in REQUIRED_FILES:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, target)
    for rel in REQUIRED_DIRS:
        (tmp_path / rel).mkdir()
    return tmp_path


def _check(report: dict, name: str) -> dict:
    return next(item for item in report["checks"] if item["name"] == name)


def test_release_check_reports_required_assets_and_hard_failure(tmp_path):
    report = run_checks(_minimal_root(tmp_path))
    names = {item["name"] for item in report["checks"]}
    assert {"核心源码", "工作流配置", "示例数据", "TUI 配置"} <= names
    assert _check(report, "版本一致")["ok"]
    assert report["ok"], report["summary"]


def test_release_check_passes_on_real_repository():
    report = run_checks(REPO_ROOT)
    failed = [item for item in report["checks"] if not item["ok"]]
    assert report["ok"], failed


def test_release_check_marks_missing_workflow_as_failure(tmp_path):
    root = _minimal_root(tmp_path)
    (root / "workflows" / "research_pipeline.yaml").unlink()
    report = run_checks(root)
    assert not report["ok"]
    item = _check(report, "工作流配置")
    assert not item["ok"] and "缺少" in item["detail"]


def test_release_check_rejects_empty_hard_asset(tmp_path):
    root = _minimal_root(tmp_path)
    (root / "src" / "agent_runtime.py").write_text("", encoding="utf-8")
    report = run_checks(root)
    assert not report["ok"]
    item = _check(report, "Agent Harness")
    assert not item["ok"] and "空" in item["detail"]


def test_release_check_rejects_unparseable_json(tmp_path):
    root = _minimal_root(tmp_path)
    (root / "tui" / "package.json").write_text("{not json", encoding="utf-8")
    report = run_checks(root)
    assert not report["ok"]
    item = _check(report, "TUI 配置")
    assert not item["ok"] and "JSON" in item["detail"]


def test_release_check_rejects_version_drift(tmp_path):
    root = _minimal_root(tmp_path)
    package_path = root / "tui" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["version"] = "0.0.0-drift"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    report = run_checks(root)
    assert not report["ok"]
    item = _check(report, "版本一致")
    assert not item["ok"]
    assert "0.0.0-drift" in item["detail"]
