"""内置插件：文献池 BibTeX 导出（LaTeX / Zotero 工作流兼容）。

命令：/bib [--limit N]      动作：export_bibtex（workflows/*.yaml 可引用）
BibTeX key = 首作者姓 + 年份（去非字母数字），同 key 自动加后缀去重。
"""
from __future__ import annotations

import re
from typing import Any

from src.orchestrator.engine import TaskContext, action


def _key(w: dict[str, Any], i: int) -> str:
    authors = w.get("authors") or []
    family = re.split(r"[\s,]+", str(authors[0]))[-1] if authors else "anon"
    year = w.get("year") or "nd"
    base = re.sub(r"[^A-Za-z0-9]+", "", f"{family}{year}") or f"entry{i}"
    return base + (chr(97 + i % 26) if i > 25 else "")


def _bibtex(w: dict[str, Any], i: int) -> str:
    fields = [
        f"  title = {{{w.get('title') or 'Untitled'}}},",
        f"  author = {{{' and '.join(w.get('authors') or []) or 'Unknown'}}},",
        f"  year = {{{w.get('year') or 'n.d.'}}},",
    ]
    if w.get("venue"):
        fields.append(f"  journal = {{{w['venue']}}},")
    if w.get("volume"):
        fields.append(f"  volume = {{{w['volume']}}},")
    if w.get("issue"):
        fields.append(f"  number = {{{w['issue']}}},")
    if w.get("pages"):
        fields.append(f"  pages = {{{w['pages']}}},")
    if w.get("doi"):
        fields.append(f"  doi = {{{w['doi']}}},")
    if w.get("url"):
        fields.append(f"  url = {{{w['url']}}},")
    return f"@article{{{_key(w, i)},\n" + "\n".join(fields) + "\n}"


def _render(pool: list[dict[str, Any]], limit: int) -> str:
    return "\n\n".join(_bibtex(w, i) for i, w in enumerate(pool[:limit], 0))


def run(args: str, ctx: TaskContext | None) -> str:
    if ctx is None:
        return "需要先打开任务：/open <ID|序号>，再 /bib 导出"
    limit = 20
    m = re.search(r"--limit\s+(\d+)", args or "")
    if m:
        limit = max(1, int(m.group(1)))
    pool = ctx.artifacts.get("lit_filter", {}).get("pool", [])
    if not pool:
        return "文献池为空（任务尚未跑到 lit_filter 节点或池为空）"
    head = f"% {len(pool)} 篇，导出前 {min(limit, len(pool))} 条（Gezhi SciZhi /bib）\n"
    return head + _render(pool, limit)


def export_bibtex(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    """工作流动作：把文献池渲染为 BibTeX 文本放进产物。"""
    pool = ctx.artifacts.get("lit_filter", {}).get("pool", [])
    limit = int(params.get("limit", len(pool)))
    text = _render(pool, limit)
    ctx.emit("node", node="export_bibtex", message=f"已生成 BibTeX {min(limit, len(pool))} 条")
    return {"bibtex": text, "count": min(limit, len(pool))}


PLUGIN = {
    "name": "bibtex",
    "version": "1.0",
    "description": "文献池 → BibTeX(.bib)，兼容 LaTeX / Zotero 工作流",
    "commands": {"bib": {"help": "导出当前任务文献池为 BibTeX（--limit N，默认 20）", "run": run}},
    "actions": {"export_bibtex": export_bibtex},
}
