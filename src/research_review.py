"""Heuristic multi-dimensional peer review for research notebook records.

Sakana AI reviewer analogue, fully deterministic and offline: it scores record
structure, evidence balance, testability, result closure and observation depth.
It never calls a model, never verifies scientific truth, and states that boundary
in every response. The scores are录入质量启发式, not peer review verdicts.
"""
from __future__ import annotations

from typing import Any

from . import research_notebook

_FIELDS = ("question", "prediction", "method", "metrics", "result", "limitations",
           "next_action")
_DIMENSIONS = ("structure", "evidence", "testability", "closure", "depth")
_BOUNDARY = ("启发式多维评分：仅衡量录入质量（结构/证据/可检验/闭合/深度），"
             "不调用模型、不验证科学真伪，不构成同行评审结论。")

_SUGGESTIONS = {
    "structure": "补齐空字段：{fields}",
    "evidence": "补充支持/反驳证据（当前关系：{supports} 支持 / {challenges} 反驳）",
    "testability": "填写可检验预测（prediction）与评价指标（metrics）",
    "closure": "记录实际结果（result）；completed 状态必须伴随真实结果",
    "depth": "追加观察记录（observations）以沉淀过程证据",
}


def _clamp(value: float) -> float:
    return max(0.0, min(5.0, value))


def _score_dimensions(record: dict[str, Any]) -> dict[str, float]:
    filled = [name for name in _FIELDS if record.get(name)]
    empty = [name for name in _FIELDS if not record.get(name)]
    structure = _clamp(round(len(filled) / len(_FIELDS) * 5, 2))

    supports = sum(1 for e in record.get("evidence", []) if e.get("relation") == "supports")
    challenges = sum(1 for e in record.get("evidence", []) if e.get("relation") == "challenges")
    total_evidence = supports + challenges
    balance = (supports - challenges) / (total_evidence + 1)
    evidence = _clamp(round(min(total_evidence, 4) * 0.75 + (balance + 1) * 1.25, 2))

    testability = 0.0
    if record.get("prediction"):
        testability += 2.5
    if record.get("metrics"):
        testability += 2.5

    status = record.get("status", "planned")
    closure = {"completed": 5.0 if record.get("result") else 1.0,
               "failed": 2.5 if record.get("result") else 1.0,
               "running": 2.0, "planned": 1.0}.get(status, 1.0)

    depth = _clamp(round(min(len(record.get("observations", [])), 5), 2))

    return {"structure": structure, "evidence": evidence,
            "testability": _clamp(round(testability, 2)), "closure": closure, "depth": depth,
            "_meta": {"empty_fields": empty, "supports": supports, "challenges": challenges,
                      "observations": len(record.get("observations", []))}}


def review_record(record: dict[str, Any]) -> dict[str, Any]:
    """Score one notebook record. Deterministic, offline, boundary-stated."""
    dims = _score_dimensions(record)
    meta = dims.pop("_meta")
    overall = round(sum(dims[name] for name in _DIMENSIONS) / len(_DIMENSIONS), 2)
    suggestions = []
    if meta["empty_fields"]:
        suggestions.append(_SUGGESTIONS["structure"].format(fields="、".join(meta["empty_fields"])))
    if dims["evidence"] < 3:
        suggestions.append(_SUGGESTIONS["evidence"].format(**meta))
    if dims["testability"] < 2.5:
        suggestions.append(_SUGGESTIONS["testability"])
    if dims["closure"] < 2.5:
        suggestions.append(_SUGGESTIONS["closure"])
    if dims["depth"] < 2:
        suggestions.append(_SUGGESTIONS["depth"])
    verdict = "优先补齐" if overall < 2 else "可提交讨论" if overall < 3.5 else "结构良好"
    return {"ok": True, "record_id": record.get("id", ""),
            "title": record.get("title", ""), "kind": record.get("kind", ""),
            "status": record.get("status", ""), "dimensions": dims, "overall": overall,
            "verdict": verdict, "suggestions": suggestions, "boundary": _BOUNDARY}


def handle(req: dict) -> dict:
    """Peer-review a notebook record by id. Read-only; never mutates records."""
    if not isinstance(req, dict) or req.get("cmd") != "peer_review":
        return {"ok": False, "error": "未知审稿命令。", "code": "unknown_command"}
    record_id = req.get("id") or req.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        return {"ok": False, "error": "必须提供科研记录 ID（/peer-review <ID>）。",
                "code": "invalid_data"}
    found = research_notebook.handle({"cmd": "notebook_get", "id": record_id.strip()})
    if not found.get("ok"):
        return {"ok": False, "error": found.get("error", "未找到科研记录。"),
                "code": found.get("code", "not_found")}
    return review_record(found["record"])
