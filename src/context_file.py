"""GEZHI.md 研究上下文文件：保存项目规则与研究约束。

放置在项目根目录或工作目录的 GEZHI.md 会被每次流水线运行读取，
正文注入系统提示词——研究方向、术语表、格式偏好由用户显式声明，
智能体不再"猜"用户想要什么。/init 生成起步模板。
"""
from __future__ import annotations

import re
import hashlib
from pathlib import Path
from typing import Any

from . import config

TEMPLATE = """# GEZHI.md —— 研究上下文（格致每次流水线运行都会读取）

## 研究方向
<!-- 一句话描述当前课题，例如：面向生物医学语料的检索增强生成 -->

## 术语表（每行一条：中文译名 = 英文原文名）
<!-- 格致在抽取与综述生成时统一采用这些译名 -->
检索增强生成 = RAG

## 偏好
- 引用格式：GB/T 7714（顺序编码制）
- 文献时间窗：近 5 年
- 综述框架：4-6 节
- 禁止使用第一人称
"""


def _cwd() -> Path:
    """工作目录（独立函数便于测试打桩）。"""
    return Path.cwd()


def _candidates(project_root: str | Path | None = None) -> list[tuple[Path, str]]:
    """Return context files in broad-to-specific order.

    A bound project is an explicit boundary: it never falls back to the
    application checkout or the process cwd.  The no-project form retains the
    legacy discovery order used by ``/context`` and old sessions.
    """
    if project_root:
        root = Path(project_root).expanduser().resolve()
        return [(config.DATA_DIR / "GEZHI.md", "user"),
                (root / "GEZHI.md", "project"),
                (root / "GEZHI.local.md", "local")]
    return [(config.ROOT_DIR / "GEZHI.md", "application"),
            (_cwd() / "GEZHI.md", "working_directory"),
            (config.DATA_DIR / "GEZHI.md", "user")]


def find(project_root: str | Path | None = None) -> Path | None:
    for p, _ in _candidates(project_root):
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def load_sources(*, max_chars: int = 2000,
                 project_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Read bounded context sources and retain provenance for prompt assembly."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 0:
        raise ValueError("max_chars 必须是非负整数")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, scope in _candidates(project_root):
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        base: dict[str, Any] = {
            "kind": "instructions", "name": path.name, "scope": scope,
            "path": resolved, "status": "missing", "used_chars": 0,
            "included_chars": 0, "total_chars": 0, "truncated": False,
        }
        try:
            if not path.is_file():
                rows.append(base)
                continue
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise OSError('规则文件不能通过链接跨越作用域')
            with path.open('rb') as stream:
                raw = stream.read(262145)
            if len(raw) > 262144:
                base.update(status='budget_deferred', reason='source_too_large',
                            total_chars=None, truncated=True)
                rows.append(base)
                if not project_root:
                    break
                continue
            text = raw.decode('utf-8', errors='replace')
            base["total_chars"] = len(text)
            base["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            body = text[:max_chars]
            base["used_chars"] = base["included_chars"] = len(body)
            base["truncated"] = len(body) < len(text)
            base["status"] = "included" if body else "budget_deferred"
            base["body"] = body
        except OSError as exc:
            base.update(status="error", reason=str(exc)[:180])
        rows.append(base)
        if not project_root and base['status'] in {'included', 'budget_deferred'}:
            break  # unbound legacy discovery uses the first existing source
    return rows


def load_body(project_root: str | Path | None = None) -> str:
    """正文（截断 2000 字符，防提示词膨胀）。"""
    rows = load_sources(project_root=project_root)
    return str(next((row.get("body", "") for row in rows
                     if row.get("status") == "included"), ""))


def status(project_root: str | Path | None = None) -> dict[str, Any]:
    rows = load_sources(project_root=project_root)
    body = str(next((row.get("body", "") for row in rows
                     if row.get("status") == "included"), ""))
    headings = [h.strip() for h in re.findall(r"^##\s+(.+)$", body, re.M)]
    p = next((row for row in rows if row.get("status") in {"included", "budget_deferred"}), None)
    return {"found": p is not None, "path": p.get("path", "") if p else "",
            "bytes": len(body.encode("utf-8")), "sections": headings,
            "sources": [{k: v for k, v in row.items() if k != "body"} for row in rows]}


def init_template() -> Path:
    """生成起步模板：工作目录优先，其次数据目录；已存在则报错。"""
    for p in (_cwd() / "GEZHI.md", config.DATA_DIR / "GEZHI.md"):
        if p.exists():
            raise FileExistsError(f"已存在：{p}")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(TEMPLATE, encoding="utf-8")
            return p
        except OSError:
            continue
    raise OSError("GEZHI.md 无可写位置（工作目录与数据目录均不可写）")
