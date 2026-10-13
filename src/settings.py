"""运行设置持久化：LLM 端点与筛选阈值可在界面修改，写入 data/settings.json。

优先级：环境变量 > settings.json > 代码默认值。
"""
from __future__ import annotations

import json
import copy
import os
import threading
import uuid
from contextlib import contextmanager
from typing import Any

from .config import DATA_DIR

_PATH = DATA_DIR / "settings.json"
_LOCK = threading.RLock()
_TRANSACTION = threading.local()


@contextmanager
def transaction():
    """Serialize read/validate/save across desktop and bridge processes.

    Nested saves on the same thread reuse the process lock. Ordinary reads
    remain side-effect free and do not create a directory or lock file.
    """
    from .session_lock import session_lock
    with _LOCK:
        path = str(_PATH.resolve())
        owned = getattr(_TRANSACTION, "paths", set())
        if path in owned:
            yield
            return
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        with session_lock(_PATH):
            _TRANSACTION.paths = {*owned, path}
            try:
                yield
            finally:
                _TRANSACTION.paths = owned

_DEFAULTS: dict[str, Any] = {
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "",
    "llm_model_fast": "",    # 双轨路由·快模型（可选，粗筛/批量）
    "llm_model_strong": "",  # 双轨路由·强模型（可选，终审/综述）
    "embedding_base_url": "",  # 向量检索端点（可选，未配置则 lexical 评分兜底）
    "embedding_api_key": "",
    "embedding_model": "",
    "lit_threshold": 0.18,   # 文献相关性保留阈值
    "lit_max_round": 3,      # 递归最大轮次
    "lit_per_page": 25,      # 每轮检索量
    "skills_active": [],     # 启用中的技能包（skills.py）
    "skills_disabled": [],
    "git_archive": True,     # /export 后自动入研究档案库（research_repo.py）
    "enable_crossref": True,  # F1 首轮双源并行检索（OpenAlex + CrossRef）
    "tool_permissions": {},  # Agent Harness 工具权限策略（通配模式→allow/ask/deny）
    "agent_permission_tier": "workspace-write",  # 科研权限三档门：read-only/workspace-write/full-access
    "llm_enabled": True,
    "plugins_enabled": True,
    "plugins_disabled": [],
    "mcp_enabled": True,
    "ui_animations": True,
    "desktop_inspector": True,
    "default_offline": True,
    "workspace_mode": "research",
    # Monotonic compare-and-swap revision for concurrent desktop/TUI mode saves.
    "workspace_mode_revision": 0,
    "llm_protocol": "chat_completions",
    "llm_auth_style": "auto",
    "llm_thinking_style": "auto",
    "llm_reasoning_effort": "auto",
    "llm_thinking_budget": 4096,
    "llm_max_output_tokens": 8192,
    "llm_context_window": 32768,
    "llm_cache_enabled": True,
    "model_connections": [],
    "model_profiles": [],
    "model_routes": {},
    "agent_execution": "adaptive",
    "agent_max_steps": 16,
    "agent_token_budget": 100000,
    "experimental_message_queue": False,
    "experimental_inflight_steering": False,
    "experimental_evidence_reviews": False,
    "memory_enabled": True,
    "memory_context_chars": 2000,
    "subagents_enabled": True,
    "subagent_max_parallel": 4,
    "hooks_enabled": True,
    "browser_control_enabled": False,
    "computer_control_enabled": False,
    "keyboard_shortcuts": {},
    "appearance_font_scale": 1.0,
    "appearance_font_family": "system",
    "appearance_code_font": "cascadia",
    "appearance_density": "comfortable",
    "appearance_dark_mode": True,
}

BOOLEAN_SETTINGS = {key for key, value in _DEFAULTS.items() if isinstance(value, bool)}


def load() -> dict[str, Any]:
    """读取设置，缺失项回退默认值。"""
    with _LOCK:
        defaults = copy.deepcopy(_DEFAULTS)
        if _PATH.exists():
            try:
                data = json.loads(_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {**defaults, **data}
            except (json.JSONDecodeError, OSError):
                pass
        return defaults


def public_settings() -> dict[str, Any]:
    result = load()
    result.pop('_inflight_steering_epoch', None)
    result["llm_api_key"] = "已配置" if result.get("llm_api_key") else ""
    result["embedding_api_key"] = "已配置" if result.get("embedding_api_key") else ""
    for connection in result.get("model_connections", []):
        connection["has_api_key"] = bool(connection.get("api_key"))
        connection["api_key"] = "已配置" if connection.get("api_key") else ""
    return result


def _validate(values: dict[str, Any]) -> None:
    if not isinstance(values, dict):
        raise ValueError("设置必须为对象")
    if "workspace_mode" in values:
        from .workspace_modes import normalize_mode
        if normalize_mode(values["workspace_mode"]) != values["workspace_mode"]:
            raise ValueError("workspace_mode 须为 general 或 research")
    if "workspace_mode_revision" in values and (
            type(values["workspace_mode_revision"]) is not int or values["workspace_mode_revision"] < 0):
        raise ValueError("workspace_mode_revision 必须为非负整数")
    if "keyboard_shortcuts" in values:
        from .workspace_features import validate_shortcuts
        validate_shortcuts(values["keyboard_shortcuts"])
    for key, lo, hi in (("memory_context_chars", 0, 6000), ("subagent_max_parallel", 1, 4)):
        if key in values and (type(values[key]) is not int or not lo <= values[key] <= hi):
            raise ValueError(f"{key} 必须在 {lo}–{hi} 之间")
    if "appearance_font_scale" in values and (type(values["appearance_font_scale"]) not in {float, int}
            or values["appearance_font_scale"] not in (0.9, 1.0, 1.1, 1.2)):
        raise ValueError("字号比例必须为 0.9、1.0、1.1 或 1.2")
    if "appearance_font_family" in values and (not isinstance(values["appearance_font_family"], str)
            or values["appearance_font_family"] not in {"system", "yahei"}):
        raise ValueError("正文字体取值无效")
    if "appearance_code_font" in values and (not isinstance(values["appearance_code_font"], str)
            or values["appearance_code_font"] not in {"cascadia", "consolas"}):
        raise ValueError("代码字体取值无效")
    if "appearance_density" in values and values["appearance_density"] not in {"comfortable", "compact"}:
        raise ValueError("界面密度取值无效")
    for key in BOOLEAN_SETTINGS & values.keys():
        if not isinstance(values[key], bool):
            raise ValueError(f"{key} 必须为 on/off 布尔值")
    for key, minimum, maximum in (("lit_threshold", 0, 1), ("lit_max_round", 1, 20), ("lit_per_page", 1, 200)):
        if key in values and (isinstance(values[key], bool) or
                not isinstance(values[key], (int, float)) or not minimum <= values[key] <= maximum):
            raise ValueError(f"{key} 必须在 {minimum}–{maximum} 之间")
    for key in ("llm_base_url", "llm_api_key", "llm_model", "llm_model_fast", "llm_model_strong",
                "embedding_base_url", "embedding_api_key", "embedding_model"):
        if key in values and (not isinstance(values[key], str) or len(values[key]) > 8192):
            raise ValueError(f"{key} 必须为有效文本")
    for key in ("skills_active", "skills_disabled", "plugins_disabled"):
        if key in values and (not isinstance(values[key], list) or
                              any(not isinstance(v, str) for v in values[key])):
            raise ValueError(f"{key} 必须为名称列表")
    from .model_registry import PROTOCOLS, THINKING_STYLES, EFFORTS, AUTH_STYLES, validate
    for key, allowed in (("llm_protocol", PROTOCOLS), ("llm_thinking_style", THINKING_STYLES),
                         ("llm_reasoning_effort", EFFORTS), ("llm_auth_style", AUTH_STYLES),
                         ("agent_execution", ("adaptive", "planned"))):
        if key in values and values[key] not in allowed:
            raise ValueError(f"{key} 取值无效")
    for key, minimum, maximum in (("llm_thinking_budget", 0, 2_000_000),
                                  ("llm_max_output_tokens", 128, 2_000_000),
                                  ("llm_context_window", 1024, 2_000_000),
                                  ("agent_max_steps", 1, 64), ("agent_token_budget", 1024, 10_000_000)):
        if key in values and (type(values[key]) is not int or not minimum <= values[key] <= maximum):
            raise ValueError(f"{key} 超出有效范围")
    if {"model_connections", "model_profiles", "model_routes"} & values.keys():
        validate({**load(), **values})


def save(settings: dict[str, Any]) -> None:
    """保存设置并即时生效（写回 config/llm 模块运行时变量）。"""
    with transaction():
        _validate(settings)
        current = load()
        merged = {**current, **{key: value for key, value in settings.items()
                               if key not in {'_inflight_steering_epoch', 'workspace_mode_revision'}}}
        revision = current.get('workspace_mode_revision', 0)
        revision = revision if type(revision) is int and revision >= 0 else 0
        if 'workspace_mode' in settings and settings['workspace_mode'] != current['workspace_mode']:
            revision += 1
        merged['workspace_mode_revision'] = revision
        if settings.get('experimental_inflight_steering') is False and current.get('experimental_inflight_steering') is True:
            merged['_inflight_steering_epoch'] = uuid.uuid4().hex
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = _PATH.with_name(f".{_PATH.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temporary, _PATH)
        finally:
            temporary.unlink(missing_ok=True)
        _apply(merged, changed=set(settings))


def _apply(merged: dict[str, Any], startup: bool = False, changed: set[str] | None = None) -> None:
    from . import config
    changed = set(merged) if changed is None else changed
    for key, attr in (("llm_base_url", "LLM_BASE_URL"), ("llm_api_key", "LLM_API_KEY"),
                      ("llm_model", "LLM_MODEL"), ("llm_model_fast", "LLM_MODEL_FAST"),
                      ("llm_model_strong", "LLM_MODEL_STRONG")):
        if (startup and not os.getenv("GEZHI_" + attr)) or (not startup and key in changed):
            setattr(config, attr, merged.get(key, ""))
    if startup or "llm_enabled" in changed:
        config.LLM_ENABLED = bool(merged.get("llm_enabled", True))
    for key in ("llm_protocol", "llm_auth_style", "llm_thinking_style", "llm_reasoning_effort",
                "llm_thinking_budget", "llm_max_output_tokens", "llm_context_window", "llm_cache_enabled"):
        attr = key.upper()
        if (startup and "GEZHI_" + attr not in os.environ) or (not startup and key in changed):
            setattr(config, attr, merged[key])
    for key, attr, environment, convert in (
        ("lit_threshold", "LIT_SCORE_THRESHOLD", "GEZHI_LIT_TAU", float),
        ("lit_max_round", "LIT_MAX_ROUND", "GEZHI_LIT_MAX_ROUND", int),
        ("lit_per_page", "LIT_PER_PAGE", "GEZHI_LIT_PER_PAGE", int),
        ("enable_crossref", "LIT_ENABLE_CROSSREF", "GEZHI_LIT_CROSSREF", bool),
    ):
        if (startup and environment not in os.environ) or (not startup and key in changed):
            setattr(config, attr, convert(merged[key]))


def apply_at_startup() -> None:
    """启动时把持久化设置应用到运行时配置。"""
    _apply(load(), startup=True)
