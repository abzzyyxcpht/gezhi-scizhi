"""Human review records for outcome checks.

The acceptance checklist has one item that no automated check can close: a
person looked at the deliverable and said so. That record lives in its own
store, separate from task artifacts, so a model-produced artifact can never
satisfy it by writing a field.

The store mirrors the world-fact store pattern: one JSON file under the data
directory, keyed by task id, written only through an explicit user action.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config
from .workbench import redact

SCHEMA_VERSION = "outcome-review.v1"
REQUIRED = ("by", "note")


def _path() -> Path:
    return Path(config.DATA_DIR) / "outcome_reviews.json"


def _read() -> dict[str, Any]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read(task_id: str) -> dict[str, Any]:
    """Return the recorded review for a task, or an empty mapping."""
    record = _read().get(str(task_id or ""))
    return record if isinstance(record, dict) else {}


def record(task_id: str, by: str, note: str, *, at: str = "") -> dict[str, Any]:
    """Store a human review. Both a reviewer and a note are required."""
    tid = str(task_id or "").strip()
    if not tid:
        raise ValueError("缺少任务 ID")
    reviewer = str(by or "").strip()
    if not reviewer:
        raise ValueError("请填写复核人")
    comment = str(note or "").strip()
    if not comment:
        raise ValueError("请写明复核结论")
    data = _read()
    entry = {"by": reviewer[:80], "note": comment[:2000], "at": str(at or _now()),
             "source": "user", "schema_version": SCHEMA_VERSION}
    data[tid] = entry
    _write(data)
    return entry


def handle(req: dict[str, Any]) -> dict[str, Any]:
    """Bridge entry: {"cmd": "outcome_review", "task_id": ..., "record": {...}}."""
    task_id = str(req.get("task_id") or "")
    if req.get("record"):
        payload = req.get("record") or {}
        try:
            entry = record(task_id, str(payload.get("by") or ""),
                           str(payload.get("note") or ""),
                           at=str(payload.get("at") or ""))
        except ValueError as exc:
            return {"ok": False, "error": redact(str(exc))}
        return {"ok": True, "task_id": task_id, "review": entry}
    return {"ok": True, "task_id": task_id, "review": read(task_id),
            "boundary": "本记录只来自用户的显式复核动作；智能体产物中的任何字段都不能替代它。"}
