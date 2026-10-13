"""Durable research planning; execution stays in the existing workflow service.

The scheduler runs only while its host process lives. Claims are persisted before
dispatch and never retried on uncertainty. Restarting requires explicit rearming.
"""
from __future__ import annotations

import copy
import html
import json
import math
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import config
from .session_lock import session_lock
from .state_store import write_json

COMMANDS = frozenset({"todo_list", "todo_save", "todo_delete", "board_snapshot",
                      "schedule_list", "schedule_save", "schedule_delete", "schedule_run_now",
                      "schedule_tick", "notifications_list", "notification_read",
                      "notifications_settings", "research_handoff"})
_HOST_ID = uuid.uuid4().hex
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_TODO_STATES = {"todo", "doing", "done", "blocked"}
_TERMINAL = {"done", "failed", "cancelled"}
_ACTIVE = {"pending", "running", "waiting_human", "paused"}
_UNCERTAIN = {"claiming", "uncertain"}
_TODO_FIELDS = {"id", "project_id", "title", "status", "priority", "due_at", "task_id",
                "session_id", "notes", "kind"}
_SCHEDULE_FIELDS = {"id", "project_id", "title", "workflow_id", "topic", "run_at",
                    "interval_seconds", "max_runs", "offline", "enabled"}
_SCHEDULE_READONLY = {"status", "task_id", "task_ids", "runs", "next_run_at", "last_error",
                       "created_at", "updated_at", "requires_resume", "claim_id", "owner_host"}


class PlanningError(ValueError):
    def __init__(self, message: str, code: str = "invalid_data"):
        super().__init__(message)
        self.code = code


def _text(value: Any, field: str, *, required: bool = False, maximum: int = 20_000) -> str:
    if not isinstance(value, str):
        raise PlanningError(f"{field} 必须为字符串。")
    value = value.strip()
    if "\0" in value or len(value) > maximum or (required and not value):
        raise PlanningError(f"{field} 为空或超过长度限制。")
    return value


def _identifier(value: Any, field: str = "id", *, optional: bool = False) -> str:
    value = _text(value, field, required=not optional, maximum=128)
    if value and not _ID.fullmatch(value):
        raise PlanningError(f"{field} 格式无效。")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise PlanningError(f"{field} 必须为布尔值。")
    return value


def _moment(value: Any) -> datetime:
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError
        return moment.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise PlanningError("时间必须为包含时区偏移的 ISO 8601 时间。") from None


def _stamp(moment: datetime) -> str:
    return _moment(moment).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe(value: Any) -> Any:
    from .workbench import redact
    return redact(value)


def _default_dispatch(request: dict) -> dict:
    from .workbench import handle
    return handle(request)


def _default_tasks() -> list[dict]:
    from .service import list_tasks
    return list_tasks()


def _default_notebook() -> list[dict]:
    from .research_notebook import handle
    result = handle({"cmd": "notebook_list"})
    if not result["ok"]:
        raise PlanningError(result["error"], result.get("code", "notebook_error"))
    return result["records"]


def _default_audit(task_id: str) -> dict:
    from .service import research_audit
    return research_audit(task_id)


def _default_sessions(request: dict) -> list[dict]:
    try:
        from .session_workspace import handle
    except ImportError:
        return []
    response = handle(request)
    if not response.get("ok"):
        raise PlanningError(response.get("error", "会话关联读取失败。"))
    return response.get("sessions", [])


class ResearchPlanning:
    """Inject dispatch, tasks, notebook, audit and clock for isolated execution."""

    def __init__(self, data_dir: Path | str | None = None, *, dispatch: Callable | None = None,
                 tasks: Callable | None = None, notebook: Callable | None = None,
                 audit: Callable | None = None, clock: Callable | None = None,
                 host_id: str | None = None, sessions: Callable | None = None):
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "research_planning.json"
        self.dispatch = dispatch or _default_dispatch
        self.tasks = tasks or _default_tasks
        self.notebook = notebook or _default_notebook
        self.audit = audit or _default_audit
        self.sessions = sessions or _default_sessions
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.host_id = host_id or _HOST_ID
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._worker_lock = threading.Lock()
        self._session_store = None
        self.last_scheduler_error = ""

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "todos": {}, "schedules": {}, "notifications": {},
                    "notification_events": {}, "observed_tasks": {}, "notifications_enabled": True,
                    "observed_sessions": {}, "sessions_initialized": False}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(state, dict) or state.get("version") != 1
                    or type(state.get("notifications_enabled")) is not bool):
                raise ValueError
            state.setdefault("observed_sessions", {})
            state.setdefault("sessions_initialized", False)
            if type(state["sessions_initialized"]) is not bool:
                raise ValueError
            for name in ("todos", "schedules", "notifications", "notification_events", "observed_tasks", "observed_sessions"):
                if not isinstance(state.get(name), dict):
                    raise ValueError
            for name in ("todos", "schedules", "notifications"):
                for key, row in state[name].items():
                    if not isinstance(row, dict) or row.get("id") != key:
                        raise ValueError
            for row in state["todos"].values():
                if (not (_TODO_FIELDS | {"created_at", "updated_at"}).issubset(row)
                        or not all(isinstance(row[field], str) for field in _TODO_FIELDS)
                        or row["status"] not in _TODO_STATES):
                    raise ValueError
            for row in state["schedules"].values():
                required = _SCHEDULE_FIELDS | (_SCHEDULE_READONLY - {"requires_resume"})
                if (not required.issubset(row) or type(row.get("runs")) is not int or row["runs"] < 0
                        or type(row.get("enabled")) is not bool or not isinstance(row.get("status"), str)
                        or not isinstance(row.get("task_ids"), list)
                        or type(row.get("max_runs")) is not int or not 1 <= row["max_runs"] <= 100
                        or type(row.get("interval_seconds")) not in {int, float}
                        or not math.isfinite(row["interval_seconds"])):
                    raise ValueError
            for row in state["notifications"].values():
                if not {"event_key", "title", "body", "kind", "project_id", "session_id", "task_id", "created_at", "read"}.issubset(row):
                    raise ValueError
            return state
        except (ValueError, TypeError, KeyError):
            raise PlanningError("研究计划文件损坏，原文件已保留，请恢复备份。", "corrupt_store") from None

    @contextmanager
    def _transaction(self):
        with session_lock(self.path):
            state = self._read()
            before = copy.deepcopy(state)
            yield state
            if state != before:
                write_json(self.path, state)

    def _view_schedule(self, row: dict) -> dict:
        result = copy.deepcopy(row)
        foreign = row.get("owner_host") != self.host_id
        requires_resume = foreign and (row["enabled"] or row["status"] in _ACTIVE | _UNCERTAIN)
        if foreign and row["status"] == "claiming":
            result.update(status="uncertain", last_error="启动结果不确定；请核对任务历史，禁止自动重试。")
        elif requires_resume and row["status"] not in _UNCERTAIN:
            result.update(status="interrupted", last_error="宿主已变化；请检查当前任务并显式恢复计划。")
        result["requires_resume"] = requires_resume
        result.pop("owner_host", None)
        result.pop("claim_id", None)
        return result

    @staticmethod
    def _scope(req: dict, rows: list[dict]) -> list[dict]:
        for field in ("project_id", "session_id"):
            if req.get(field) is not None:
                wanted = _identifier(req[field], field, optional=True)
                rows = [row for row in rows if row.get(field, "") == wanted]
        return rows

    def _save_todo(self, incoming: Any) -> dict:
        if not isinstance(incoming, dict) or set(incoming) - (_TODO_FIELDS | {"created_at", "updated_at", "task_status", "linked_task"}):
            raise PlanningError("todo 包含不支持的字段。")
        identifier = _identifier(incoming["id"]) if incoming.get("id") else "todo_" + uuid.uuid4().hex
        stamp = _stamp(self.clock())
        with self._transaction() as state:
            old = state["todos"].get(identifier, {})
            row = {"id": identifier, "title": "", "project_id": "", "session_id": "", "task_id": "",
                   "status": "todo", "priority": "normal", "kind": "research", "due_at": "", "notes": "",
                   "created_at": stamp, **old, **{k: v for k, v in incoming.items() if k in _TODO_FIELDS},
                   "updated_at": stamp}
            row["title"] = _text(row["title"], "title", required=True, maximum=300)
            row["notes"] = _text(row["notes"], "notes")
            for field in ("project_id", "session_id", "task_id"):
                row[field] = _identifier(row[field], field, optional=True)
            for field, choices in (("status", _TODO_STATES), ("priority", {"low", "normal", "high"}),
                                   ("kind", {"research", "experiment", "review"})):
                if not isinstance(row[field], str) or row[field] not in choices:
                    raise PlanningError(f"{field} 不受支持。")
            row["due_at"] = _text(row["due_at"], "due_at", maximum=80)
            row["due_at"] = _stamp(_moment(row["due_at"])) if row["due_at"] else ""
            state["todos"][identifier] = row
        return copy.deepcopy(row)

    def _save_schedule(self, incoming: Any) -> dict:
        if not isinstance(incoming, dict) or set(incoming) - (_SCHEDULE_FIELDS | _SCHEDULE_READONLY):
            raise PlanningError("schedule 包含不支持的字段。")
        identifier = _identifier(incoming["id"]) if incoming.get("id") else "schedule_" + uuid.uuid4().hex
        now = _moment(self.clock())
        with self._transaction() as state:
            old = state["schedules"].get(identifier, {})
            row = {"id": identifier, "project_id": "", "title": "", "workflow_id": "research_pipeline",
                   "topic": "", "run_at": "", "interval_seconds": 0, "max_runs": 1,
                   "offline": True, "enabled": False, "runs": 0, "task_id": "", "task_ids": [],
                   "next_run_at": "", "status": "disabled", "last_error": "", "claim_id": "",
                   "owner_host": self.host_id, "created_at": _stamp(now), **old,
                   **{k: v for k, v in incoming.items() if k in _SCHEDULE_FIELDS}, "updated_at": _stamp(now)}
            row["project_id"] = _identifier(row["project_id"], "project_id", optional=True)
            for field in ("title", "topic"):
                row[field] = _text(row[field], field, required=True, maximum=300 if field == "title" else 20_000)
            from .workbench import WORKFLOW_TITLES
            if not isinstance(row["workflow_id"], str) or row["workflow_id"] not in WORKFLOW_TITLES:
                raise PlanningError("未知工作流，请先查看 workflows。")
            row["run_at"] = _stamp(_moment(row["run_at"]))
            interval = row["interval_seconds"]
            if (type(interval) not in {int, float} or not math.isfinite(interval)
                    or (interval != 0 and not 60 <= interval <= 31_536_000)):
                raise PlanningError("interval_seconds 必须为 0 或 60 到 31536000 的有限数值。")
            if type(row["max_runs"]) is not int or not 1 <= row["max_runs"] <= 100:
                raise PlanningError("max_runs 必须为 1 到 100 的整数。")
            if interval == 0 and row["max_runs"] != 1:
                raise PlanningError("一次性计划的 max_runs 必须为 1。")
            for field in ("offline", "enabled"):
                _boolean(row[field], field)
            execution_fields = _SCHEDULE_FIELDS - {"id", "title", "project_id", "enabled"}
            active = old.get("status") in _ACTIVE | _UNCERTAIN
            if active and any(row[k] != old[k] for k in execution_fields):
                raise PlanningError("任务运行或启动结果不确定时不能修改执行参数。", "schedule_busy")
            if old.get("status") in _UNCERTAIN and incoming.get("enabled") is True:
                raise PlanningError("启动结果不确定，请核对已有任务，不能重新启动此计划。", "uncertain_execution")
            if old.get("status") in {"failed", "cancelled"} and incoming.get("enabled") is True:
                raise PlanningError("前一轮失败或已取消，请核查后创建新计划。")
            if row["enabled"] and incoming.get("enabled") is True:
                if row["runs"] >= row["max_runs"] and not active:
                    raise PlanningError("计划已达到运行次数上限，请创建新计划。")
                row["owner_host"] = self.host_id
                row["last_error"] = ""
            if not active:
                row["status"] = "ready" if row["enabled"] else "disabled"
                if "run_at" in incoming or not row["next_run_at"]:
                    row["next_run_at"] = row["run_at"]
            state["schedules"][identifier] = row
        return self._view_schedule(row)

    def _notify_locked(self, state: dict, event_key: str, title: str, body: str,
                       kind: str = "completion", project_id: str = "", session_id: str = "", task_id: str = "") -> dict | None:
        known = state["notification_events"]
        if event_key in known:
            return state["notifications"].get(known[event_key])
        if not state["notifications_enabled"]:
            known[event_key] = ""
            return None
        identifier = "notice_" + uuid.uuid4().hex
        row = _safe({"id": identifier, "event_key": event_key, "title": title, "body": body,
                     "kind": kind, "project_id": project_id, "session_id": session_id,
                     "task_id": task_id, "created_at": _stamp(self.clock()), "read": False})
        state["notifications"][identifier] = row
        known[event_key] = identifier
        return row

    def notify(self, event_key: str, title: str, body: str, kind: str = "completion",
               project_id: str = "", session_id: str = "", task_id: str = "") -> dict:
        event_key = _text(event_key, "event_key", required=True, maximum=500)
        title = _text(title, "title", required=True, maximum=300)
        body = _text(body, "body")
        kind = _text(kind, "kind", required=True, maximum=80)
        for field, value in (("project_id", project_id), ("session_id", session_id), ("task_id", task_id)):
            _identifier(value, field, optional=True)
        with self._transaction() as state:
            duplicate = event_key in state["notification_events"]
            row = self._notify_locked(state, event_key, title, body, kind, project_id, session_id, task_id)
        return {"ok": True, "notification": copy.deepcopy(row), "created": bool(row) and not duplicate}

    def _task_notice(self, state: dict, task: dict, project_id: str = "") -> None:
        tid, status = task["task_id"], task["status"]
        if not project_id:
            linked = [r for r in state["todos"].values() if r.get("task_id") == tid]
            project_id = next((r["project_id"] for r in linked if r.get("project_id")), "")
        self._notify_locked(state, f"task:{tid}:{status}",
                            {"done": "科研任务已完成", "failed": "科研任务失败", "cancelled": "科研任务已取消"}[status],
                            str(task.get("topic") or tid), "completion" if status == "done" else status,
                            project_id=project_id, task_id=tid)

    def observe_tasks(self) -> dict:
        tasks = self.tasks()
        with self._transaction() as state:
            before = len(state["notifications"])
            for task in tasks:
                tid, status = task.get("task_id"), task.get("status")
                if not tid or not status:
                    continue
                previous = state["observed_tasks"].get(tid)
                if previous in _ACTIVE and status in _TERMINAL:
                    self._task_notice(state, task)
                state["observed_tasks"][tid] = status
            created = len(state["notifications"]) - before
        return {"ok": True, "created": created}

    def observe_sessions(self) -> dict:
        """Observe terminal log events, including approved and continued turns.

        The first scan records a durable baseline. Later scans include fast new
        sessions, and event cursors distinguish multiple turns in one session.
        """
        from .agent_runtime import SessionStore
        if self._session_store is None:
            self._session_store = SessionStore(self.data_dir / "sessions")
        store = self._session_store
        with self._transaction() as state:
            baseline = state["sessions_initialized"]
            previous = copy.deepcopy(state["observed_sessions"])
        changed = []
        for row in store.list():
            sid = row["session_id"]
            if not _ID.fullmatch(sid):
                continue
            if sid in previous and row["events"] <= previous[sid]["events"]:
                continue
            events = store.read(sid)
            terminal = next(((index, event) for index, event in reversed(list(enumerate(events)))
                             if isinstance(event, dict) and event.get("type") in {"completed", "error"}), None)
            changed.append((row, len(events), terminal))
        metadata = {}
        if baseline and any(item[2] is not None for item in changed):
            try:
                metadata = {row["session_id"]: row for row in self.sessions({"cmd": "chat_sessions", "include_archived": True})}
            except (PlanningError, ValueError, OSError):
                pass  # A missing title/association does not erase completion.
        with self._transaction() as state:
            count = len(state["notifications"])
            for row, event_count, terminal in changed:
                sid = row["session_id"]
                cursor = state["observed_sessions"].get(sid, {}).get("events", 0)
                if event_count < cursor:
                    continue
                if baseline and terminal and terminal[0] >= cursor:
                    index, event = terminal
                    status = "error" if event["type"] == "error" else str((event.get("payload") or {}).get("status") or "done")
                    meta = metadata.get(sid, {})
                    key = f"session:{sid}:{index}:{event.get('timestamp', '')}:{event['type']}"
                    self._notify_locked(state, key, "会话已完成" if status == "done" else "会话需要检查",
                                        str(meta.get("title") or row.get("summary") or sid),
                                        "completion" if status == "done" else status,
                                        project_id=str(meta.get("project_id") or ""), session_id=sid,
                                        task_id=str(meta.get("task_id") or ""))
                state["observed_sessions"][sid] = {"events": event_count}
            state["sessions_initialized"] = True
            created = len(state["notifications"]) - count
        return {"ok": True, "created": created}

    def _advance(self, state: dict, row: dict, tasks: dict, now: datetime) -> None:
        if row["status"] not in _ACTIVE or not row["task_id"]:
            return
        task = tasks.get(row["task_id"])
        if task is None:
            row.update(status="uncertain", enabled=False, last_error="关联任务不可见；请核对任务历史，禁止自动重试。")
            return
        status = task["status"]
        if status not in _TERMINAL:
            row["status"] = status if status in _ACTIVE else "running"
            return
        self._task_notice(state, task, row["project_id"])
        state["observed_tasks"][row["task_id"]] = status
        if status in {"failed", "cancelled"}:
            row.update(status=status, enabled=False, next_run_at="", last_error="前一轮失败或已取消，计划已停止。")
        elif row["runs"] >= row["max_runs"] or row["interval_seconds"] == 0:
            row.update(status="completed", enabled=False, next_run_at="")
        else:
            row.update(status="ready" if row["enabled"] else "disabled",
                       next_run_at=_stamp(now + timedelta(seconds=row["interval_seconds"])))
        row["updated_at"] = _stamp(now)

    def _claim(self, identifier: str, now: datetime, *, manual: bool = False) -> dict | None:
        tasks = {row["task_id"]: row for row in self.tasks()}
        with self._transaction() as state:
            row = state["schedules"].get(identifier)
            if row is None:
                raise PlanningError("计划不存在。", "not_found")
            if not manual and row["owner_host"] != self.host_id:
                return None
            self._advance(state, row, tasks, now)
            if row["status"] in _ACTIVE | _UNCERTAIN:
                if manual:
                    raise PlanningError("已有运行中、待审批或结果不确定的任务，不能重复启动。", "schedule_busy")
                return None
            if row["runs"] >= row["max_runs"] or row["status"] in {"failed", "cancelled", "completed"}:
                if manual:
                    raise PlanningError("计划已完成、失败、取消或达到次数上限，请创建新计划。")
                return None
            if not manual:
                if not row["enabled"] or row["status"] != "ready":
                    return None
                due = _moment(row["next_run_at"])
                if now < due:
                    return None
                if (now - due).total_seconds() > 60:
                    row.update(status="missed", enabled=False, last_error="错过执行时间超过 60 秒；请检查后显式立即运行。")
                    return None
            claim = uuid.uuid4().hex
            row.update(status="claiming", claim_id=claim, owner_host=self.host_id,
                       runs=row["runs"] + 1, last_error="", next_run_at="", updated_at=_stamp(now))
            claimed = copy.deepcopy(row)
        return claimed

    def _launch(self, claimed: dict) -> dict:
        try:
            result = self.dispatch({"cmd": "workflow_run", "workflow_id": claimed["workflow_id"],
                                    "topic": claimed["topic"], "offline": claimed["offline"]})
            if not isinstance(result, dict) or not result.get("ok") or not result.get("task_id"):
                raise PlanningError(str(result.get("error", "未返回任务编号")) if isinstance(result, dict) else "无效执行响应")
            tid = _identifier(result["task_id"], "task_id")
        except Exception as exc:
            with self._transaction() as state:
                row = state["schedules"][claimed["id"]]
                if row["claim_id"] == claimed["claim_id"]:
                    row.update(status="uncertain", enabled=False,
                               last_error=str(_safe(f"启动结果不确定，禁止自动重试：{exc}")))
            return {"ok": False, "error": row["last_error"], "schedule": self._view_schedule(row)}
        # If this write fails after launch, the durable claiming row remains and
        # all future hosts refuse to launch it again.
        with self._transaction() as state:
            row = state["schedules"][claimed["id"]]
            if row["claim_id"] != claimed["claim_id"]:
                raise PlanningError("执行领取已变化，请核查任务历史。", "uncertain_execution")
            row.update(status="running", task_id=tid, task_ids=[*row["task_ids"], tid],
                       updated_at=_stamp(self.clock()))
            state["observed_tasks"].setdefault(tid, "running")
        return {"ok": True, "schedule": self._view_schedule(row), "task_id": tid}

    def tick(self, now: datetime | str | None = None) -> dict:
        try:
            moment = _moment(now if now is not None else self.clock())
            with self._transaction() as state:
                identifiers = list(state["schedules"])
            launched, errors = [], []
            for identifier in identifiers:
                claimed = self._claim(identifier, moment)
                if claimed:
                    result = self._launch(claimed)
                    if result["ok"]:
                        launched.append(result["task_id"])
                    else:
                        errors.append(result["error"])
            return {"ok": True, "launched": launched, "errors": errors}
        except (PlanningError, OSError) as exc:
            return {"ok": False, "error": str(_safe(str(exc))), "code": getattr(exc, "code", "storage_error")}

    def start_scheduler(self, interval_seconds: float = 1.0) -> dict:
        if type(interval_seconds) not in {int, float} or not math.isfinite(interval_seconds) or interval_seconds < .1:
            raise PlanningError("轮询间隔必须至少为 0.1 秒。")
        with self._worker_lock:
            if self._worker and self._worker.is_alive():
                return {"ok": True, "running": True}
            self._stop.clear()
            def run():
                while not self._stop.is_set():
                    try:
                        result = self.tick()
                        self.observe_tasks()
                        self.observe_sessions()
                        self.last_scheduler_error = result.get("error", "")
                    except Exception as exc:
                        self.last_scheduler_error = str(_safe(str(exc)))
                    self._stop.wait(interval_seconds)
            self._worker = threading.Thread(target=run, name="scizhi-research-scheduler", daemon=True)
            self._worker.start()
        return {"ok": True, "running": True}

    def stop_scheduler(self) -> dict:
        with self._worker_lock:
            self._stop.set()
            worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=2)
        return {"ok": True, "running": bool(worker and worker.is_alive())}

    def _board(self, req: dict) -> dict:
        task_map = {row["task_id"]: row for row in self.tasks()}
        with self._transaction() as state:
            todos = copy.deepcopy(self._scope(req, list(state["todos"].values())))
            schedules = self._scope(req, list(state["schedules"].values()))
            linked = {row["task_id"] for row in todos if row["task_id"]}
            linked.update(tid for row in schedules for tid in row["task_ids"])
        counts = {status: sum(row["status"] == status for row in todos) for status in sorted(_TODO_STATES)}
        counts["total"] = len(todos)
        for row in todos:
            row["linked_task"] = task_map.get(row["task_id"])
            row["task_status"] = (row["linked_task"] or {}).get("status", "missing" if row["task_id"] else "")
        return {"todos": sorted(todos, key=lambda row: row["updated_at"], reverse=True), "counts": counts,
                "progress": counts["done"] / len(todos) if todos else 0,
                "tasks": [task_map.get(tid, {"task_id": tid, "status": "missing"}) for tid in sorted(linked)]}

    def _handoff(self, req: dict) -> dict:
        if set(req) - {"cmd", "project_id", "session_id", "title", "request_id"}:
            raise PlanningError("交接导出只能指定项目、会话和标题；文件固定保存在 data/exports。")
        board = self._board(req)
        tids = {row["task_id"] for row in board["tasks"]}
        # Session association is optional and read-only. A project scope never
        # includes unlinked global notebook records.
        if req.get("project_id") or req.get("session_id"):
            sessions = self.sessions({"cmd": "chat_sessions", **({"project_id": req["project_id"]} if req.get("project_id") else {})})
            for row in sessions:
                if req.get("session_id") and row["session_id"] != req["session_id"]:
                    continue
                if req.get("project_id") and row.get("project_id") != req["project_id"]:
                    continue
                if row.get("task_id"):
                    tids.add(row["task_id"])
        scoped = req.get("project_id") is not None or req.get("session_id") is not None
        records = [copy.deepcopy(row) for row in self.notebook() if not scoped or row.get("task_id") in tids]
        task_map = {row["task_id"]: row for row in self.tasks()}
        if not scoped:
            tids.update(task_map)
        tasks = [task_map.get(tid, {"task_id": tid, "status": "missing"}) for tid in sorted(tids)]
        deficits = []
        for record in records:
            if not record.get("evidence"):
                deficits.append({"code": "no_evidence", "record_id": record["id"], "message": "未关联证据，不能视为已验证结论。"})
            if record.get("status") == "failed":
                deficits.append({"code": "failed_result", "record_id": record["id"], "message": "存在失败结果，请保留原因和下一步。"})
            if record.get("synthetic") or any(e.get("source", {}).get("synthetic") for e in record.get("evidence", [])):
                deficits.append({"code": "synthetic_evidence", "record_id": record["id"], "message": "含模拟材料，不能作为真实实证证据。"})
        audits = {}
        for task in tasks:
            tid, status = task["task_id"], task["status"]
            if status in {"failed", "missing"}:
                deficits.append({"code": "task_failed" if status == "failed" else "task_missing", "task_id": tid,
                                 "message": "任务失败或不可见，交接不代表执行成功。"})
            try:
                audits[tid] = self.audit(tid)
            except (KeyError, ValueError, OSError):
                audits[tid] = {"available": False, "reason": "没有可读取的研究审计记录。"}
        title = _text(req.get("title", "科研交接记录"), "title", required=True, maximum=300)
        document = _safe({"schema_version": "1.0", "title": title, "exported_at": _stamp(self.clock()),
                          "scope": {"project_id": req.get("project_id", ""), "session_id": req.get("session_id", "")},
                          "todos": board["todos"], "progress": board["progress"], "tasks": tasks,
                          "notebook_records": records, "evidence_deficits": deficits,
                          "manifest": {"source": "recorded_local_state", "research_audits": audits,
                                       "scientific_verification": "not_performed", "reproducibility_verification": "not_performed",
                                       "boundary": "本交接只整理已有记录，不验证科学支持、来源真实性或可复现性。"}})
        out = self.data_dir / "exports"
        out.mkdir(parents=True, exist_ok=True)
        base = out / ("research_handoff_" + uuid.uuid4().hex)
        json_path, markdown_path = base.with_suffix(".json"), base.with_suffix(".md")
        write_json(json_path, document)
        markdown_path.write_text(self._handoff_markdown(document), encoding="utf-8")
        return {"json_path": str(json_path.resolve()), "markdown_path": str(markdown_path.resolve()),
                "paths": [str(markdown_path.resolve()), str(json_path.resolve())], "handoff": document}

    @staticmethod
    def _handoff_markdown(document: dict) -> str:
        def md(value):
            return re.sub(r"([\\`*_[\]|#])", r"\\\1", html.escape(str(value), quote=False)).replace("\r", "")
        lines = [f"# {md(document['title'])}", "", document["manifest"]["boundary"], "",
                 f"导出时间：{document['exported_at']}", f"项目：{md(document['scope']['project_id'] or '全部')}",
                 f"会话：{md(document['scope']['session_id'] or '全部')}", "", "## 待办与进度", "",
                 f"已标记完成的待办：{document['progress']:.0%}", ""]
        lines += [f"- [{r['status']}] {md(r['title'])}；task={md(r['task_id'])}；实际任务状态={md(r.get('task_status', ''))}；{md(r['notes'])}"
                  for r in document["todos"]] or ["未记录待办。"]
        lines += ["", "## 当前任务", ""]
        lines += [f"- {md(r['task_id'])}：{md(r['status'])}，{md(r.get('topic', ''))}" for r in document["tasks"]] or ["未关联任务。"]
        lines += ["", "## 假设、实验与结果", ""]
        for record in document["notebook_records"]:
            lines += [f"### {md(record.get('title', record['id']))}", ""]
            for field in ("kind", "status", "question", "prediction", "method", "metrics", "result", "limitations", "next_action"):
                lines.append(f"- {field}: {md(record.get(field) or '未记录')}")
            for evidence in record.get("evidence", []):
                lines.append(f"- evidence: {md(json.dumps(evidence, ensure_ascii=False))}")
            lines.append("")
        if not document["notebook_records"]:
            lines.append("未关联科研记录。")
        lines += ["", "## 证据缺口与失败结果", ""]
        lines += [f"- {r['code']} · {md(r.get('record_id') or r.get('task_id'))}：{r['message']}" for r in document["evidence_deficits"]] or ["未发现上述规则定义的缺口；这不代表证据已经验证。"]
        lines += ["", "## 科研清单", "", "科学支持与可复现性验证：未执行。", ""]
        for tid, audit in document["manifest"]["research_audits"].items():
            lines += [f"- {md(tid)}：{md(json.dumps(audit, ensure_ascii=False))}"]
        return "\n".join(lines) + "\n"

    def handle(self, req: dict) -> dict:
        try:
            if not isinstance(req, dict) or not isinstance(req.get("cmd"), str) or req["cmd"] not in COMMANDS:
                raise PlanningError("未知研究计划命令。", "unknown_command")
            cmd = req["cmd"]
            if cmd in {"todo_list", "board_snapshot"}:
                return {"ok": True, **self._board(req)}
            if cmd == "todo_save":
                return {"ok": True, "todo": self._save_todo(req.get("todo"))}
            if cmd == "schedule_save":
                return {"ok": True, "schedule": self._save_schedule(req.get("schedule"))}
            if cmd == "schedule_tick":
                return self.tick()
            if cmd == "schedule_run_now":
                claimed = self._claim(_identifier(req.get("id")), _moment(self.clock()), manual=True)
                return self._launch(claimed)
            if cmd == "research_handoff":
                return {"ok": True, **self._handoff(req)}
            with self._transaction() as state:
                if cmd in {"todo_delete", "schedule_delete"}:
                    identifier = _identifier(req.get("id"))
                    collection = state["todos" if cmd == "todo_delete" else "schedules"]
                    if identifier not in collection:
                        raise PlanningError("记录不存在。", "not_found")
                    if cmd == "schedule_delete" and collection[identifier]["status"] in _ACTIVE | _UNCERTAIN:
                        raise PlanningError("运行中或执行结果不确定的计划不能删除；可以停用后核查。", "schedule_busy")
                    del collection[identifier]
                    result = {"deleted": True, "id": identifier}
                elif cmd == "schedule_list":
                    result = {"schedules": [self._view_schedule(row) for row in self._scope(req, list(state["schedules"].values()))],
                              "scheduler_running": bool(self._worker and self._worker.is_alive()),
                              "scheduler_error": self.last_scheduler_error,
                              "boundary": "仅宿主进程运行期间执行；重启后须显式恢复，错过时间不会补跑。"}
                elif cmd == "notifications_list":
                    rows = self._scope(req, list(state["notifications"].values()))
                    if req.get("unread") is not None and _boolean(req["unread"], "unread"):
                        rows = [row for row in rows if not row["read"]]
                    result = {"notifications": sorted(rows, key=lambda row: row["created_at"], reverse=True),
                              "unread_count": sum(not row["read"] for row in rows), "enabled": state["notifications_enabled"]}
                elif cmd == "notifications_settings":
                    if "enabled" in req:
                        state["notifications_enabled"] = _boolean(req["enabled"], "enabled")
                    result = {"enabled": state["notifications_enabled"]}
                else:  # notification_read
                    if req.get("all") is not None and _boolean(req["all"], "all"):
                        rows = self._scope(req, list(state["notifications"].values()))
                    else:
                        identifier = _identifier(req.get("id"))
                        if identifier not in state["notifications"]:
                            raise PlanningError("通知不存在。", "not_found")
                        rows = [state["notifications"][identifier]]
                    for row in rows:
                        row["read"] = True
                    result = {"read": len(rows)}
                result = copy.deepcopy(result)
            return {"ok": True, **result}
        except (PlanningError, OSError) as exc:
            return {"ok": False, "error": str(_safe(str(exc))), "code": getattr(exc, "code", "storage_error")}


_instance: ResearchPlanning | None = None
_instance_lock = threading.Lock()


def _get_instance() -> ResearchPlanning:
    global _instance
    with _instance_lock:
        if _instance is not None and _instance.data_dir.resolve() != Path(config.DATA_DIR).resolve():
            if _instance.stop_scheduler()["running"]:
                raise PlanningError("旧数据目录的调度器正在停止，请稍后重试。", "scheduler_stopping")
            _instance = None
        if _instance is None:
            _instance = ResearchPlanning()
        return _instance


def handle(req: dict) -> dict:
    return _get_instance().handle(req)


def tick(now: datetime | str | None = None) -> dict:
    return _get_instance().tick(now)


def notify(event_key: str, title: str, body: str, kind: str = "completion", project_id: str = "",
           session_id: str = "", task_id: str = "") -> dict:
    return _get_instance().notify(event_key, title, body, kind, project_id, session_id, task_id)


def observe_tasks() -> dict:
    return _get_instance().observe_tasks()


def observe_sessions() -> dict:
    return _get_instance().observe_sessions()


def start_scheduler(interval_seconds: float = 1.0) -> dict:
    return _get_instance().start_scheduler(interval_seconds)


def stop_scheduler() -> dict:
    return _get_instance().stop_scheduler()
