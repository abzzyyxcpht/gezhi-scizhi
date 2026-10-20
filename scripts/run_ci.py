"""本地 CI：依次跑公开源码清单校验、Python 全量测试、Ruff、TUI 测试。

任何一段失败即以非零码退出；全部通过打印汇总。与 .github/workflows/ci.yml
保持一致。用法：python scripts/run_ci.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _steps(pytest_basetemp: Path) -> list[tuple[str, list[str], Path]]:
    """按顺序返回 CI 步骤；pytest 的 --basetemp 由调用方给出系统临时路径。"""
    return [
        ("公开源码清单校验",
         [sys.executable, "scripts/verify_public_source.py"], ROOT),
        ("Python 全量测试",
         [sys.executable, "-m", "pytest", "tests", "-q",
          "--basetemp", str(pytest_basetemp)], ROOT),
        ("Ruff 静态检查",
         [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"], ROOT),
        ("TUI 测试",
         ["npm.cmd" if os.name == "nt" else "npm", "test"], ROOT / "tui"),
    ]


def _configure_stdio() -> None:
    """Keep CI diagnostics printable on Windows runners using a legacy code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):
            # Some embedders expose a stream-like object without reconfigurable encoding.
            continue


def main() -> int:
    _configure_stdio()
    # --basetemp 必须落在系统临时目录：旧实现每次在仓库 build/ 留下
    # pytest-ci-<uuid> 且不清理。整棵临时目录在 finally 中删除。
    scratch = Path(tempfile.mkdtemp(prefix="gezhi-ci-pytest-"))
    failed = False
    try:
        for name, cmd, cwd in _steps(scratch / "pytest"):
            if cmd[0] in {"npm", "npm.cmd"} and shutil.which(cmd[0]) is None:
                print(f"[失败] {name}：未找到 Node.js/npm，无法完成验收")
                failed = True
                continue
            print(f"\n===== {name} =====")
            if subprocess.call(cmd, cwd=cwd) != 0:
                print(f"[失败] {name}")
                failed = True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print("\n===== CI 汇总 =====")
    print("存在失败步骤" if failed else "全部通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
