"""Declarative runtime hooks: audit records or narrow a tool authorization.

Hooks never execute arbitrary code, shell commands or nested agents. Only
before_tool can block. Logs contain identity and outcome, never tool arguments,
tool output, prompts, environment variables or authentication credentials.
"""
from __future__ import annotations

import fnmatch
import json
import re
import threading
import time
import uuid
from typing import Any

from . import config, settings
from .state_store import write_json

CONFIG_PATH = config.DATA_DIR / "lifecycle_hooks.json"
LOG_PATH = config.DATA_DIR / "lifecycle_hooks.jsonl"
_LOCK = threading.RLock()
EVENTS = ("before_tool", "after_tool", "completed")
ACTIONS = ("record", "block_tool")


def _validate(hook: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(hook, dict) or set(hook) - {"id", "name", "event", "action", "enabled", "tool", "message"}:
        raise ValueError("Hook 仅支持声明式配置字段")
    row = {"id": uuid.uuid4().hex[:12], "name": "", "enabled": True,
           "tool": "*", "message": "", **(previous or {}), **hook}
    if not isinstance(row["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", row["id"]):
        raise ValueError("Hook ID 无效")
    if row.get("event") not in EVENTS or row.get("action") not in ACTIONS:
        raise ValueError("Hook 事件或动作无效；仅支持 record / block_tool")
    if row["action"] == "block_tool" and row["event"] != "before_tool":
        raise ValueError("block_tool 只能用于 before_tool")
    if type(row["enabled"]) is not bool:
        raise ValueError("Hook enabled 必须为布尔值")
    for key, maximum in (("name", 120), ("tool", 200), ("message", 1000)):
        if not isinstance(row[key], str) or len(row[key]) > maximum:
            raise ValueError(f"Hook {key} 不是有效文本")
    if not row["tool"]:
        raise ValueError("Hook tool 匹配式不能为空")
    return row


def _load() -> list[dict[str, Any]]:
    if not CONFIG_PATH.exists():
        return []
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), list):
        raise ValueError("Hook 配置格式无效")
    rows = [_validate(hook) for hook in data["hooks"]]
    if len(rows) > 100 or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Hook 数量超过上限或 ID 重复")
    return rows


def _logs(limit: int = 100) -> list[dict[str, Any]]:
    from collections import deque
    if not LOG_PATH.exists():
        return []
    rows = []
    with LOG_PATH.open(encoding="utf-8") as stream:
        for line in deque(stream, maxlen=limit):
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except ValueError:
                continue
    return rows


def handle(req: dict[str, Any]) -> dict[str, Any]:
    try:
        with _LOCK:
            hooks = _load()
            command = req.get("cmd")
            if command == "hook_save":
                incoming = req.get("hook")
                previous = next((h for h in hooks if isinstance(incoming, dict) and h["id"] == incoming.get("id")), None)
                row = _validate(incoming, previous)
                hooks = [h for h in hooks if h["id"] != row["id"]] + [row]
                if len(hooks) > 100:
                    raise ValueError("最多保存 100 条 Hook")
                write_json(CONFIG_PATH, {"hooks": hooks})
            elif command == "hook_delete":
                if not any(h["id"] == req.get("id") for h in hooks):
                    raise ValueError("Hook 不存在")
                hooks = [h for h in hooks if h["id"] != req.get("id")]
                write_json(CONFIG_PATH, {"hooks": hooks})
            elif command not in {"hook_list", "hook_logs"}:
                raise ValueError("未知 Hook 命令")
            return {"ok": True, "hooks": hooks, "logs": _logs(),
                    "enabled": settings.load().get("hooks_enabled", True)}
    except (ValueError, OSError, UnicodeError) as exc:
        return {"ok": False, "error": str(exc)}


def run(event: str, context: dict[str, Any]) -> dict[str, Any]:
    if event not in EVENTS:
        raise ValueError("未知 Hook 生命周期事件")
    if not settings.load().get("hooks_enabled", True):
        return {"blocked": False, "logs": []}
    with _LOCK:
        hooks = _load()
        tool = str(context.get("tool") or "")
        scope = str(context.get("permission_key") or tool)
        matched = [hook for hook in hooks if hook["enabled"] and hook["event"] == event
                   and (event == "completed" or fnmatch.fnmatchcase(tool, hook["tool"])
                        or fnmatch.fnmatchcase(scope, hook["tool"]))]
        blocked = any(hook["action"] == "block_tool" for hook in matched)
        logs = []
        for hook in matched:
            row = {"id": uuid.uuid4().hex[:12], "hook_id": hook["id"],
                   "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "session_id": str(context.get("session_id") or ""),
                   "event": event, "tool": tool,
                   "tool_call_id": str(context.get("tool_call_id") or ""),
                   "action": hook["action"], "message": hook["message"],
                   "blocked": hook["action"] == "block_tool"}
            if event == "after_tool":
                row["tool_ok"] = context.get("tool_ok") is True
                row["executed"] = context.get("executed") is True
            if event == "completed":
                row["status"] = str(context.get("status") or "")
            logs.append(row)
        if logs:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as stream:
                for row in logs:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"blocked": blocked, "logs": logs,
                "message": next((h["message"] or "声明式 Hook 阻止了此工具调用"
                                 for h in matched if h["action"] == "block_tool"), "")}
