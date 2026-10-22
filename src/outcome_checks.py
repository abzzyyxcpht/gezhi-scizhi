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
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "outcome-checks.v1"

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

# A citation entry needs these fields to be formatted in a reference style.
_CITATION_FIELDS = ("title",)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
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


def _citations(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    entries = (artifacts.get("citation") or {}).get("entries") or []
    return [entry for entry in entries if isinstance(entry, dict)]


def _check_artifact(key: str, label: str, criterion: str,
                    artifacts: dict[str, Any]) -> dict[str, Any]:
    present = bool(artifacts.get(key))
    return {
        "id": f"artifact:{key}",
        "title": label,
        "criterion": criterion,
        "kind": "artifact_present",
        "state": "satisfied" if present else "failed",
        "evidence": ([{"artifact": key}] if present else []),
        "detail": ("已记录该产物" if present else "任务记录中没有该产物"),
    }


def _check_material_integrity(artifacts: dict[str, Any], materials_root: Path | None
                              ) -> dict[str, Any]:
    rows = _material_rows(artifacts)
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
    for row in recorded:
        if not row["path"]:
            continue
        path = _resolve_material(row["path"], materials_root)
        current = _digest(path)
        if current is None:
            missing.append(row)
        elif current != row["sha256"]:
            stale.append(row)
        else:
            checked.append(row)
    if stale or missing:
        evidence = []
        for row in (stale + missing):
            path = _resolve_material(row["path"], materials_root)
            evidence.append({"material": row["id"], "recorded_sha256": row["sha256"],
                             "current_sha256": _digest(path)})
        return {"id": "material:integrity", "title": "材料完整性",
                "criterion": "有本地材料的，记录的哈希必须仍然一致",
                "kind": "material_integrity", "state": "stale",
                "evidence": evidence,
                "detail": (f"{len(stale)} 份材料内容已变化，{len(missing)} 份材料已不可访问；"
                           "原结论需要复核")}
    return {"id": "material:integrity", "title": "材料完整性",
            "criterion": "有本地材料的，记录的哈希必须仍然一致",
            "kind": "material_integrity", "state": "satisfied",
            "evidence": [{"material": row["id"], "sha256": row["sha256"]} for row in checked],
            "detail": f"{len(checked)} 份材料哈希一致"}


def _check_citations(artifacts: dict[str, Any]) -> dict[str, Any]:
    entries = _citations(artifacts)
    if not entries:
        return {"id": "citations:format", "title": "参考文献完整性",
                "criterion": "参考文献条目必须齐备且非空",
                "kind": "citation_format", "state": "failed",
                "evidence": [], "detail": "没有参考文献条目"}
    incomplete = [entry for entry in entries
                  if not all(_text(entry.get(field)) for field in _CITATION_FIELDS)]
    if incomplete:
        return {"id": "citations:format", "title": "参考文献完整性",
                "criterion": "参考文献条目必须齐备且非空",
                "kind": "citation_format", "state": "failed",
                "evidence": [{"citation": _text(entry.get("title")) or "(无标题)"}
                             for entry in incomplete],
                "detail": f"{len(incomplete)} 条参考文献缺少必要字段"}
    return {"id": "citations:format", "title": "参考文献完整性",
            "criterion": "参考文献条目必须齐备且非空",
            "kind": "citation_format", "state": "satisfied",
            "evidence": [{"citations": len(entries)}],
            "detail": f"{len(entries)} 条参考文献字段齐备"}


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


def _check_manual(confirmations: dict[str, Any]) -> dict[str, Any]:
    """A human confirmation is the only thing that can close this item."""
    record = confirmations.get("outcome_review")
    if not isinstance(record, dict):
        return {"id": "review:manual", "title": "人工复核",
                "criterion": "结论必须由人工复核确认后才能算完成",
                "kind": "manual_review", "state": "pending",
                "evidence": [], "detail": "尚未记录人工复核"}
    missing = [field for field in ("by", "note", "at") if not _text(record.get(field))]
    if missing:
        return {"id": "review:manual", "title": "人工复核",
                "criterion": "结论必须由人工复核确认后才能算完成",
                "kind": "manual_review", "state": "pending",
                "evidence": [], "detail": "人工复核记录缺少：" + "、".join(missing)}
    return {"id": "review:manual", "title": "人工复核",
            "criterion": "结论必须由人工复核确认后才能算完成",
            "kind": "manual_review", "state": "satisfied",
            "evidence": [{"by": _text(record.get("by")), "at": _text(record.get("at"))}],
            "detail": "已有人工复核记录"}


def build_checklist(artifacts: dict[str, Any], manifest: dict[str, Any] | None = None,
                    confirmations: dict[str, Any] | None = None,
                    materials_root: Path | None = None) -> list[dict[str, Any]]:
    """Deterministic acceptance checklist for one task's delivered outcomes."""
    artifacts = artifacts or {}
    manifest = manifest or {}
    confirmations = confirmations or {}
    items = [_check_artifact(key, label, criterion, artifacts)
             for key, label, criterion in ARTIFACT_CHECKS]
    items.append(_check_material_integrity(artifacts, materials_root))
    items.append(_check_citations(artifacts))
    items.append(_check_deficits(manifest))
    items.append(_check_manual(confirmations))
    return items


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
        materials_root: Path | None = None) -> dict[str, Any]:
    """Build and evaluate the checklist in one call."""
    return evaluate(build_checklist(artifacts, manifest, confirmations, materials_root))
