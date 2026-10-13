"""Shared research workbench commands for desktop, remote bridge and TUI.

Loops own scheduling only. Task approvals remain explicit service operations;
reading snapshots and restoring loop definitions never resumes a task.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, service

WORKFLOW_TITLES = {
    "research_pipeline": "完整科研流水线",
    "literature_scan": "文献速览与引用清单",
    "evidence_review": "证据抽取与人工复核",
    "reproduce": "论文复现流水线（计划→确认→白名单执行→实验记录）",
}
COMMANDS = frozenset({"workbench_status", "workflows", "workflow_run", "loop_start",
                      "loop_list", "loop_stop", "loop_resume", "session_export",
                      "terminal_open"})
_ACTIVE_LOOP_STATES = {"running", "scheduled", "waiting_human", "paused"}
_REPLACE_RETRY_DELAYS = (.01, .02, .04, .08, .16)  # Six attempts; at most 310 ms backoff.
_SENSITIVE_KEY = re.compile(
    r"(?:api_?key|access_?token|refresh_?token|id_?token|auth_?token|client_?secret|"
    r"password|passwd|secret|credential|authorization|cookie|bearer|private_?key)", re.I)
_INLINE_SECRET = re.compile(
    r"(?i)((?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
    r"password|authorization|secret|token)[\"']?\s*[=:]\s*[\"']?)(?:(?:Bearer|Basic)\s+)?[^\s,;\"'<>]+")
_BEARER = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_KEY_VALUE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(value: Any) -> Any:
    """Return a recursive safe projection; token usage counters stay intact."""
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            sensitive = (normalized in {"token", "tokens", "auth", "authentication", "headers"}
                         or _SENSITIVE_KEY.search(str(key))
                         or _SENSITIVE_KEY.search(normalized))
            # Counts are measurements, not credentials. Do not discard them.
            counter = normalized in {"inputtokens", "outputtokens", "totaltokens", "prompttokens",
                                     "completiontokens", "cachedtokens", "reasoningtokens", "tokencount"}
            counter = counter or (normalized == "tokens" and isinstance(child, (int, float))
                                  and not isinstance(child, bool))
            out[key] = "[REDACTED]" if sensitive and not counter else redact(child)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(child) for child in value]
    if isinstance(value, str):
        text = _INLINE_SECRET.sub(lambda match: match.group(1) + "[REDACTED]", value)
        return _KEY_VALUE.sub("[REDACTED]", _BEARER.sub("Bearer [REDACTED]", text))
    return value


def workflows() -> list[dict[str, Any]]:
    descriptions = {
        "research_pipeline": "检索并筛选文献，经确认后整理摘要、综述框架和参考文献。",
        "literature_scan": "快速筛选一个主题的文献，生成文献池与规范引用清单。",
        "evidence_review": "人工复核文献池后，逐篇抽取方法、结果与局限并保留出处。",
        "reproduce": "生成复现计划，经人工确认后执行白名单基线命令，结果写入实验记录。",
    }
    return [{"id": wid, "title": title,
             "description": descriptions[wid],
             "stages": service.node_sequence(wid),
             "requires_approval": any(node["type"] == "human_approval"
                                      for node in service.load_workflow(wid)["nodes"])}
            for wid, title in WORKFLOW_TITLES.items()]


def _workflow_args(req: dict[str, Any]) -> dict[str, Any]:
    wid = req.get("workflow_id", "research_pipeline")
    if wid not in WORKFLOW_TITLES:
        raise ValueError("未知科研工作流，请先查看 workflows")
    topic = req.get("topic", "")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("研究方向不能为空")
    from . import settings
    offline = req.get("offline", settings.load().get("default_offline", True))
    if not isinstance(offline, bool):
        raise ValueError("offline 必须为 true 或 false")
    path = req.get("path", "")
    if not isinstance(path, str):
        raise ValueError("path 必须为字符串")
    return {"workflow_id": wid, "topic": topic.strip(), "offline": offline, "path": path}


class Workbench:
    """An in-process scheduler with durable definitions and explicit restart."""

    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.data_dir / "workbench_loops.json"
        self._lock = threading.RLock()
        self._loops: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._load_error = ""
        if self._path.exists():
            try:
                document = json.loads(self._path.read_text(encoding="utf-8"))
                for row in document.get("loops", []):
                    if not isinstance(row, dict) or not row.get("loop_id"):
                        continue
                    row = copy.deepcopy(row)
                    if row.get("status") in _ACTIVE_LOOP_STATES:
                        row.update(status="interrupted", updated_at=_now(),
                                   last_error="进程已重启；请检查当前任务，再手动恢复循环。")
                    self._loops[row["loop_id"]] = row
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                self._load_error = f"循环配置读取失败，原文件已保留：{exc}"

    def _persist_locked(self) -> None:
        if self._load_error:
            raise ValueError(self._load_error)
        target = self._path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            target.write_text(json.dumps({"version": 1, "loops": list(self._loops.values())},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
            # Windows readers/virus scanners can temporarily deny replacing an
            # existing file even though this instance serializes its writers.
            # Retry rename only, keeping the closed temp file and old JSON
            # intact. Permanent denial still surfaces after the bounded wait.
            for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
                try:
                    target.replace(self._path)
                    return
                except OSError as exc:
                    if (getattr(exc, "winerror", None) not in {5, 32, 33}
                            or attempt == len(_REPLACE_RETRY_DELAYS)):
                        raise
                    time.sleep(_REPLACE_RETRY_DELAYS[attempt])
        finally:
            primary_error = sys.exc_info()[1]
            try:
                target.unlink(missing_ok=True)
            except OSError as cleanup_error:
                if primary_error is None:
                    raise
                # Preserve the real write/replace failure if cleanup also
                # fails; attach diagnostics where the interpreter supports it.
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(f"临时文件清理失败 {target}: {cleanup_error}")

    def _update(self, loop_id: str, *, _stop: threading.Event | None = None,
                **changes: Any) -> dict[str, Any]:
        with self._lock:
            row = self._loops[loop_id]
            if _stop is not None and _stop.is_set():
                return copy.deepcopy(row)
            if any(row.get(key) != value for key, value in changes.items()):
                row.update(changes, updated_at=_now())
                self._persist_locked()
            return copy.deepcopy(row)

    def loops(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(reversed(list(self._loops.values()))))

    def _start_worker(self, loop_id: str) -> None:
        with self._lock:
            old = self._threads.get(loop_id)
            if old and old.is_alive():
                return
            stop = threading.Event()
            worker = threading.Thread(target=self._run_loop, args=(loop_id, stop),
                                      name=f"gezhi-loop-{loop_id}", daemon=True)
            self._stops[loop_id] = stop
            self._threads[loop_id] = worker
            worker.start()

    def _run_loop(self, loop_id: str, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                with self._lock:
                    row = copy.deepcopy(self._loops[loop_id])
                task_id = row.get("task_id")
                ctx = service.get(task_id) if task_id else None
                if task_id and ctx is None:
                    self._update(loop_id, _stop=stop, status="failed", last_error="当前任务不存在；循环已停止。")
                    return
                if ctx is not None and ctx.status in {"cancelled", "failed"}:
                    self._update(loop_id, _stop=stop, status=ctx.status,
                                 last_error=f"任务 {task_id} {service.STATUS_TEXT.get(ctx.status, ctx.status)}，循环已停止。")
                    return
                if ctx is not None and ctx.status != "done":
                    status = ctx.status if ctx.status in {"paused", "waiting_human"} else "running"
                    self._update(loop_id, _stop=stop, status=status, next_run_at=None)
                    stop.wait(.1)
                    continue
                if row["iteration"] >= row["max_iterations"]:
                    self._update(loop_id, _stop=stop, status="completed", next_run_at=None)
                    return
                if ctx is not None:
                    next_run = row.get("next_run_at")
                    if next_run is None:
                        next_run = time.time() + row["interval_seconds"]
                        self._update(loop_id, _stop=stop, status="scheduled", next_run_at=next_run)
                    if stop.wait(max(0, next_run - time.time())):
                        return
                # Serialize task creation with stop/resume so a stopped loop
                # cannot launch one last task after a UI has acknowledged stop.
                with self._lock:
                    if stop.is_set():
                        return
                    task_id = service.create_task(row["topic"], workflow=row["workflow_id"],
                                                  offline=row["offline"], path=row.get("path", ""))
                    self._update(loop_id, status="running", task_id=task_id,
                                 iteration=row["iteration"] + 1, next_run_at=None, last_error="",
                                 task_ids=[*row.get("task_ids", []), task_id])
        except Exception as exc:  # A scheduler failure must be visible, never silently die.
            with self._lock:
                if not stop.is_set():
                    row = self._loops[loop_id]
                    row.update(status="failed", last_error=str(redact(str(exc))), updated_at=_now())
                    try:
                        self._persist_locked()
                    except OSError:
                        pass

    def _start_loop(self, req: dict[str, Any]) -> dict[str, Any]:
        args = _workflow_args(req)
        iterations = req.get("max_iterations", 3)
        interval = req.get("interval_seconds", 60)
        if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= 100:
            raise ValueError("max_iterations 必须是 1 到 100 的整数")
        if (isinstance(interval, bool) or not isinstance(interval, (int, float))
                or not math.isfinite(interval) or interval < 1 or interval > 31_536_000):
            raise ValueError("interval_seconds 必须是 1 到 31536000 的有限数值")
        lid = uuid.uuid4().hex[:12]
        row = {"loop_id": lid, **args, "status": "running", "interval_seconds": interval,
               "max_iterations": iterations, "iteration": 0, "task_id": None, "task_ids": [],
               "next_run_at": None, "last_error": "", "created_at": _now(), "updated_at": _now()}
        with self._lock:
            self._loops[lid] = row
            try:
                self._persist_locked()
            except Exception:
                # Initial persistence must succeed before advertising a live
                # schedule. No worker has been launched at this point.
                self._loops.pop(lid, None)
                raise
        self._start_worker(lid)
        with self._lock:
            return copy.deepcopy(self._loops[lid])

    def _stop_loop(self, loop_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._loops[loop_id]
            event = self._stops.get(loop_id)
            if event:
                event.set()
            ctx = service.get(row.get("task_id"))
            if ctx is not None and ctx.status in {"pending", "running"}:
                service.pause(ctx.task_id)
            return self._update(loop_id, status="stopped", next_run_at=None)

    def _resume_loop(self, loop_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._loops[loop_id]
            ctx = service.get(row.get("task_id"))
            if row["status"] == "completed":
                raise ValueError("循环已达到迭代上限，请新建循环")
            if ctx is not None and ctx.status in {"failed", "cancelled"}:
                raise ValueError("当前任务失败或取消，请先显式重跑任务或新建循环")
            old = self._threads.get(loop_id)
            stop = self._stops.get(loop_id)
        if old and old.is_alive() and stop and stop.is_set():
            old.join(timeout=2)
            if old.is_alive():
                raise ValueError("循环正在停止，请稍后恢复")
        # This reattaches observation only: paused and approval tasks stay put.
        status = ctx.status if ctx and ctx.status in {"paused", "waiting_human"} else "running"
        self._update(loop_id, status=status, last_error="")
        self._start_worker(loop_id)
        with self._lock:
            return copy.deepcopy(self._loops[loop_id])

    def _export_session(self, req: dict[str, Any]) -> dict[str, Any]:
        from .agent_runtime import SessionStore
        sid = req.get("session_id", "")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
            raise ValueError("无效的 session_id")
        fmt = req.get("format", "markdown")
        if fmt not in {"markdown", "json"}:
            raise ValueError("format 必须为 markdown 或 json")
        store = SessionStore(self.data_dir / "sessions")
        source = store.root / f"{sid}.jsonl"
        if not source.is_file() or source.resolve().parent != store.root.resolve():
            raise ValueError("会话不存在或超出会话目录")
        events = store.read(sid)
        projected = store._project(sid, events)
        document = redact({"metadata": {"exported_at": _now(), "source_session_id": sid,
                                          "event_count": len(events), "format": fmt,
                                          "redaction": "credentials and authorization removed"},
                           "session": projected, "events": events})
        out = Path(req.get("dir") or (self.data_dir / "exports"))
        out.mkdir(parents=True, exist_ok=True)
        suffix = ".json" if fmt == "json" else ".md"
        target = out / f"session-{sid}-{uuid.uuid4().hex[:8]}{suffix}"
        if fmt == "json":
            text = json.dumps(document, ensure_ascii=False, indent=2)
        else:
            lines = [f"# 科研会话 {sid}", "", "## 导出信息", "",
                     *[f"- {key}: {value}" for key, value in document["metadata"].items()],
                     "", "## 会话状态", "", "```json",
                     json.dumps(document["session"], ensure_ascii=False, indent=2), "```", "",
                     "## 事件记录", ""]
            for index, event in enumerate(document["events"], 1):
                lines.extend([f"### {index}. {event.get('type', '')} · {event.get('timestamp', '')}",
                              "", "```json", json.dumps(event.get("payload", {}),
                                                         ensure_ascii=False, indent=2), "```", ""])
            text = "\n".join(lines)
        target.write_text(text, encoding="utf-8")
        return {"path": str(target.resolve()), "format": fmt, "session_id": sid}

    @staticmethod
    def _open_terminal(req: dict[str, Any]) -> dict[str, Any]:
        if set(req) - {"cmd"}:
            raise ValueError("terminal_open 仅打开项目终端，不接受自定义命令或路径")
        root = str(config.ROOT_DIR.resolve())
        if os.name == "nt":
            argv = [shutil.which("powershell.exe") or "powershell.exe", "-NoLogo", "-NoProfile", "-NoExit"]
            subprocess.Popen(argv, cwd=root, creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Terminal", root], cwd=root)
        else:
            terminal = next((shutil.which(name) for name in
                             ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm")
                             if shutil.which(name)), None)
            if not terminal:
                raise ValueError("未找到图形终端，请在项目目录手动打开 shell")
            subprocess.Popen([terminal], cwd=root)
        return {"opened": True, "path": root}

    def handle(self, req: dict[str, Any]) -> dict[str, Any]:
        try:
            cmd = req.get("cmd")
            if cmd == "workflows":
                result = {"workflows": workflows()}
            elif cmd == "workflow_run":
                args = _workflow_args(req)
                tid = service.create_task(args["topic"], workflow=args["workflow_id"],
                                          offline=args["offline"], path=args["path"])
                result = {"task_id": tid, "workflow_id": args["workflow_id"], "offline": args["offline"]}
            elif cmd == "workbench_status":
                result = {"processes": service.list_tasks(), "loops": self.loops(),
                          "warnings": [self._load_error] if self._load_error else []}
            elif cmd == "loop_list":
                result = {"loops": self.loops()}
            elif cmd == "loop_start":
                result = {"loop": self._start_loop(req)}
            elif cmd == "loop_stop":
                result = {"loop": self._stop_loop(req["loop_id"])}
            elif cmd == "loop_resume":
                result = {"loop": self._resume_loop(req["loop_id"])}
            elif cmd == "session_export":
                result = self._export_session(req)
            elif cmd == "terminal_open":
                result = self._open_terminal(req)
            else:
                raise ValueError(f"未知工作台命令：{cmd}")
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def close(self) -> None:
        """Stop observing without changing durable state or granting approvals."""
        with self._lock:
            for stop in self._stops.values():
                stop.set()
            workers = list(self._threads.values())
        for worker in workers:
            worker.join(timeout=2)


_instance: Workbench | None = None
_instance_lock = threading.Lock()


def handle(req: dict[str, Any]) -> dict[str, Any]:
    global _instance
    try:
        with _instance_lock:
            if _instance is None:
                _instance = Workbench()
            instance = _instance
        return instance.handle(req)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
