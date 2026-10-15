"""研究档案库：科研产物的 Git 版本管理。

每次 /export 把任务产物复制进 data/research_repo/tasks/<task_id>/ 并以
Gezhi 身份自动 commit——研究过程从此有 diff、有历史、可回滚，与 F5
「断点续跑/版本化干预」同一哲学：科研每一步都留得下、退得回。
git 不可用时全部函数优雅降级（返回提示/None），绝不炸主流程。
"""
from __future__ import annotations

import shutil
import subprocess

from . import config

REPO_DIR = config.DATA_DIR / "research_repo"
_IDENTITY = ["-c", "user.name=Gezhi SciZhi", "-c", "user.email=gezhi@local",
             "-c", "core.quotepath=false"]


def git_available() -> bool:
    return shutil.which("git") is not None


def _run(*args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", *_IDENTITY, *args], cwd=REPO_DIR,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        return r.returncode, (r.stdout or r.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def ensure() -> bool:
    """确保档案库存在（git init 幂等）。"""
    if not git_available():
        return False
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    if (REPO_DIR / ".git").exists():
        return True
    rc, _ = _run("init")
    return rc == 0


def commit_task(task_id: str, topic: str = "") -> str | None:
    """导出任务产物入档并 commit；返回短哈希（无变更/失败返回 None 或 HEAD）。"""
    from . import service
    if not ensure():
        return None
    task_dir = REPO_DIR / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    files = service.export_artifacts(task_id, task_dir)
    if not files:
        return None
    _run("add", "-A")
    msg = f"task {task_id}: {topic or '科研任务'}（{len(files)} 个产物文件）"
    rc, out = _run("commit", "-m", msg)
    if rc != 0:
        if "nothing to commit" in out:  # 重复导出：返回最近提交
            rc2, head = _run("rev-parse", "--short", "HEAD")
            return head if rc2 == 0 else None
        return None
    rc2, head = _run("rev-parse", "--short", "HEAD")
    return head if rc2 == 0 else None


def log_text(limit: int = 10) -> str:
    if not ensure():
        return "git 不可用，研究档案库未启用"
    rc, out = _run("--no-pager", "log", "--oneline", "-n", str(limit))
    return out if (rc == 0 and out) else "（档案库暂无提交——/export 导出后自动入档）"


def status_text() -> str:
    if not ensure():
        return "git 不可用，研究档案库未启用"
    rc, out = _run("status", "--short")
    hrc, head = _run("rev-parse", "--short", "HEAD")
    head_line = f"HEAD {head}" if hrc == 0 else "HEAD（空档案库）"
    return f"{head_line}\n{out or '（工作区干净）'}"
