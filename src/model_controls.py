"""Read-only model previews and compare-before-save profile controls.

Effort availability is validation by our existing wire adapters. It is not
evidence that a remote provider accepts the model or supplies that capability.
"""
from __future__ import annotations

import copy
import hashlib
import json

from . import model_protocols, model_registry, reasoning_receipt, settings
from .workbench import redact


def _stage(stage):
    if stage not in model_registry.ROUTES:
        raise ValueError("无效的科研阶段")


def _revision():
    # Include both disk preferences and effective environment/runtime values.
    # The digest is opaque; keys and endpoint URLs never appear in the catalog.
    values = {"settings": settings.load(), "legacy": model_registry._legacy(None)}
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _item(profile_id, profile):
    efforts = []
    for value in model_registry.EFFORTS:
        candidate = {**profile, "reasoning_effort": value}
        wire, state = [], "unsupported"
        try:
            cap = int(candidate.get("max_output_tokens") or 8192)
            visible = max(1, min(cap, 2048))
            controls, total, _exclude_temp = model_protocols.thinking(candidate, visible)
            enabled, reason = True, ""
            # Describe the controls the adapter just produced, so the dialog can
            # preview the parameter that will really be sent - not the dial label.
            receipt = reasoning_receipt.describe(candidate, controls,
                                                 reserved=max(0, total - visible),
                                                 total_limit=total)
            wire, state = receipt["wire"], receipt["state"]
        except (ValueError, TypeError, KeyError) as error:
            enabled, reason = False, redact(str(error))[:180]
        efforts.append({"value": value, "enabled": enabled, "reason": reason,
                        "wire": wire, "state": state})
    return {"id": profile_id, "label": redact(str(profile.get("label") or ("默认连接" if profile_id == "legacy" else profile_id)))[:80],
            "model": redact(str(profile.get("model") or ""))[:120],
            "connection_id": profile.get("connection_id", "legacy"),
            "protocol": profile.get("protocol", "chat_completions"),
            "reasoning_effort": profile.get("reasoning_effort", "auto"),
            "thinking_style": profile.get("thinking_style", "auto"),
            "configured": bool(profile.get("model") and profile.get("base_url")),
            "efforts": efforts, "capability_note": "档位仅经本地适配器校验，供应商能力尚未验证"}


def _catalog_once(stage):
    _stage(stage)
    with settings._LOCK:
        described = model_registry.describe()
        items = [_item("legacy", model_registry._legacy(stage))]
        connections = {row["id"]: row for row in settings.load().get("model_connections", [])}
        for saved in described.get("profiles", []):
            cid = saved.get("connection_id", "legacy")
            connection = model_registry._legacy(stage) if cid == "legacy" else connections.get(cid, {})
            profile = {**model_registry.PROFILE_DEFAULTS, **connection, **saved,
                       "protocol": connection.get("protocol", "chat_completions")}
            items.append(_item(saved["id"], profile))
        return {"stage": stage,
                "active_id": described["resolved"][stage]["profile_id"],
                "stages": list(model_registry.ROUTES), "items": items,
                "scope": "保存用于后续请求；已有模型会话若检测到配置变化会停止，需新建或分叉会话"}


def model_control_catalog(stage="planning"):
    _stage(stage)
    with settings._LOCK:
        for _ in range(3):
            before = _revision()
            catalog = _catalog_once(stage)
            after = _revision()
            if before == after:
                return {**catalog, "revision": after}
    raise ValueError("模型配置正在变化，请稍后刷新")


def model_control_apply(stage, profile_id, expected_revision, effort=None):
    _stage(stage)
    with settings.transaction():
        catalog = model_control_catalog(stage)
        if not isinstance(expected_revision, str) or expected_revision != catalog["revision"]:
            raise ValueError("模型配置已变化，请刷新后重新选择")
        target = next((row for row in catalog["items"] if row["id"] == profile_id), None)
        if target is None:
            raise ValueError("模型配置不存在，请刷新列表")
        if effort is not None:
            option = next((row for row in target["efforts"] if row["value"] == effort), None)
            if option is None or not option["enabled"]:
                raise ValueError(option["reason"] if option else "无效的思考档位")
        current = settings.load()
        routes = copy.deepcopy(current.get("model_routes") or {})
        routes[stage] = profile_id
        patch = {"model_routes": routes}
        if effort is not None:
            if profile_id == "legacy":
                patch["llm_reasoning_effort"] = effort
            else:
                profiles = copy.deepcopy(current.get("model_profiles") or [])
                for row in profiles:
                    if row["id"] == profile_id:
                        row["reasoning_effort"] = effort
                patch["model_profiles"] = profiles
        settings.save(patch)
        return model_control_catalog(stage)
