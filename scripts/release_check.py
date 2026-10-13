"""格致 SciZhi 发布就绪检查。

硬性资源缺失会阻断发布；网络、LLM、Git 和 MCP 等可选能力只报告状态，
保证无外网环境仍可完成离线演示。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any


def _asset(root: Path, name: str, rel: str, hard: bool = True) -> dict[str, Any]:
    path = root / rel
    ok = path.exists()
    return {"name": name, "ok": ok, "hard": hard,
            "detail": str(path) if ok else f"缺少 {path}"}


def _optional(name: str, fn) -> dict[str, Any]:
    try:
        ok, detail = fn()
        return {"name": name, "ok": bool(ok), "hard": False,
                "detail": str(detail)}
    except Exception as exc:  # noqa: BLE001 - 自检不能阻断其它检查
        return {"name": name, "ok": False, "hard": False,
                "detail": f"检查异常：{type(exc).__name__}"}


def run_checks(root: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    checks: list[dict[str, Any]] = [
        _asset(root, "核心源码", "src"),
        _asset(root, "Agent Harness", "src/agent_runtime.py"),
        _asset(root, "专项智能体编制表", "src/agents/specs.py"),
        _asset(root, "工作流配置", "workflows/research_pipeline.yaml"),
        _asset(root, "示例数据", "examples/experiment_demo.csv"),
        _asset(root, "插件目录", "plugins"),
        _asset(root, "技能目录", "skills"),
        _asset(root, "TUI 配置", "tui/package.json"),
    ]

    def import_core() -> tuple[bool, str]:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        importlib.import_module("src.service")
        return True, "src.service 可导入"

    checks.append(_optional("核心模块", import_core))

    def node_check() -> tuple[bool, str]:
        pkg = json.loads((root / "tui" / "package.json").read_text(encoding="utf-8"))
        return bool(pkg.get("scripts", {}).get("test")), "TUI 测试脚本已声明"

    checks.append(_optional("TUI 测试脚本", node_check))

    hard_ok = all(item["ok"] for item in checks if item["hard"])
    passed = sum(1 for item in checks if item["ok"])
    return {"ok": hard_ok, "checks": checks,
            "summary": f"{passed}/{len(checks)} 项通过"}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report = run_checks(root)
    print(f"发布自检：{report['summary']}")
    for item in report["checks"]:
        mark = "✔" if item["ok"] else "✘"
        suffix = "（硬性）" if item["hard"] else "（可选）"
        print(f"  {mark} {item['name']}{suffix}：{item['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
