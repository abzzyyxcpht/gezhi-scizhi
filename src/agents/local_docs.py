# -*- coding: utf-8 -*-
"""本地文献材料入池：把用户显式给出的 PDF / 文本材料变成证据池条目。

这是"PDF 全文证据链"的入池口：``pdf_text`` 提取全文 → 构造带
``full_text`` 的池条目 → 并入文献池 → 下游 extract 节点按全文抽取
（``material_scope=fulltext``，出处逐字核对以全文为准）。

边界（如实告知，不夸大）：
- 只读用户显式列出的路径，不联网、不遍历目录；
- 题名/作者/年份取自 PDF 元数据，缺失即留空或按文件名兜底，不编造；
- ``score`` 留 None：本地材料的相关性由用户断言，不参与检索评分，
  审计器会把"缺相关性分数"作为中等问题如实上报；
- 单文件失败不阻断整批，结果逐条报告。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .. import pdf_text
from ..orchestrator.engine import TaskContext, action

MAX_DOCS = 20                       # 单次入池上限（与附件 20 项预算一致）
DOC_TEXT_CHARS = 20000              # 单篇全文入池字符上限
TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
_SOURCE_NOTE = "用户提供的本地全文材料（未参与检索评分）"


def _entry_id(sha: str) -> str:
    return f"local:{sha[:16]}"


def _from_pdf(path: Path) -> dict[str, Any] | None:
    """PDF → 池条目；提取失败返回 None（调用方记录原因）。"""
    info = pdf_text.extract_file(path, max_chars=DOC_TEXT_CHARS)
    if not info.get("ok"):
        return None
    meta = info.get("metadata") or {}
    authors = [a.strip() for a in str(meta.get("author") or "").replace(";", ",").split(",")
               if a.strip()][:8]
    return {
        "id": _entry_id(info["sha256"]),
        "title": (meta.get("title") or "").strip() or path.stem,
        "authors": authors,
        "year": meta.get("year"),
        "venue": "",
        "doi": "",
        "url": str(path),
        "abstract": info["text"][:800],
        "full_text": info["text"],
        "score": None,
        "decision": "保留",
        "reason": _SOURCE_NOTE,
        "source": "local_pdf",
        "source_type": "local_pdf",
        "sha256": info["sha256"],
        "pages": info.get("pages", 0),
        "extract_backend": info.get("method", ""),
    }


def _from_text(path: Path) -> dict[str, Any] | None:
    """UTF-8 文本/Markdown → 池条目；读取失败返回 None。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    text = raw.decode("utf-8-sig", "replace")[:DOC_TEXT_CHARS]
    if not text.strip():
        return None
    sha = hashlib.sha256(raw).hexdigest()
    return {
        "id": _entry_id(sha),
        "title": path.stem,
        "authors": [],
        "year": None,
        "venue": "",
        "doi": "",
        "url": str(path),
        "abstract": text[:800],
        "full_text": text,
        "score": None,
        "decision": "保留",
        "reason": _SOURCE_NOTE,
        "source": "local_text",
        "source_type": "local_text",
        "sha256": sha,
        "pages": None,
        "extract_backend": "utf8",
    }


def load_entries(paths: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """读取本地材料列表，返回 (入池条目, 跳过明细)。绝不抛出。"""
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list):
        return [], [{"path": "", "reason": "paths 必须是路径列表"}]
    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in paths[:MAX_DOCS]:
        raw_path = str(value or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        try:
            if not path.is_file():
                skipped.append({"path": raw_path, "reason": "文件不存在"})
                continue
            if path.stat().st_size > pdf_text.MAX_PDF_BYTES:
                skipped.append({"path": raw_path, "reason": "超过 32 MiB 上限"})
                continue
        except OSError as exc:
            skipped.append({"path": raw_path, "reason": f"无法读取：{exc}"})
            continue
        suffix = path.suffix.casefold()
        if suffix == ".pdf":
            entry = _from_pdf(path)
            unsupported = "提取失败（扫描件/加密 PDF 或损坏文件）"
        elif suffix in TEXT_SUFFIXES:
            entry = _from_text(path)
            unsupported = "文本为空或不是有效的 UTF-8"
        else:
            entry, unsupported = None, "格式不支持（仅 PDF 与 UTF-8 文本/Markdown）"
        if entry is None:
            skipped.append({"path": raw_path, "reason": unsupported})
            continue
        if entry["id"] in seen:
            skipped.append({"path": raw_path, "reason": "与本批材料重复"})
            continue
        seen.add(entry["id"])
        entries.append(entry)
    return entries, skipped


def merge_into_pool(pool: list[dict[str, Any]],
                    entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """并入文献池：按 id/DOI 去重，本地条目排在检索条目之后。"""
    merged = list(pool or [])
    seen = {(w.get("doi") or w.get("id") or "").lower() for w in merged}
    for entry in entries:
        key = entry["id"].lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)
    return merged


@action("ingest_local_docs")
def ingest_local_docs(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    """工作流动作：把本地文献材料并入当前任务的文献池。

    params: ``{"paths": ["...", ...]}``。产物写
    ``artifacts["lit_filter"]["pool"]``（不清空检索条目），ingested /
    skipped 明细随产物返回，供审计与界面展示。
    """
    entries, skipped = load_entries(params.get("paths"))
    if not entries and not skipped:
        raise ValueError("ingest_local_docs 需要 paths 列表参数")
    lit = ctx.artifacts.setdefault("lit_filter", {})
    lit["pool"] = merge_into_pool(list(lit.get("pool") or []), entries)
    for entry in entries:
        pages = entry.get("pages")
        ctx.emit("node", node="ingest_local_docs",
                 message=f"本地材料入池：{str(entry.get('title') or '')[:40]}"
                         f"（{pages if pages else '—'} 页 · "
                         f"{entry.get('extract_backend') or 'text'}）")
    result = {
        "ingested": [{k: entry.get(k) for k in
                      ("id", "title", "year", "source", "pages", "extract_backend")}
                     for entry in entries],
        "skipped": skipped,
        "pool_size": len(lit.get("pool") or []),
        "full_text_chars": sum(len(str(e.get("full_text") or "")) for e in entries),
        "summary": f"本地材料入池 {len(entries)} 篇，跳过 {len(skipped)} 个",
    }
    ctx.emit("node", node="ingest_local_docs", message=result["summary"])
    return result
