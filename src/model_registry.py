"""Connections, model capability profiles and research-stage routes."""
from __future__ import annotations

import copy
import re
from urllib.parse import urlsplit
from typing import Any

from . import config, settings

PROTOCOLS = ("chat_completions", "responses", "anthropic_messages", "gemini", "ollama")
THINKING_STYLES = ("auto", "none", "openai_effort", "anthropic_budget", "anthropic_adaptive",
                   "gemini_budget", "gemini_level", "deepseek_thinking", "ollama_think")
EFFORTS = ("auto", "off", "minimal", "low", "medium", "high", "xhigh", "max")
AUTH_STYLES = ("auto", "bearer", "anthropic", "gemini", "none")
ROUTES = ("default", "fast", "strong", "planning", "extraction", "synthesis", "review")
PROFILE_DEFAULTS = {"thinking_style": "auto", "reasoning_effort": "auto", "thinking_budget": 4096,
                    "max_output_tokens": 8192, "context_window": 32768, "prompt_cache": True}
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def _identifier(value: Any) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value) or value == "legacy":
        raise ValueError("名称使用 1–64 位字母、数字、下划线或短横线；legacy 为内置配置")


def validate(values: dict[str, Any]) -> None:
    connections = values.get("model_connections", [])
    profiles = values.get("model_profiles", [])
    routes = values.get("model_routes", {})
    if not isinstance(connections, list) or not isinstance(profiles, list) or not isinstance(routes, dict):
        raise ValueError("连接和模型配置必须为列表，用途路由必须为对象")
    seen_connections, seen_profiles = {"legacy"}, {"legacy"}
    for row in connections:
        if not isinstance(row, dict):
            raise ValueError("连接配置必须为对象")
        _identifier(row.get("id"))
        if set(row) - {"id", "label", "protocol", "base_url", "api_key", "auth_style"}:
            raise ValueError("连接包含未声明字段")
        if row["id"] in seen_connections:
            raise ValueError("连接名称重复")
        seen_connections.add(row["id"])
        if row.get("protocol", "chat_completions") not in PROTOCOLS:
            raise ValueError("不支持的模型协议")
        url = urlsplit(str(row.get("base_url", "")))
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ValueError("服务地址须为 HTTP(S) 基础地址，不能包含凭据、查询参数或片段")
        if row.get("auth_style", "auto") not in AUTH_STYLES:
            raise ValueError("不支持的认证方式")
        for key in ("api_key", "label"):
            if key in row and (not isinstance(row[key], str) or len(row[key]) > 8192):
                raise ValueError(f"{key} 必须为有效文本")
    for row in profiles:
        if not isinstance(row, dict):
            raise ValueError("模型配置必须为对象")
        _identifier(row.get("id"))
        if set(row) - {"id", "label", "connection_id", "model", *PROFILE_DEFAULTS}:
            raise ValueError("模型配置包含未声明字段")
        if row["id"] in seen_profiles:
            raise ValueError("模型配置名称重复")
        seen_profiles.add(row["id"])
        if row.get("connection_id") not in seen_connections:
            raise ValueError("模型引用了不存在的连接")
        if not isinstance(row.get("model"), str) or not row["model"].strip():
            raise ValueError("请填写服务端模型名称")
        if row.get("thinking_style", "auto") not in THINKING_STYLES or row.get("reasoning_effort", "auto") not in EFFORTS:
            raise ValueError("思考模式或强度无效")
        for key, lower in (("thinking_budget", 0), ("max_output_tokens", 128), ("context_window", 1024)):
            value = row.get(key, PROFILE_DEFAULTS[key])
            if type(value) is not int or not lower <= value <= 2_000_000:
                raise ValueError(f"{key} 超出有效范围")
        if not isinstance(row.get("prompt_cache", True), bool):
            raise ValueError("缓存提示开关必须为布尔值")
    for stage, profile in routes.items():
        if stage not in ROUTES or profile not in seen_profiles:
            raise ValueError("用途路由引用了未知阶段或模型配置")


def _legacy(tier: str | None) -> dict[str, Any]:
    aliases = {"planning": "strong", "review": "strong", "synthesis": "strong", "extraction": "fast"}
    tier = aliases.get(tier, tier)
    model = (config.LLM_MODEL_FAST if tier == "fast" else config.LLM_MODEL_STRONG if tier == "strong" else "") or config.LLM_MODEL
    return {"connection_id": "legacy", "profile_id": "legacy", "label": "默认连接",
            "protocol": getattr(config, "LLM_PROTOCOL", "chat_completions"),
            "base_url": config.LLM_BASE_URL, "api_key": config.LLM_API_KEY,
            "model": model, "auth_style": getattr(config, "LLM_AUTH_STYLE", "auto"),
            "thinking_style": getattr(config, "LLM_THINKING_STYLE", "auto"),
            "reasoning_effort": getattr(config, "LLM_REASONING_EFFORT", "auto"),
            "thinking_budget": getattr(config, "LLM_THINKING_BUDGET", 4096),
            "max_output_tokens": getattr(config, "LLM_MAX_OUTPUT_TOKENS", 8192),
            "context_window": getattr(config, "LLM_CONTEXT_WINDOW", 32768),
            "prompt_cache": getattr(config, "LLM_CACHE_ENABLED", True)}


def resolve(tier: str | None = None) -> dict[str, Any]:
    values = settings.load()
    routes = values.get("model_routes") or {}
    stage = tier or "default"
    aliases = {"planning": "strong", "review": "strong", "synthesis": "strong", "extraction": "fast"}
    chosen = routes.get(stage) or routes.get(aliases.get(stage, "")) or routes.get("default", "legacy")
    if chosen == "legacy":
        return _legacy(tier)
    profile = next((p for p in values.get("model_profiles", []) if p.get("id") == chosen), None)
    if profile is None:
        raise ValueError(f"用途 {stage} 引用了不存在的模型配置")
    cid = profile["connection_id"]
    connection = (_legacy(tier) if cid == "legacy" else next(
        (c for c in values.get("model_connections", []) if c.get("id") == cid), None))
    if connection is None:
        raise ValueError("模型连接不存在")
    return {**PROFILE_DEFAULTS, **copy.deepcopy(connection), **copy.deepcopy(profile),
            "connection_id": cid, "profile_id": profile["id"],
            "protocol": connection.get("protocol", "chat_completions"),
            "auth_style": connection.get("auth_style", "auto"), "api_key": connection.get("api_key", "")}


def describe() -> dict[str, Any]:
    values = settings.public_settings()
    resolved = {}
    for stage in ROUTES:
        row = resolve(stage)
        row.pop("api_key", None)
        resolved[stage] = row
    return {"connections": values.get("model_connections", []),
            "profiles": values.get("model_profiles", []), "routes": values.get("model_routes", {}),
            "resolved": resolved, "protocols": list(PROTOCOLS), "thinking_styles": list(THINKING_STYLES),
            "efforts": list(EFFORTS), "stages": list(ROUTES)}


def _save_collection(key: str, row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("配置必须为对象")
    _identifier(row.get("id"))
    allowed = ({"id", "label", "protocol", "base_url", "api_key", "auth_style", "has_api_key"}
               if key == "model_connections" else {"id", "label", "connection_id", "model", *PROFILE_DEFAULTS})
    if set(row) - allowed:
        raise ValueError("配置包含不支持的字段：" + ", ".join(sorted(set(row) - allowed)))
    row = {k: v for k, v in row.items() if k != "has_api_key" and not (k == "api_key" and v == "已配置")}
    with settings.transaction():
        values = settings.load()
        records = values.get(key, [])
        previous = next((r for r in records if r["id"] == row["id"]), {})
        merged = {**previous, **row}
        if key == "model_connections":
            merged.setdefault("protocol", "chat_completions")
            merged.setdefault("auth_style", "auto")
            merged.setdefault("api_key", "")
        else:
            merged = {**PROFILE_DEFAULTS, **merged}
        records = [r for r in records if r["id"] != row["id"]] + [merged]
        settings.save({key: records})
    return describe()


def save_connection(row: dict[str, Any]) -> dict[str, Any]:
    return _save_collection("model_connections", row)


def save_profile(row: dict[str, Any]) -> dict[str, Any]:
    return _save_collection("model_profiles", row)


def set_route(stage: str, profile_id: str) -> dict[str, Any]:
    with settings.transaction():
        routes = settings.load().get("model_routes", {})
        routes[stage] = profile_id
        settings.save({"model_routes": routes})
    return describe()


def delete_connection(identifier: str) -> dict[str, Any]:
    with settings.transaction():
        values = settings.load()
        if any(p.get("connection_id") == identifier for p in values.get("model_profiles", [])):
            raise ValueError("连接仍被模型配置使用，请先更改模型配置")
        settings.save({"model_connections": [c for c in values.get("model_connections", []) if c["id"] != identifier]})
    return describe()


def delete_profile(identifier: str) -> dict[str, Any]:
    with settings.transaction():
        values = settings.load()
        if identifier in values.get("model_routes", {}).values():
            raise ValueError("模型配置仍被用途路由使用，请先更改路由")
        settings.save({"model_profiles": [p for p in values.get("model_profiles", []) if p["id"] != identifier]})
    return describe()
