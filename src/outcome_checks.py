"""Deterministic acceptance checks for delivered research outcomes.

A research deliverable is only "done" when someone can check it. A model saying
"the references are formatted" is a claim; a checklist item that names the
artifact, quotes its recorded digest, and states which fact made it pass is
evidence. This module only ever reports facts that already exist in the task
record:

- **artifact_present** — a named artifact key exists in the task artifacts;
- **material_integrity** — a material's recorded sha256 still matches the file
  on disk (changed or missing becomes *stale*, never silently *satisfied*);
- **deficit_free** — the research package recorded no known gaps;
- **citation_format** — the reference list is non-empty and every entry carries
  the fields the chosen style needs;
- **manual_review** — a human confirmation is required; a model statement can
  never satisfy it.

States are deliberately narrow:

``satisfied``   the fact was observed and every check passed;
``failed``      the fact was observed and a check did not hold;
``stale``       the material changed after it was recorded, so the earlier
                verdict no longer applies;
``pending``     waiting for a human confirmation that has not happened yet;
``unknown``     the task record does not contain the fact at all.

Nothing here contacts a model or rewrites the task record.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "outcome-checks.v2"

# States a check may report. "satisfied" is the only passing one.
STATES = ("satisfied", "failed", "stale", "pending", "unknown")

# Artifact keys the research pipeline is expected to produce, with the label
# used in the acceptance checklist.
ARTIFACT_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("lit_filter", "文献池", "检索筛选后保留的文献清单必须存在"),
    ("citation", "参考文献", "参考文献清单必须存在"),
    ("digest", "摘要整理稿", "摘要整理稿必须存在"),
    ("review_frame", "综述框架", "综述框架必须存在"),
)

def _text(value: Any) -> str:
    return str(value or "").strip()


def _digest(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def _material_rows(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    pool = (artifacts.get("lit_filter") or {}).get("pool") or []
    rows = []
    for work in pool:
        if not isinstance(work, dict):
            continue
        digest = _text(work.get("sha256"))
        # Local materials record their file under "url" (absolute) or "path".
        location = _text(work.get("path")) or _text(work.get("url"))
        rows.append({"id": _text(work.get("id")), "title": _text(work.get("title")),
                     "sha256": digest, "path": location})
    return rows


def _resolve_material(location: str, materials_root: Path | None) -> Path:
    candidate = Path(location)
    if candidate.is_absolute():
        return candidate
    return (materials_root / candidate) if materials_root else candidate


def _citations(artifacts: dict[str, Any]) -> list[Any]:
    citation = artifacts.get("citation")
    entries = citation.get("entries") if isinstance(citation, dict) else None
    # Invalid rows must remain visible to the check, not disappear beside a valid row.
    return entries if isinstance(entries, list) else []


def _check_artifact(key: str, label: str, criterion: str,
                    artifacts: dict[str, Any]) -> dict[str, Any]:
    present = bool(artifacts.get(key))
    evidence = [{"artifact": key}] if present else []
    if key == "digest":
        extract = artifacts.get("extract")
        if isinstance(extract, dict) and _text(extract.get("digest_md")):
            present = True
            evidence = [{"artifact": "extract", "field": "digest_md"}]
    return {
        "id": f"artifact:{key}",
        "title": label,
        "criterion": criterion,
        "kind": "artifact_present",
        "state": "satisfied" if present else "failed",
        "evidence": evidence,
        "detail": ("已记录该产物" if present else "任务记录中没有该产物"),
    }


def _observe_materials(artifacts: dict[str, Any], materials_root: Path | None
                       ) -> list[dict[str, Any]]:
    rows = _material_rows(artifacts)
    for row in rows:
        row["local"] = bool(row["path"]) and not row["path"].lower().startswith(("http://", "https://"))
        row["current_sha256"] = (_digest(_resolve_material(row["path"], materials_root))
                                 if row["local"] else None)
    return rows


def _revision(artifacts: dict[str, Any], manifest: dict[str, Any],
              rows: list[dict[str, Any]], context: dict[str, Any] | None) -> str:
    # Review metadata, render caches, settings and polling timestamps are not outcomes.
    facts = {"schema_version": SCHEMA_VERSION,
             "artifacts": {key: value for key, value in artifacts.items() if not key.startswith("_")},
             "deficits": manifest.get("deficits"), "materials": rows, "context": context or {}}
    encoded = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _check_material_integrity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    recorded = [row for row in rows if row["sha256"]]
    if not rows:
        return {"id": "material:integrity", "title": "材料完整性",
                "criterion": "有本地材料的，记录的哈希必须仍然一致",
                "kind": "material_integrity", "state": "unknown",
                "evidence": [], "detail": "没有带哈希记录的本地材料"}
    if not recorded:
        return {"id": "material:integrity", "title": "材料完整性",
                "criterion": "有本地材料的，记录的哈希必须仍然一致",
                "kind": "material_integrity", "state": "unknown",
                "evidence": [], "detail": "材料没有记录哈希，无法核对"}
    stale, missing, checked = [], [], []
    uncheckable = [row for row in rows if row["local"] and not row["sha256"]]
    for row in recorded:
        if not row["local"]:
            uncheckable.append(row)
            continue
        current = row["current_sha256"]
        if current is None:
            missing.append(row)
        elif current != row["sha256"]:
            stale.append(row)
        else:
            checked.append(row)
    if stale or missing:
        evidence = []
        for row in (stale + missing):
            evidence.append({"material": row["id"], "recorded_sha256": row["sha256"],
                             "current_sha256": row["current_sha256"]})
        return {"id": "material:integrity", "title": "材料完整性",
                "criterion": "有本地材料的，记录的哈希必须仍然一致",
                "kind": "material_integrity", "state": "stale",
                "evidence": evidence,
                "detail": (f"{len(stale)} 份材料内容已变化，{len(missing)} 份材料已不可访问；"
                           "原结论需要复核")}
    if uncheckable or not checked:
        return {"id": "material:integrity", "title": "材料完整性",
                "criterion": "有本地材料的，记录的哈希必须仍然一致",
                "kind": "material_integrity", "state": "unknown",
                "evidence": [{"material": row["id"], "sha256": row["sha256"]} for row in checked],
                "detail": f"{len(checked)} 份材料哈希一致；另有材料缺少哈希或本地文件位置，无法完整核对"}
    return {"id": "material:integrity", "title": "材料完整性",
            "criterion": "有本地材料的，记录的哈希必须仍然一致",
            "kind": "material_integrity", "state": "satisfied",
            "evidence": [{"material": row["id"], "sha256": row["sha256"]} for row in checked],
            "detail": f"{len(checked)} 份材料哈希一致"}


def _check_citations(artifacts: dict[str, Any]) -> dict[str, Any]:
    criterion = "格式化引用有文本与来源，必需元数据无缺项；旧记录仅核对已有字段"
    required_gaps = {"缺作者", "缺出版年", "缺期刊/来源名"}
    optional_gaps = {"缺页码（建议用 DOI 反查补全）", "缺 DOI"}
    entries = _citations(artifacts)
    if not entries:
        return {"id": "citations:format", "title": "参考文献完整性",
                "criterion": criterion,
                "kind": "citation_format", "state": "failed",
                "evidence": [], "detail": "没有参考文献条目"}
    incomplete, unconfirmed, advice = [], [], []
    legacy_count = 0
    pool = (artifacts.get("lit_filter") or {}).get("pool") or []
    sources: dict[str, list[dict[str, Any]]] = {}
    for source in pool:
        if isinstance(source, dict) and isinstance(source.get("id"), str):
            sources.setdefault(source["id"].strip(), []).append(source)
    for entry in entries:
        if not isinstance(entry, dict):
            incomplete.append({"citation": "(无标题)", "message": "参考文献条目必须为对象"})
            continue
        fields = {key: entry[key].strip() if isinstance(entry.get(key), str) else ""
                  for key in ("title", "text", "source_id", "id")}
        label = fields["title"] or fields["source_id"] or fields["id"] or fields["text"][:160] or "(无标题)"
        problems = []
        if "text" in entry:
            # format_references emits text/source_id/issues; earlier exports use id.
            if not fields["text"]:
                problems.append("缺少非空的排版文本")
            source_id = fields["source_id"] or fields["id"]
            if not source_id:
                problems.append("缺少来源标识")
            elif pool:
                matched = sources.get(source_id, [])
                if not matched:
                    problems.append("来源标识不在当前文献池中")
                elif any(not isinstance(source.get("title"), str) or not source["title"].strip()
                         for source in matched):
                    problems.append("当前来源缺少有效的文献标题")
        else:
            # Retain support for historical metadata-only citation records.
            legacy_count += 1
            if not fields["title"]:
                problems.append("缺少文献标题")
        issues = entry.get("issues", [])
        if not isinstance(issues, list):
            unconfirmed.append({"citation": label, "message": "引用缺项记录格式无效，需要确认"})
        else:
            for issue in issues:
                if not isinstance(issue, str) or not issue.strip():
                    unconfirmed.append({"citation": label, "message": "引用缺项记录格式无效，需要确认"})
                elif issue.strip() in required_gaps:
                    problems.append(issue.strip())
                elif issue.strip() in optional_gaps:
                    advice.append({"citation": label, "message": "补全建议：" + issue.strip()})
                else:
                    unconfirmed.append({"citation": label, "message": "待确认的问题：" + issue.strip()})
        if problems:
            incomplete.append({"citation": label, "message": "；".join(problems)})
    if incomplete:
        return {"id": "citations:format", "title": "参考文献完整性",
                "criterion": criterion,
                "kind": "citation_format", "state": "failed",
                "evidence": incomplete + unconfirmed + advice,
                "detail": f"{len(incomplete)} 条参考文献缺少文本、来源标识或必需元数据"}
    if unconfirmed:
        return {"id": "citations:format", "title": "参考文献完整性",
                "criterion": criterion, "kind": "citation_format", "state": "unknown",
                "evidence": unconfirmed + advice,
                "detail": f"{len(unconfirmed)} 项引用问题尚不能确定性质，请核对"}
    detail = f"{len(entries)} 条参考文献按现有记录检查通过"
    if legacy_count:
        detail += f"；其中 {legacy_count} 条旧记录只核对已有字段（标题）"
    if advice:
        detail += f"；另有 {len(advice)} 项补全建议"
    return {"id": "citations:format", "title": "参考文献完整性",
            "criterion": criterion,
            "kind": "citation_format", "state": "satisfied",
            "evidence": [{"citations": len(entries)}] + advice,
            "detail": detail + "；不判断文献真实性"}


def _check_deficits(manifest: dict[str, Any]) -> dict[str, Any]:
    deficits = [row for row in (manifest.get("deficits") or []) if isinstance(row, dict)]
    if not manifest:
        return {"id": "package:deficits", "title": "研究包缺口",
                "criterion": "研究包必须没有已记录的缺口",
                "kind": "deficit_free", "state": "unknown",
                "evidence": [], "detail": "还没有生成研究包清单"}
    if deficits:
        return {"id": "package:deficits", "title": "研究包缺口",
                "criterion": "研究包必须没有已记录的缺口",
                "kind": "deficit_free", "state": "failed",
                "evidence": [{"code": _text(row.get("code")),
                              "message": _text(row.get("message"))} for row in deficits],
                "detail": f"{len(deficits)} 项缺口已记录在研究包清单里"}
    return {"id": "package:deficits", "title": "研究包缺口",
            "criterion": "研究包必须没有已记录的缺口",
            "kind": "deficit_free", "state": "satisfied",
            "evidence": [{"deficits": 0}], "detail": "研究包未记录缺口"}


def _check_manual(confirmations: dict[str, Any], revision: str) -> dict[str, Any]:
    """A human confirmation is the only thing that can close this item."""
    record = confirmations.get("outcome_review")
    if not isinstance(record, dict) or not record:
        return {"id": "review:manual", "title": "人工复核",
                "criterion": "结论必须由人工复核确认后才能算完成",
                "kind": "manual_review", "state": "pending",
                "evidence": [], "detail": "尚未记录人工复核"}
    missing = [field for field in ("by", "note", "at") if not _text(record.get(field))]
    if missing:
        return {"id": "review:manual", "title": "人工复核",
                "criterion": "结论必须由人工复核确认后才能算完成",
                "kind": "manual_review", "state": "pending",
                "evidence": [], "detail": "人工复核记录缺少：" + "、".join(
                    {"by": "复核人", "note": "复核说明", "at": "记录时间"}[field]
                    for field in missing)}
    evidence = [{key: _text(record.get(key)) for key in ("by", "at", "revision", "decision", "note")}]
    if not _text(record.get("revision")) or record.get("decision") not in {"approved", "changes_requested"}:
        return {"id": "review:manual", "title": "人工复核",
                "criterion": "结论必须由人工复核确认后才能算完成",
                "kind": "manual_review", "state": "pending", "evidence": evidence,
                "detail": "旧复核记录未绑定成果版本或明确结论，请重新复核"}
    if record["revision"] != revision:
        return {"id": "review:manual", "title": "人工复核",
                "criterion": "结论必须由人工复核确认后才能算完成",
                "kind": "manual_review", "state": "stale", "evidence": evidence,
                "detail": "成果已在复核后发生变化，原复核不适用于当前版本"}
    if record["decision"] == "changes_requested":
        return {"id": "review:manual", "title": "人工复核",
                "criterion": "结论必须由人工复核确认后才能算完成",
                "kind": "manual_review", "state": "failed", "evidence": evidence,
                "detail": "人工复核要求修改，尚未通过"}
    return {"id": "review:manual", "title": "人工复核",
            "criterion": "结论必须由人工复核确认后才能算完成",
            "kind": "manual_review", "state": "satisfied",
            "evidence": evidence,
            "detail": "当前成果版本已有人工通过记录"}


def build_checklist(artifacts: dict[str, Any], manifest: dict[str, Any] | None = None,
                    confirmations: dict[str, Any] | None = None,
                    materials_root: Path | None = None, *,
                    context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Deterministic acceptance checklist for one task's delivered outcomes."""
    return _inspect(artifacts, manifest, confirmations, materials_root, context)[1]


def _inspect(artifacts, manifest, confirmations, materials_root, context):
    artifacts = artifacts or {}
    manifest = manifest or {}
    confirmations = confirmations or {}
    rows = _observe_materials(artifacts, materials_root)
    revision = _revision(artifacts, manifest, rows, context)
    items = [_check_artifact(key, label, criterion, artifacts)
             for key, label, criterion in ARTIFACT_CHECKS]
    items.append(_check_material_integrity(rows))
    items.append(_check_citations(artifacts))
    items.append(_check_deficits(manifest))
    items.append(_check_manual(confirmations, revision))
    return revision, items


def evaluate(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate checklist items into one verdict without softening any state."""
    counts = {state: 0 for state in STATES}
    for item in items:
        state = item.get("state")
        counts[state if state in counts else "unknown"] += 1
    total = len(items)
    satisfied = counts["satisfied"]
    if total and satisfied == total:
        verdict = "all_satisfied"
    elif counts["failed"] or counts["stale"]:
        verdict = "needs_review"
    elif counts["pending"] or counts["unknown"]:
        verdict = "incomplete"
    else:
        verdict = "unknown"
    return {
        "schema_version": SCHEMA_VERSION,
        "items": items,
        "counts": counts,
        "total": total,
        "satisfied": satisfied,
        "verdict": verdict,
        "boundary": "本检查只依据任务记录中已存在的事实判定；未通过不等于结论错误，"
                    "只表示现有记录不足以支持「已完成」。",
    }


def run(artifacts: dict[str, Any], manifest: dict[str, Any] | None = None,
        confirmations: dict[str, Any] | None = None,
        materials_root: Path | None = None, *,
        context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build and evaluate the checklist in one call."""
    revision, items = _inspect(artifacts, manifest, confirmations, materials_root, context)
    return {**evaluate(items), "revision": revision}
