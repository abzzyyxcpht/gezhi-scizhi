"""本地 CI：依次跑 Python 全量测试、Ruff（可选）、TUI 测试。

任何一段失败即以非零码退出；全部通过打印汇总。与 .github/workflows/ci.yml
保持一致。用法：python scripts/run_ci.py
"""
from __future__ import annotations

import shutil
import os
import uuid
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = [
    ("Python 全量测试", [sys.executable, "-m", "pytest", "tests", "-q", "--basetemp", str(ROOT / "build" / ("pytest-ci-" + uuid.uuid4().hex))], ROOT),
    ("Ruff 静态检查", [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"], ROOT),
    ("TUI 测试", ["npm.cmd" if os.name == 'nt' else "npm", "test"], ROOT / "tui"),
]


def main() -> int:
    failed = False
    for name, cmd, cwd in STEPS:
        if cmd[0] in {"npm", "npm.cmd"} and shutil.which(cmd[0]) is None:
            print(f"[失败] {name}：未找到 Node.js/npm，无法完成验收")
            failed = True
            continue
        print(f"\n===== {name} =====")
        if subprocess.call(cmd, cwd=cwd) != 0:
            print(f"[失败] {name}")
            failed = True
    print("\n===== CI 汇总 =====")
    print("存在失败步骤" if failed else "全部通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
