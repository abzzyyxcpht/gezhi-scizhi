"""Bounded local launches publish durable session identity before model work.

Workers are daemon threads, matching the bridge lifetime. Losing the bridge
never writes a successful completion; journals remain available for recovery.
"""
from __future__ import annotations

import copy
import re
import threading
import uuid
from collections import OrderedDict

from .agents.specs import ROSTER


class AgentLauncher:
    def __init__(self, max_running=4):
        self._lock = threading.RLock()
        self._slots = threading.BoundedSemaphore(max_running)
        self._requests = OrderedDict()
        self._sessions = {}

    def launch(self, runtime, request, mode="agent", agent="research", *, launch_id=None, session_id=None,
               workspace_mode=None):
        if not isinstance(request, str) or not request.strip() or len(request) > 100000:
            raise ValueError("请求为空或超过 100000 字符")
        if mode not in {"agent", "review"} or not isinstance(agent, str) or agent not in ROSTER:
            raise ValueError("会话模式或智能体无效")
        if session_id is not None:
            if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
                raise ValueError("无效的会话 ID")
        # Resolve mentions before starting a thread, as the normal runtime does.
        text, _ = runtime._resolve_agent(request, agent)
        if not text:
            raise ValueError("Agent 请求不能为空")
        launch_id = launch_id or uuid.uuid4().hex
        if not isinstance(launch_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", launch_id):
            raise ValueError("启动请求 ID 无效")
        if workspace_mode is not None:
            from .workspace_modes import normalize_mode
            workspace_mode = normalize_mode(workspace_mode)
        signature = (id(runtime), request, mode, agent, session_id, workspace_mode)
        with self._lock:
            entry = self._requests.get(launch_id)
            if entry:
                if entry["signature"] != signature:
                    raise ValueError("启动请求 ID 已用于不同任务")
            else:
                if session_id is not None:
                    snapshot = runtime.sessions.snapshot(session_id)
                    if snapshot.get('status') not in {'draft', 'done', 'planned'}:
                        raise ValueError('当前会话尚未完成；请先处理审批、继续或分叉')
                    saved_agent = snapshot.get('agent') or 'research'
                    expected_agent = 'critic' if mode == 'review' and agent != 'general' else agent
                    if snapshot.get('request') and (expected_agent != saved_agent or mode != (snapshot.get('mode') or 'agent')):
                        raise ValueError('切换角色或模式请新建或分叉会话')
                if not self._slots.acquire(blocking=False):
                    raise ValueError("已有 4 个智能体在运行，请等待或取消后重试")
                entry = {"signature": signature, "runtime": runtime, "ready": threading.Event(),
                         "finished": threading.Event(), "session_id": "", "result": None, "error": ""}
                self._requests[launch_id] = entry

                def started(sid):
                    with self._lock:
                        entry["session_id"] = sid
                        self._sessions[sid] = entry
                    entry["ready"].set()

                def work():
                    try:
                        if session_id is None:
                            purpose = {'workspace_mode': workspace_mode} if workspace_mode is not None else {}
                            result = runtime.start(request, mode, agent, on_started=started, **purpose)
                        else:
                            from .session_workspace import _turn
                            response = _turn({'session_id': session_id, 'request': request, 'mode': mode,
                                              'agent': agent, 'online': not runtime.offline}, on_started=started)
                            result = response['result']
                        if not isinstance(result, dict):
                            raise ValueError("智能体返回了无效结果")
                        with self._lock:
                            # The event journal is read separately; do not duplicate it in memory/polls.
                            entry["result"] = {k: v for k, v in result.items() if k != "events"}
                    except Exception as exc:
                        from .workbench import redact
                        message = redact(str(exc))
                        sid = entry["session_id"]
                        status = "error"
                        if sid:
                            try:
                                status = runtime._publish_terminal(sid, "error", {
                                    "message": message, "source": "agent_launch"})
                            except OSError:
                                pass  # Surface failure in the result even if storage has become unavailable.
                        with self._lock:
                            entry["error"] = message if status == "error" else ""
                            entry["result"] = {"session_id": sid, "status": status,
                                               "error" if status == "error" else "warning": message}
                    finally:
                        entry["finished"].set()
                        entry["ready"].set()
                        self._slots.release()

                worker = threading.Thread(target=work, name=f"scizhi-agent-{launch_id[:12]}", daemon=True)
                entry["thread"] = worker
                try:
                    worker.start()
                except Exception:
                    self._requests.pop(launch_id, None)
                    self._slots.release()
                    raise
                # Bound completed in-memory results; persistent journals remain intact.
                for key, old in list(self._requests.items()):
                    if len(self._requests) <= 256:
                        break
                    if old["finished"].is_set() and old is not entry:
                        self._requests.pop(key)
                        if self._sessions.get(old["session_id"]) is old:
                            self._sessions.pop(old["session_id"], None)
        if not entry["ready"].wait(15):
            raise ValueError(f"启动仍在等待存储响应；重试时请沿用启动请求 ID {launch_id}")
        if not entry["session_id"]:
            raise ValueError(entry["error"] or "启动未生成会话")
        state = self.status(entry["session_id"])
        return {**(state["result"] or state["snapshot"]), "launch_id": launch_id,
                "launch_pending": not state["finished"]}

    def status(self, session_id, *, fallback_store=None):
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            raise ValueError("会话 ID 无效")
        with self._lock:
            entry = self._sessions.get(session_id)
        if entry is None:
            if fallback_store is None:
                raise ValueError("此进程没有该启动记录")
            return {"snapshot": fallback_store.snapshot(session_id), "result": None,
                    "finished": False, "worker_missing": True}
        with self._lock:
            result = copy.deepcopy(entry["result"])
            finished = entry["finished"].is_set()
        # Once finished is observed, every journal write from that worker is durable.
        # Reading the journal first can pair an old running snapshot with a new final result.
        snapshot = entry["runtime"].sessions.snapshot(session_id)
        if result and result.get("status") != snapshot.get("status"):
            result = None  # An approval or continuation has already advanced this session.
        return {"snapshot": snapshot, "result": result, "finished": finished,
                "worker_missing": False}

    def join(self, session_id, timeout=None):
        with self._lock:
            entry = self._sessions.get(session_id)
        if entry is None:
            return False
        entry["thread"].join(timeout)
        return not entry["thread"].is_alive()


launcher = AgentLauncher()
