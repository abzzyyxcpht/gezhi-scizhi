"""Explicit browser/computer bindings to installed, configured MCP servers.

This module does not install software, invent available capabilities or launch
servers during status reads. Host calls are explicit user actions; model calls
must pass AgentRuntime's real MCP target permission check first.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from . import config, mcp, settings
from .state_store import write_json

CONFIG_PATH = config.DATA_DIR / "control_bindings.json"
_LOCK = threading.RLock()
KINDS = ("browser", "computer")


def _load() -> dict[str, dict[str, Any]]:
    defaults = {kind: {"server": "", "enabled": False} for kind in KINDS}
    if not CONFIG_PATH.exists():
        return defaults
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("bindings"), dict):
        raise ValueError("控制绑定配置格式无效")
    for kind, binding in data["bindings"].items():
        if kind not in KINDS:
            raise ValueError("未知控制类型")
        defaults[kind] = _validate_binding(binding)
    return defaults


def _validate_binding(binding: Any) -> dict[str, Any]:
    if not isinstance(binding, dict) or set(binding) - {"server", "enabled"}:
        raise ValueError("binding 仅支持 server 和 enabled 字段")
    if not isinstance(binding.get("server", ""), str) or type(binding.get("enabled", False)) is not bool:
        raise ValueError("server 须为文本，enabled 须为布尔值")
    server = binding.get("server", "").strip()
    if len(server) > 80:
        raise ValueError("服务器名称过长")
    return {"server": server, "enabled": binding.get("enabled", False)}


def status() -> dict[str, Any]:
    with _LOCK:
        bindings = _load()
    preferences = settings.load()
    servers = {server["name"]: server for server in mcp.load_servers()}
    controls = {}
    for kind, binding in bindings.items():
        configured = bool(binding["server"] and binding["server"] in servers)
        feature_enabled = preferences.get(f"{kind}_control_enabled", False)
        if not configured:
            reason = "尚未绑定已配置的 MCP 服务器"
        elif not feature_enabled:
            reason = "控制功能已在设置中停用"
        elif not binding["enabled"]:
            reason = "控制绑定已停用"
        elif not preferences.get("mcp_enabled", True):
            reason = "MCP 已在设置中停用"
        elif not servers[binding["server"]].get("enabled", True):
            reason = "绑定的 MCP 服务器已停用"
        else:
            reason = "已配置；连接能力需显式探测"
        available = bool(configured and feature_enabled and binding["enabled"]
                         and preferences.get("mcp_enabled", True)
                         and servers[binding["server"]].get("enabled", True))
        controls[kind] = {**binding, "configured": configured, "available": available,
                          "feature_enabled": bool(feature_enabled), "reason": reason,
                          "connection": "not_probed"}
    return {"ok": True, "controls": controls}


def call(kind: str, tool: str, arguments: dict[str, Any] | None = None,
         *, expected_server: str | None = None) -> dict[str, Any]:
    if kind not in KINDS:
        return {"ok": False, "error": "控制类型须为 browser 或 computer"}
    if not isinstance(tool, str) or not tool.strip() or not isinstance(arguments if arguments is not None else {}, dict):
        return {"ok": False, "error": "tool 须为非空文本，arguments 须为对象"}
    with _LOCK:
        state = status()["controls"][kind]
        if not state["available"]:
            return {"ok": False, "kind": kind, "error": state["reason"]}
        server = state["server"]
        if expected_server is not None and server != expected_server:
            return {"ok": False, "error": "控制绑定已变化；需重新确认实际 MCP 服务器"}
        # Capture the target while holding the binding lock. Later rebindings
        # cannot redirect this call to a server the user did not approve.
    return {**mcp.call_named(server, tool, arguments), "kind": kind}


def handle(req: dict[str, Any]) -> dict[str, Any]:
    try:
        command = req.get("cmd")
        if command in {"mcp_config", "mcp_save", "mcp_delete", "mcp_probe"}:
            return mcp.handle(req)
        if command == "control_status":
            return status()
        if command == "control_call":
            return call(req.get("kind"), req.get("tool"), req.get("arguments"))
        if command != "control_save" or req.get("kind") not in KINDS:
            raise ValueError("未知控制命令或类型")
        with _LOCK:
            bindings = _load()
            incoming = req.get("binding")
            if not isinstance(incoming, dict):
                raise ValueError("binding 必须为对象")
            binding = _validate_binding({**bindings[req["kind"]], **incoming})
            if binding["server"] and binding["server"] not in {s["name"] for s in mcp.load_servers()}:
                raise ValueError("请先保存指定 MCP 服务器配置")
            if binding["enabled"] and not binding["server"]:
                raise ValueError("启用控制前须绑定 MCP 服务器")
            bindings[req["kind"]] = binding
            write_json(CONFIG_PATH, {"bindings": bindings})
        return status()
    except (ValueError, OSError, UnicodeError) as exc:
        return {"ok": False, "error": str(exc)}
