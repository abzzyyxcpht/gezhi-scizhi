"""Offline evidence inventory and traceability checks, not an entailment model.

Accepts pipeline artifacts or a flattened workspace/service view. Only literal
abstract spans are promoted to evidence excerpts; model paraphrases stay unverified.
No network, model calls, filesystem writes, or mutation of caller-owned artifacts.
"""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections import Counter
from typing import Any

SCHEMA_VERSION = "1.0"
_DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
_INSTRUCTION = re.compile(
    r"ignore\s+(?:all\s+|previous\s+|prior\s+)*(?:instructions|rules)|"
    r"(?:system|developer)\s*(?:prompt|message)\s*:|"
    r"忽略.{0,10}(?:指令|提示词|规则)|泄露.{0,8}(?:密钥|密码)|"
    r"<\|(?:im_start|system)\|>", re.I)
_HINTS = {
    "method": ("we propose", "we present", "we introduce", "method", "approach",
               "framework", "algorithm", "we design", "提出", "方法", "构建"),
    "findings": ("results show", "experiments show", "outperform", "achieves",
                 "improve", "accuracy", "demonstrate", "结果表明", "实验表明", "发现"),
    "limitations": ("limitation", "future work", "limited to", "局限", "限制", "未来工作"),
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _norm(value: Any) -> str:
    # Keep internal punctuation: matching must never fall back to substring matching.
    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


def normalize_doi(value: Any) -> str:
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", _text(value), flags=re.I)
    return text.casefold() if _DOI.fullmatch(text) else ""


def instruction_like(text: str) -> bool:
    """Conservative flag for manual review, not a complete prompt-injection detector."""
    return bool(_INSTRUCTION.search(text))


def quote_provenance(abstract: str, quote: str, field: str = "abstract") -> dict[str, Any]:
    """A located quote proves textual occurrence only, never scientific support."""
    start = abstract.find(quote) if quote else -1
    if start < 0:
        return {"status": "unverified", "field": field, "quote": quote,
                "start": None, "end": None}
    return {"status": "exact_quote", "field": field, "quote": quote,
            "start": start, "end": start + len(quote)}


def _sentences(abstract: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|(?<=[。！？])", abstract) if s.strip()]


def _reference_keys(ref: Any) -> list[str]:
    if isinstance(ref, dict):
        keys = []
        for field in ("paper_id", "id"):
            if _text(ref.get(field)):
                keys.append("id:" + _norm(ref[field]))
                if normalize_doi(ref[field]):
                    keys.append("doi:" + normalize_doi(ref[field]))
        if normalize_doi(ref.get("doi")):
            keys.append("doi:" + normalize_doi(ref["doi"]))
        if _text(ref.get("title")):
            keys.append("title:" + _norm(ref["title"]))
        return list(dict.fromkeys(keys))
    value = _text(ref)
    if not value:
        return []
    doi = normalize_doi(value)
    return ["doi:" + doi] if doi else ["id:" + _norm(value), "title:" + _norm(value)]


def _view(artifacts: dict[str, Any]) -> tuple[list, list, list, dict]:
    lit = _dict(artifacts.get("lit_filter"))
    extract = _dict(artifacts.get("extract")) or _dict(artifacts.get("digest"))
    review = _dict(artifacts.get("review_frame"))
    pool = _rows(lit.get("pool", artifacts.get("pool", [])))
    records = _rows(extract.get("records", artifacts.get("records", [])))
    sections = _rows(review.get("sections", artifacts.get("sections", [])))
    return pool, records, sections, lit


def _inventory(pool: list[dict[str, Any]]) -> tuple[list[dict], dict[str, set[str]], dict[str, str]]:
    groups: dict[str, list[tuple[int, dict]]] = {}
    for index, work in enumerate(pool):
        demo = bool(work.get("demo") or work.get("source_type") == "synthetic_demo")
        doi = normalize_doi(work.get("doi"))
        identity = ("doi:" + doi if doi else "id:" + _text(work.get("id"))
                    if _text(work.get("id")) else "title:" + _norm(work.get("title")) + ":" + str(work.get("year")))
        if not work.get("title") and not work.get("id") and not doi:
            identity = "anonymous:" + str(index)
        groups.setdefault(f"{demo}:{identity}", []).append((index, work))
    matrix, ref_index, abstracts = [], {}, {}
    used_ids: set[str] = set()
    for identity, group in groups.items():
        work = group[0][1]
        abstract = next((_text(w.get("abstract")) for _, w in group if _text(w.get("abstract"))), "")
        demo = bool(work.get("demo") or work.get("source_type") == "synthetic_demo")
        paper_id = _text(work.get("id")) or "paper:" + hashlib.sha256(identity.encode()).hexdigest()[:12]
        if paper_id in used_ids:
            paper_id += ":" + hashlib.sha256(identity.encode()).hexdigest()[:8]
        used_ids.add(paper_id)
        flags = ["instruction_like_content"] if instruction_like(abstract) else []
        if len({_text(w.get("abstract")) for _, w in group if _text(w.get("abstract"))}) > 1:
            flags.append("conflicting_abstracts")
        source_type = "synthetic_demo" if demo else "abstract_only" if abstract else "metadata_only"
        row: dict[str, Any] = {
            "paper_id": paper_id, "title": _text(work.get("title")), "year": work.get("year"),
            "doi": normalize_doi(work.get("doi")) or None,
            "doi_status": "format_only" if normalize_doi(work.get("doi")) else
                          "invalid_format" if work.get("doi") else "missing",
            "source_type": source_type, "demo": demo,
            "source": _text(work.get("source")) or "unknown",
            "url": _text(work.get("url")), "material_scope": "abstract" if abstract else "metadata",
            "eligible_as_evidence": bool(abstract and not demo and not flags),
            "method": [], "findings": [], "limitations": [],
            "provenance": {field: [] for field in _HINTS}, "unverified_extractions": [],
            "flags": flags, "input_indices": [i for i, _ in group],
            "duplicate_count": len(group) - 1,
        }
        if not flags:
            for field, hints in _HINTS.items():
                for sentence in _sentences(abstract):
                    if any(hint in sentence.casefold() for hint in hints):
                        row[field].append(sentence)
                        row["provenance"][field].append(quote_provenance(abstract, sentence))
        matrix.append(row)
        abstracts[paper_id] = abstract
        for _, original in group:
            for key in _reference_keys(original):
                ref_index.setdefault(key, set()).add(paper_id)
        ref_index.setdefault("id:" + _norm(paper_id), set()).add(paper_id)
    return matrix, ref_index, abstracts


def _resolve(ref: Any, index: dict[str, set[str]]) -> tuple[str, list[str]]:
    if isinstance(ref, dict):
        identity_sets = [set().union(*(index.get(k, set()) for k in _reference_keys({field: ref[field]})))
                         for field in ("paper_id", "id", "doi") if _text(ref.get(field))]
        if identity_sets:
            # A supplied identifier must not silently fall back to a coincident title.
            if not all(identity_sets):
                return "unresolved", []
            intersection = set.intersection(*identity_sets)
            matches = intersection or set.union(*identity_sets)
            ids = sorted(matches)
            return ("resolved" if len(intersection) == 1 else "ambiguous"), ids
    keys = _reference_keys(ref)
    if not isinstance(ref, dict) and keys and keys[0].startswith("id:") and index.get(keys[0]):
        keys = keys[:1]
    matches = set().union(*(index.get(k, set()) for k in keys))
    ids = sorted(matches)
    return ("resolved" if len(ids) == 1 else "ambiguous" if ids else "unresolved"), ids


def analyze_artifacts(artifacts: dict[str, Any], topic: str = "") -> dict[str, Any]:
    """Return a deterministic evidence inventory with explicit verification limits."""
    art = _dict(artifacts)
    pool, records, sections, lit = _view(art)
    matrix, index, abstracts = _inventory(pool or records)
    by_id = {r["paper_id"]: r for r in matrix}
    unlinked = []
    for record in records:
        status, ids = _resolve(record, index)
        if status != "resolved":
            unlinked.append({"id": _text(record.get("id")), "title": _text(record.get("title")),
                             "status": status})
            continue
        row = by_id[ids[0]]
        for field, output in (("method", "method"), ("innovation", "findings"),
                              ("conclusion", "findings"), ("limitation", "limitations")):
            value = _text(record.get(field))
            if not value:
                continue
            span = quote_provenance(abstracts[ids[0]], value)
            if span["status"] == "exact_quote" and not row["flags"]:
                if value not in row[output]:
                    row[output].append(value)
                    row["provenance"][output].append(span)
            else:
                row["unverified_extractions"].append({"field": field, "text": value,
                                                       "status": "unverified"})

    claims = [(f"claims[{i}]", c) for i, c in enumerate(_rows(art.get("claims")))]
    claims += [(f"sections[{i}]", {"text": s.get("points"), "refs": s.get("refs", []),
                                   "title": s.get("title")}) for i, s in enumerate(sections)]
    reference_items, claim_items = [], []
    for location, claim in claims:
        raw_refs = claim.get("refs", claim.get("references", []))
        refs = raw_refs if isinstance(raw_refs, list) else [raw_refs] if raw_refs else []
        resolved: set[str] = set()
        unresolved = 0
        for ref in refs:
            status, ids = _resolve(ref, index)
            duplicate = status == "resolved" and ids[0] in resolved
            reference_items.append({"location": location, "reference": ref, "status": status,
                                    "paper_ids": ids, "duplicate": duplicate})
            if status == "resolved":
                resolved.update(ids)
            else:
                unresolved += 1
        quote = _text(claim.get("quote"))
        spans = [{"paper_id": pid, **quote_provenance(abstracts[pid], quote)}
                 for pid in sorted(resolved) if quote and not by_id[pid]["flags"]]
        spans = [s for s in spans if s["status"] == "exact_quote"]
        real = [pid for pid in sorted(resolved) if by_id[pid]["eligible_as_evidence"]]
        claim_items.append({
            "location": location, "text": _text(claim.get("text") or claim.get("claim")),
            "input_label": _text(claim.get("label") or claim.get("status")),
            "citation_status": "missing" if not refs else "partial" if resolved and unresolved else
                               "unresolved" if unresolved else "resolved",
            "paper_ids": sorted(resolved), "eligible_paper_ids": real,
            "quote_status": "located" if spans else "not_located" if quote else "not_provided",
            "quote_spans": spans, "support_status": "unverified" if real else "insufficient_evidence",
            "reason": "仅核对引用身份和原文位置；未判断论断是否被支持。",
        })

    search = _dict(lit.get("search_metadata")) or _dict(art.get("search_metadata"))
    raw_queries = search.get("queries", [search["query"]] if search.get("query") else [])
    queries = [_text(q) for q in raw_queries if _text(q)] if isinstance(raw_queries, list) else []
    rounds = _rows(lit.get("rounds", art.get("rounds", [])))
    years = sorted({int(r["year"]) for r in matrix if str(r.get("year") or "").isdigit()})
    missing = []
    if not queries:
        missing.append("search_queries_not_recorded")
    if not search.get("searched_at"):
        missing.append("search_date_not_recorded")
    if not claims and (art.get("digest_md") or art.get("outline_md")):
        missing.append("structured_claims_not_available")
    coverage = {
        "scope": "observed_artifacts", "search_complete": None,
        "input_papers": len(pool or records), "unique_papers": len(matrix),
        "duplicate_papers": sum(r["duplicate_count"] for r in matrix),
        "real_papers": sum(not r["demo"] for r in matrix),
        "demo_papers": sum(r["demo"] for r in matrix),
        "with_abstract": sum(bool(abstracts[r["paper_id"]]) for r in matrix),
        "eligible_papers": sum(r["eligible_as_evidence"] for r in matrix),
        "unlinked_extractions": len(unlinked),
        "real_papers_definition": "non_demo_records_not_identity_verified",
        "observed_sources": dict(sorted(Counter(r["source"] for r in matrix).items())),
        "year_span": [years[0], years[-1]] if years else None,
        "queries": queries, "searched_at": search.get("searched_at"),
        "filters": _dict(search.get("filters")), "rounds": rounds,
        "missing_metadata": missing,
    }
    gaps: list[dict[str, Any]] = []
    def gap(kind: str, detail: str, paper_ids: list[str] | None = None) -> None:
        gaps.append({"kind": kind, "status": "needs_verification", "detail": detail,
                     "paper_ids": paper_ids or []})
    if not coverage["eligible_papers"]:
        gap("insufficient_evidence", "当前材料不足以支持科研结论；未检索到证据不能证明创新。")
    for record in unlinked:
        gap("unlinked_extraction", f"抽取记录 {record['id'] or record['title'] or '未命名'} "
            f"未唯一匹配来源（{record['status']}），内容未当作已定位原文。")
    for row in matrix:
        ids = [row["paper_id"]]
        if row["source_type"] == "metadata_only":
            gap("missing_abstract", "缺少摘要，应取得可阅读材料后再抽取。", ids)
        if row["demo"]:
            gap("synthetic_demo", "演示样例不能作为真实科研证据。", ids)
        if row["flags"]:
            gap("source_requires_review", "材料含指令式文本或冲突版本，应人工核对来源。", ids)
        if not row["limitations"] and not row["demo"]:
            gap("limitations_not_reported", "现有摘要未定位局限；不能解释为研究没有局限。", ids)
        if row["unverified_extractions"]:
            gap("unverified_extraction", "既有抽取内容未在摘要中逐字定位，仍需核对。", ids)
    for claim in claim_items:
        if claim["citation_status"] != "resolved":
            gap("unanchored_claim", f"{claim['location']} 的引用缺失、歧义或不在文献池中。", claim["paper_ids"])
        if claim["input_label"].casefold() in {"contradiction", "contradicts", "refutes", "反驳", "矛盾"}:
            gap("conflict_requires_review", "输入中的冲突标签尚未核实，应比较原文、数据集及适用条件。", claim["paper_ids"])
    if missing:
        gap("search_coverage_unknown", "检索或结构化材料留痕不完整；无法评估领域覆盖率。")
    next_steps = ["核对论断对应的原文片段与适用条件；引用存在不等于论断受到支持。",
                  "记录数据库、完整查询、日期、过滤条件及失败情况后补充检索。",
                  "取得全文后核对数据集、基线、评价指标、实验设置与局限。"]
    if any(g["kind"] == "conflict_requires_review" for g in gaps):
        next_steps.insert(0, "对疑似冲突逐条比较研究对象与实验条件，暂不裁定支持或反驳。")
    return {
        "schema_version": SCHEMA_VERSION, "topic": topic or _text(art.get("topic")),
        "evidence_matrix": matrix, "coverage": coverage,
        "reference_audit": {"items": reference_items,
                            "resolved_count": sum(r["status"] == "resolved" for r in reference_items),
                            "unresolved_count": sum(r["status"] == "unresolved" for r in reference_items),
                            "ambiguous_count": sum(r["status"] == "ambiguous" for r in reference_items),
                            "duplicate_count": sum(r["duplicate"] for r in reference_items)},
        "claim_audit": {"items": claim_items, "supported_count": 0,
                        "assessment": "traceability_only"},
        "research_gaps": gaps, "next_steps": next_steps,
        "limitations": ["本报告只检查已提供材料，不进行外部检索或全文阅读。",
                        "非演示记录未经过外部真实性验证；方法、发现与局限的自动归类仅依据原文线索。",
                        "DOI 格式通过不表示已核实存在；原文定位不等于蕴含、复现或同行评审验证。",
                        "证据缺口是待核对事项，不能证明研究创新或领域不存在相关工作。",
                        "指令式文本检测仅用于提示人工复核，不能保证识别所有提示注入。"],
    }


def _md(value: Any) -> str:
    text = html.escape(str(value if value is not None else "—"))
    return re.sub(r"([\\`*_[\]|])", r"\\\1", text).replace("\n", " ").replace("\r", " ")


def render_report(analysis: dict[str, Any]) -> str:
    """Render the same conservative assessment for export without model generation."""
    cov = analysis["coverage"]
    lines = [f"# 科研证据审计：{_md(analysis.get('topic') or '未命名课题')}", "",
             "本报告检查材料来源与引用可追溯性，未验证论断蕴含或实验可复现性。", "",
             f"文献 {cov['unique_papers']} 篇；演示 {cov['demo_papers']} 篇；重复记录 {cov['duplicate_papers']} 条。",
             "检索覆盖范围仅限当前产物，未检索到文献不能证明创新。", "",
             f"检索记录日期：{_md(cov.get('searched_at') or '未记录')}；"
             f"查询：{_md('；'.join(cov.get('queries', [])) or '未记录')}。",
             f"观察到的来源：{_md(cov.get('observed_sources', {}))}；"
             f"过滤条件：{_md(cov.get('filters', {}))}。", "",
             "## 证据矩阵", "", "| 文献 ID | 题名 | 年份 | 材料 | 方法原文 | 发现原文 | 局限原文 |",
             "|---|---|---|---|---|---|---|"]
    for row in analysis["evidence_matrix"]:
        cells = [row["paper_id"], row["title"], row["year"], row["source_type"],
                 "；".join(row["method"]) or "未定位", "；".join(row["findings"]) or "未定位",
                 "；".join(row["limitations"]) or "未定位"]
        lines.append("| " + " | ".join(_md(c) for c in cells) + " |")
    lines += ["", "## 原文定位与未核对内容", ""]
    for row in analysis["evidence_matrix"]:
        lines.append(f"- {_md(row['paper_id'])}：DOI {_md(row['doi'])}（{row['doi_status']}）；"
                     f"可作待核查证据材料：{'是' if row['eligible_as_evidence'] else '否'}。")
        for field, spans in row["provenance"].items():
            for span in spans:
                lines.append(f"  - {field}，abstract[{span['start']}:{span['end']}]：{_md(span['quote'])}")
        for pending in row["unverified_extractions"]:
            lines.append(f"  - unverified：{_md(pending['text'])}")
    lines += ["", "## 引用与论断", ""]
    for ref in analysis["reference_audit"]["items"]:
        lines.append(f"- {_md(ref['location'])}：{_md(ref['reference'])} → {ref['status']}"
                     + ("（重复引用）" if ref["duplicate"] else ""))
    for claim in analysis["claim_audit"]["items"]:
        lines.append(f"- {_md(claim['text'])}：引用 {claim['citation_status']}；"
                     f"原文 {claim['quote_status']}；支持关系 {claim['support_status']}。")
    if not analysis["claim_audit"]["items"]:
        lines.append("未提供结构化论断，未进行论断级核查。")
    lines += ["", "## 待核对的证据缺口", ""]
    lines += [f"- {_md(g['detail'])}" for g in analysis["research_gaps"]]
    lines += ["", "## 下一步", ""] + [f"- {_md(s)}" for s in analysis["next_steps"]]
    lines += ["", "## 核查边界", ""] + [f"- {_md(s)}" for s in analysis["limitations"]]
    return "\n".join(lines) + "\n"
