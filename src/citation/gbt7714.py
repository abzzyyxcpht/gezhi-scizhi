"""F4 格式管家：参考文献格式化引擎（规则式，零格式幻觉）。

默认 GB/T 7714—2015 顺序编码制，支持 APA 一键切换。
作者姓名按国标规则处理：姓全称在前、名取首字母大写；超过 3 位取前 3 位加 et al.。
"""
from __future__ import annotations

from typing import Any

from ..orchestrator.engine import TaskContext, action

_STYLES = {"gbt7714", "apa"}


def _author_gbt(name: str) -> str:
    """“John M. Smith” → “Smith J M”。"""
    parts = name.replace(",", " ").split()
    if not parts:
        return "佚名"
    if len(parts) == 1:
        return parts[0]
    family, given = parts[-1], parts[:-1]
    initials = " ".join(p[0].upper() for p in given if p)
    return f"{family} {initials}".strip()


def _author_apa(name: str) -> str:
    """“John M. Smith” → “Smith, J. M.”。"""
    parts = name.replace(",", " ").split()
    if not parts:
        return "佚名"
    if len(parts) == 1:
        return parts[0]
    family, given = parts[-1], parts[:-1]
    initials = " ".join(f"{p[0].upper()}." for p in given if p)
    return f"{family}, {initials}".strip()


def format_entry(work: dict[str, Any], style: str = "gbt7714") -> str:
    """格式化单条文献。"""
    if style not in _STYLES:
        raise ValueError(f"不支持的引用格式：{style}（支持 {sorted(_STYLES)}）")
    authors = [a for a in (work.get("authors") or []) if a]
    title = (work.get("title") or "未名文献").rstrip(".")
    year = work.get("year") or "出版年不详"
    venue = work.get("venue") or ""
    doi = work.get("doi") or ""

    if style == "gbt7714":
        names = [_author_gbt(a) for a in authors[:3]]
        if len(authors) > 3:
            names.append("et al.")
        author_str = ", ".join(names) if names else "佚名"
        entry = f"{author_str.rstrip('.')}. {title}[J]."
        if venue:
            entry += f" {venue},"
        entry += f" {year}"
        vol = work.get("volume") or ""
        issue = work.get("issue") or ""
        pages = work.get("pages") or ""
        if vol:
            entry += f", {vol}" + (f"({issue})" if issue else "")
        if pages:
            entry += f": {pages}"
        entry += "."
        if doi:
            entry += f" DOI: {doi}."
        return entry

    # APA 7
    names = [_author_apa(a) for a in authors[:20]]
    if len(names) > 1:
        author_str = ", ".join(names[:-1]) + f", & {names[-1]}"
    else:
        author_str = names[0] if names else "佚名"
    entry = f"{author_str} ({year}). {title}."
    if venue:
        entry += f" *{venue}*"
        vol = work.get("volume") or ""
        if vol:
            entry += f", *{vol}*" + (f"({work.get('issue')})" if work.get("issue") else "")
        pages = work.get("pages") or ""
        if pages:
            entry += f", {pages}"
        entry += "."
    if doi:
        entry += f" https://doi.org/{doi}"
    return entry


def check_entry(work: dict[str, Any]) -> list[str]:
    """校对：列出元数据缺项（用于前端提示补全）。"""
    issues = []
    if not work.get("authors"):
        issues.append("缺作者")
    if not work.get("year"):
        issues.append("缺出版年")
    if not work.get("venue"):
        issues.append("缺期刊/来源名")
    if not work.get("pages"):
        issues.append("缺页码（建议用 DOI 反查补全）")
    if not work.get("doi"):
        issues.append("缺 DOI")
    return issues


@action("format_references")
def format_references(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    """工作流节点：对文献池批量格式化，默认 GB/T 7714。"""
    pool = ctx.artifacts.get("lit_filter", {}).get("pool", [])
    style = ctx.params.get("cite_style", "gbt7714")
    limit = int(params.get("limit", 20))
    entries = []
    for i, w in enumerate(pool[:limit], 1):
        entries.append({
            "text": f"[{i}] {format_entry(w, style)}" if style == "gbt7714"
                    else format_entry(w, style),
            "issues": check_entry(w),
            "source_id": w.get("id", ""),
        })
    ctx.emit("node", node="citation",
             message=f"已按 {style} 排版 {len(entries)} 条参考文献")
    return {"style": style, "entries": entries}
