"""Durable Swarm plans backed by the existing permission-aware agent runtime.

Planning has no model or tool side effects. Execution is synchronous here so
desktop/bridge callers can choose their own worker; every branch uses a real
sidechain journal, and approvals stay in the regular AgentRuntime API.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import config, settings
from .agents.specs import get_spec
from .session_lock import session_lock
from .state_store import write_json


TERMINAL = {"done", "partial", "error", "blocked", "cancelled", "interrupted"}


def _process_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied does not prove death.
        try:
            exit_code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SwarmCoordinator:
    def __init__(self, runtime, root: Path | str | None = None):
        self.runtime = runtime
        self.root = Path(root) if root is not None else config.DATA_DIR / "swarms"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, plan_id: str) -> Path:
        if not isinstance(plan_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", plan_id):
            raise ValueError("无效的 Swarm 计划 ID")
        return self.root / f"{plan_id}.json"

    def _load(self, plan_id: str) -> dict[str, Any]:
        path = self._path(plan_id)
        if not path.is_file():
            raise ValueError("Swarm 计划不存在")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or data.get("plan_id") != plan_id:
            raise ValueError("Swarm 计划版本或身份无效")
        return data

    def plan(self, request: str, branches=None, max_parallel=None) -> dict[str, Any]:
        if not isinstance(request, str) or not request.strip() or len(request) > 100000:
            raise ValueError("研究目标不能为空或超过 100000 字符")
        request = request.strip()
        if max_parallel is None:
            max_parallel = settings.load().get("subagent_max_parallel", 4)
        if type(max_parallel) is not int or not 1 <= max_parallel <= 4:
            raise ValueError("Swarm 并发数必须为 1–4 的整数")
        if branches is None:
            queries = self.runtime._split_queries(request)
            if len(queries) == 1:
                queries = [request, f"{request} methods", f"{request} benchmarks limitations"]
            branches = [{"agent": "literature", "request": query} for query in queries]
        if not isinstance(branches, list) or not 1 <= len(branches) <= 16:
            raise ValueError("Swarm 计划需要 1–16 路分支")
        rows = []
        for index, branch in enumerate(branches):
            if not isinstance(branch, dict):
                raise ValueError("每个分支须包含 agent 和 request")
            agent = branch.get("agent", "literature")
            text = branch.get("request")
            if not isinstance(agent, str) or not isinstance(text, str) or not text.strip() or len(text) > 100000:
                raise ValueError("分支角色与请求无效")
            spec = get_spec(agent)
            rows.append({"id": f"branch-{index + 1}", "agent": spec.name,
                         "request": text.strip(), "status": "queued", "session_id": "", "error": ""})
        plan_id = "swarm-" + uuid.uuid4().hex[:12]
        state = {"schema_version": 1, "plan_id": plan_id, "session_id": uuid.uuid4().hex[:12],
                 "request": request, "offline": bool(self.runtime.offline), "status": "planned",
                 "max_parallel": max_parallel, "branches": rows, "created_at": time.time(),
                 "pending": [], "tokens": 0, "steps": 0, "error": ""}
        write_json(self._path(plan_id), state)
        return self.snapshot(plan_id)

    def start(self, plan_id: str) -> dict[str, Any]:
        # A process-held lock prevents duplicate starts even across bridge restarts.
        with session_lock(self._path(plan_id)):
            state = self._load(plan_id)
            if state["status"] != "planned" or self._cancel_path(plan_id).exists():
                return self.snapshot(plan_id)
            if bool(state["offline"]) != bool(self.runtime.offline):
                raise ValueError("执行模式与计划不同，请使用计划创建时的在线/离线模式")
            state.update(status="running", owner_pid=os.getpid(), started_at=time.time())
            write_json(self._path(plan_id), state)
            sid = state["session_id"]
            steps = [{"tool": "subagent.fanout", "reason": "按已保存计划并行执行独立分支",
                      "arguments": {"agents": [b["agent"] for b in state["branches"]],
                                    "requests": [b["request"] for b in state["branches"]],
                                    "max_parallel": state["max_parallel"],
                                    "__branch_ids": [b["id"] for b in state["branches"]]}}]
            self.runtime._session_agent[sid] = "research"
            self.runtime._workspaces[sid] = {"topic": state["request"]}
            self.runtime.sessions.append(sid, "user_message", {
                "text": state["request"], "agent": "research", "mode": "swarm", "offline": state["offline"]})
            self.runtime.sessions.append(sid, "agent_selected", {"agent": "research"})
            self.runtime.sessions.append(sid, "plan_created", {"plan": {
                "request": state["request"], "mode": "swarm", "steps": steps}})
            try:
                with session_lock(self.runtime.sessions._path(sid)):
                    outcome = self.runtime._continue(sid, steps, 0)
                state.update(status=outcome["status"], error=outcome.get("error", ""))
            except Exception as exc:
                from .workbench import redact
                message = redact(str(exc))
                status = self.runtime._publish_terminal(sid, "error", {"message": message, "source": "swarm"})
                state.update(status=status, error=message if status == "error" else "")
            state["updated_at"] = time.time()
            write_json(self._path(plan_id), state)
            return self.snapshot(plan_id)

    def snapshot(self, plan_id: str) -> dict[str, Any]:
        state = copy.deepcopy(self._load(plan_id))
        sid = state["session_id"]
        parent = self.runtime.sessions.snapshot(sid)
        pending = []
        if parent.get("pending"):
            pending.append({"session_id": sid, **parent["pending"]})
        metas = []
        for path in (self.runtime.sessions.root / sid / "children").glob("*.meta.json"):
            try:
                metas.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        matched = set()
        for branch in state["branches"]:
            meta = next((m for m in metas if m.get("branch_id") == branch["id"]), None)
            if meta is None:
                meta = next((m for m in metas if m.get("child") not in matched and
                             m.get("agent") == branch["agent"] and m.get("request") == branch["request"]), None)
            if meta is None:
                continue
            child = meta["child"]
            matched.add(child)
            snapshot = self.runtime.sessions.snapshot(child)
            branch.update(session_id=child, status=snapshot["status"],
                          tokens=int(meta.get("tokens") or 0), steps=int(meta.get("steps") or 0),
                          error=str(meta.get("error") or ""), summary=str(snapshot.get("content") or ""))
            if snapshot.get("pending"):
                pending.append({"session_id": child, **snapshot["pending"]})
        statuses = [b["status"] for b in state["branches"]]
        active = any(s in {"running", "cancelling", "waiting_task", "waiting_children"} for s in statuses)
        finished = sum(s in TERMINAL for s in statuses)
        succeeded = statuses.count("done")
        cancelled = self._cancel_path(plan_id).exists()
        if cancelled:
            state["status"] = "cancelling" if active else "cancelled"
            for branch in state["branches"]:
                if branch["status"] == "queued":
                    branch["status"] = "cancelled"
        elif pending:
            state["status"] = "waiting_approval"
        elif finished == len(statuses):
            state["status"] = "done" if succeeded == len(statuses) else "partial" if succeeded else "error"
        elif parent.get("status") in {"blocked", "error", "cancelled"}:
            state["status"] = parent["status"]
        elif state["status"] == "running" and not _process_alive(int(state.get("owner_pid") or 0)):
            state.update(status="interrupted", error="执行进程已退出；请检查已有产物后创建新计划，不会自动重放工具")
            for branch in state["branches"]:
                if branch["status"] in {"queued", "running", "cancelling"}:
                    branch["status"] = "interrupted"
        elif active:
            state["status"] = "running" if any(s == "running" for s in statuses) else "waiting_children"
        state["pending"] = pending
        state["tokens"] = sum(b.get("tokens", 0) for b in state["branches"])
        state["steps"] = sum(b.get("steps", 0) for b in state["branches"])
        state["progress"] = {"total": len(statuses), "completed": sum(b["status"] in TERMINAL for b in state["branches"]),
                             "succeeded": succeeded, "failed": sum(b["status"] in {"error", "blocked", "interrupted"} for b in state["branches"]),
                             "running": sum(b["status"] == "running" for b in state["branches"]),
                             "waiting": len(pending)}
        return state

    def _cancel_path(self, plan_id: str) -> Path:
        return self._path(plan_id).with_suffix(".cancel")

    def cancel(self, plan_id: str) -> dict[str, Any]:
        state = self.snapshot(plan_id)
        if state["status"] in TERMINAL:
            return state
        sid = state["session_id"]
        from .session_lock import session_terminal_lock
        # Share the parent's short terminal lane with completion. A stale
        # initial snapshot cannot cancel a result already durably published.
        with session_terminal_lock(self.runtime.sessions._path(sid)):
            if self.runtime._terminal_status(sid) is None:
                self._cancel_path(plan_id).touch()
                # This also closes the race before start creates the journal.
                (self.runtime.sessions.root / f"{sid}.cancel").touch()
            else:
                return self.snapshot(plan_id)
        # cancel_session takes the same non-reentrant short lock itself.
        if self.runtime.sessions._path(sid).is_file():
            self.runtime.cancel_session(sid)
        return self.snapshot(plan_id)

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        paths = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        rows = []
        for path in paths:
            try:
                rows.append(self.snapshot(path.stem))
            except (OSError, ValueError, TypeError, KeyError):
                continue
            if len(rows) >= limit:
                break
        return rows
