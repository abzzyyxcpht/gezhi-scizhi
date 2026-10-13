"""发布前全量验收：自检、Python 测试、TUI 冒烟和端到端演示。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


def configure_output() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_command(name: str, command: list[str], cwd: Path,
                timeout: int = 300) -> dict[str, Any]:
    started = time.perf_counter()
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(command, cwd=cwd, env=env, text=True,
                              encoding="utf-8", errors="replace",
                              capture_output=True, timeout=timeout)
        return {"name": name, "returncode": proc.returncode,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "stdout": (proc.stdout + proc.stderr)[-4000:]}
    except subprocess.TimeoutExpired as exc:
        return {"name": name, "returncode": 124,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "stdout": f"超时：{exc.timeout}s"}


def build_report(root: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
    gates = [{"name": item["name"], "ok": item["returncode"] == 0,
              "duration_ms": item.get("duration_ms", 0)} for item in results]
    return {"ok": all(gate["ok"] for gate in gates),
            "hard_gates": gates, "results": results,
            "root": str(root), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def main() -> int:
    configure_output()
    root = Path(__file__).resolve().parents[1]
    py = sys.executable
    commands = [
        ("ui_lint", [py, "scripts/lint_ui.py"], root),
        ("ruff", [py, "-m", "ruff", "check", "src", "tests", "scripts"], root),
        ("release_check", [py, "scripts/release_check.py"], root),
        ("python_tests", [py, "-m", "pytest", "tests", "-q", "--basetemp", str(root / "build" / ("pytest-release-" + uuid.uuid4().hex))], root),
        ("tui_smoke", ["npm.cmd" if os.name == "nt" else "npm", "test"], root / "tui"),
        ("workbench_e2e", [py, "scripts/verify_workbench.py"], root),
        ("offline_demo", [py, "scripts/gezhi.py", "demo",
                          "large language model agents", "--out",
                          "data/exports/release_verify"], root),
    ]
    results = []
    for name, command, cwd in commands:
        print(f"\n=== {name} ===")
        result = run_command(name, command, cwd)
        results.append(result)
        print(result["stdout"][-1200:])
        print(f"[{name}] returncode={result['returncode']}")

    report = build_report(root, results)
    out = root / "data" / "exports" / "release_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)
    print(f"\n发布验收：{'PASS' if report['ok'] else 'FAIL'}")
    print(f"报告：{out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
