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
import copy
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import config
from .workbench import redact

SCHEMA_VERSION = "outcome-review.v2"
REQUIRED = ("by", "note")
_LOCK = threading.RLock()
_HISTORY_LIMIT = 50


class ReviewError(ValueError):
    """A review failure whose code is safe for either frontend to act on."""

    def __init__(self, message: str, code: str = "invalid_review", *, revision: str = ""):
        super().__init__(message)
        self.code = code
        self.revision = revision


def _path() -> Path:
    return Path(config.DATA_DIR) / "outcome_reviews.json"


def _read() -> dict[str, Any]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ReviewError("复核记录无法读取；原记录已保留，请修复后重试", "review_store_error") from exc
    if not isinstance(data, dict) or any(not isinstance(entry, dict) for entry in data.values()):
        raise ReviewError("复核记录结构无效；原记录已保留，请修复后重试", "review_store_error")
    return data


def _write(data: dict[str, Any]) -> None:
    from .state_store import write_json
    write_json(_path(), data, default=None)


@contextmanager
def _transaction():
    """Serialize the whole read/validate/write across bridge and desktop processes."""
    from .session_lock import session_lock
    with _LOCK:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with session_lock(path):
            yield


def _now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read(task_id: str) -> dict[str, Any]:
    """Return the recorded review for a task, or an empty mapping."""
    with _LOCK:
        entry = _read().get(str(task_id or ""))
        return copy.deepcopy(entry) if isinstance(entry, dict) else {}


def record(task_id: str, by: str, note: str, *, decision: str = "",
           expected_revision: str = "", at: str = "") -> dict[str, Any]:
    """Store an explicit decision only for the exact outcome the user inspected."""
    from . import service

    tid = task_id.strip() if isinstance(task_id, str) else ""
    if not tid:
        raise ReviewError("缺少任务 ID")
    reviewer = by.strip() if isinstance(by, str) else ""
    if not reviewer or len(reviewer) > 80:
        raise ReviewError("请填写不超过 80 字的复核人")
    comment = note.strip() if isinstance(note, str) else ""
    if not comment or len(comment) > 2000:
        raise ReviewError("请填写不超过 2000 字的复核说明")
    if not isinstance(decision, str) or decision not in {"approved", "changes_requested"}:
        raise ReviewError("请选择复核通过或要求修改")
    if not isinstance(expected_revision, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_revision):
        raise ReviewError("缺少有效的成果版本，请先重新读取验收清单")
    if service.get(tid) is None:
        raise ReviewError("请先打开有效的研究任务", "task_not_found")
    try:
        with _transaction():
            data = _read()
            current = service.outcome_checks(tid)["revision"]
            if current != expected_revision:
                raise ReviewError("成果已变化，请重新读取清单并复核后保存", "stale_revision", revision=current)
            entry = {"by": reviewer, "note": comment, "at": str(at or _now()),
                     "decision": decision, "revision": current,
                     "source": "user", "schema_version": SCHEMA_VERSION}
            previous = data.get(tid) or {}
            history = previous.get("history", [previous] if previous else [])
            if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
                raise ReviewError("复核历史结构无效；原记录已保留", "review_store_error")
            history = [{key: item[key] for key in entry if key in item} for item in history]
            entry["history"] = (history + [dict(entry)])[-_HISTORY_LIMIT:]
            data[tid] = entry
            _write(data)
            return copy.deepcopy(entry)
    except OSError as exc:
        raise ReviewError("复核记录写入失败；请重新读取清单后重试", "review_store_error") from exc


def handle(req: dict[str, Any]) -> dict[str, Any]:
    """Bridge entry: {"cmd": "outcome_review", "task_id": ..., "record": {...}}."""
    task_id = str(req.get("task_id") or "")
    if "record" in req:
        payload = req["record"]
        try:
            if not isinstance(payload, dict):
                raise ReviewError("复核记录必须为对象")
            entry = record(task_id, payload.get("by"), payload.get("note"),
                           decision=payload.get("decision"),
                           expected_revision=payload.get("expected_revision"))
        except ReviewError as exc:
            return {"ok": False, "error": redact(str(exc)), "code": exc.code,
                    **({"revision": exc.revision} if exc.revision else {})}
        return {"ok": True, "task_id": task_id, "review": entry}
    return {"ok": True, "task_id": task_id, "review": read(task_id),
            "boundary": "本记录只来自用户的显式复核动作；智能体产物中的任何字段都不能替代它。"}
