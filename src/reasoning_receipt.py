"""Auditable trail for a reasoning-effort choice: intent, wire, provider report.

A dial that only stores a preference is easy to draw and easy to lie about. The
three stages are different facts, and conflating them is how a UI ends up
claiming that a model "thought harder" because a button moved:

1. **requested** — the preference saved for the profile;
2. **wire** — what the built request actually carries (or deliberately omits);
3. **reported** — what the provider returned in its usage block, if anything.

This module derives stage 2 from the real payload produced by
``model_protocols.build_request`` rather than from the profile, so a receipt
cannot describe a parameter that was never sent. Stage 3 is attached later by
the caller that has the provider response.

Nothing here performs IO, reads settings, or contacts a provider.
"""
from __future__ import annotations

from typing import Any

# Every reasoning control this product can emit, per wire protocol. The scan is
# deliberately explicit: a new adapter must register its key here or the receipt
# will honestly report "no reasoning control found" instead of guessing.
_WIRE_KEYS: dict[str, tuple[tuple[str, ...], ...]] = {
    "chat_completions": (
        ("reasoning_effort",),
        ("reasoning", "effort"),
        ("thinking", "type"),
        ("think",),
    ),
    "responses": (
        ("reasoning", "effort"),
        ("thinking", "type"),
    ),
    "anthropic_messages": (
        ("thinking", "type"),
        ("thinking", "budget_tokens"),
        ("output_config", "effort"),
    ),
    "gemini": (
        ("generationConfig", "thinkingConfig", "thinkingLevel"),
        ("generationConfig", "thinkingConfig", "thinkingBudget"),
    ),
    "ollama": (
        ("think",),
    ),
}

# The wire value that means "we asked for no reasoning at all".
_DISABLED = {"none", "disabled", "false"}

STATES = ("sent", "budget", "omitted", "unsupported")


def _dig(payload: Any, path: tuple[str, ...]) -> tuple[bool, Any]:
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
    return True, node


def _flat(value: Any) -> str:
    """Render a wire value without inventing precision."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    return str(value)


def _scan(protocol: str, payload: dict) -> list[tuple[str, str]]:
    """Return ``(dotted_key, rendered_value)`` for each control present."""
    found: list[tuple[str, str]] = []
    for path in _WIRE_KEYS.get(protocol, ()):
        present, value = _dig(payload, path)
        if present:
            found.append((".".join(path), _flat(value)))
    return found


def describe(profile: dict, payload: dict, *, reserved: int = 0,
             total_limit: int | None = None) -> dict[str, Any]:
    """Describe what the wire request really carries for thinking controls.

    ``payload`` must be the same dict that will be (or was) serialized as the
    HTTP body. ``reserved`` is the token budget the adapter carved out of the
    generation limit for thinking; it is only meaningful when the adapter
    reported it.
    """
    protocol = str(profile.get("protocol") or "chat_completions")
    requested = str(profile.get("reasoning_effort") or "auto")
    style = str(profile.get("thinking_style") or "auto")
    controls = _scan(protocol, payload)
    keys = [key for key, _ in controls]
    values = [value for _, value in controls]

    disabled = any(value.lower() in _DISABLED for value in values)
    if not controls:
        state = "omitted"
    elif any("budget" in key.lower() or "budgettokens" in key.lower() for key in keys):
        state = "budget"
    elif disabled and not any(key.endswith("effort") for key in keys):
        state = "omitted"
    else:
        state = "sent"

    return {
        "protocol": protocol,
        "requested": requested,
        "style": style,
        "state": state,
        "wire": [{"key": key, "value": value} for key, value in controls],
        "reserved_tokens": int(reserved or 0),
        "total_limit": total_limit,
        "delivered": bool(controls),
        "reason": _reason(state, requested, style, controls),
    }


def _reason(state: str, requested: str, style: str, controls: list[tuple[str, str]]) -> str:
    if state == "omitted":
        if requested == "auto":
            return "选择「供应商默认」：请求不附带思考参数，由服务端决定"
        if not controls:
            return "该协议/适配器没有发送思考参数"
        return "请求显式关闭思考"
    if state == "budget":
        return "该模型以 token 预算表达强度，不是等级名"
    keys = "、".join(key for key, _ in controls)
    return f"请求已附带 {keys}"


def attach_report(receipt: dict[str, Any], usage: dict[str, Any] | None) -> dict[str, Any]:
    """Attach provider-reported reasoning usage; never infer an unreported value.

    ``reasoning_reported`` is the adapter's explicit "the provider sent this
    number" flag. Without it a zero is indistinguishable from silence, so an
    unreported value is recorded as unknown rather than as ``0``.
    """
    usage = usage or {}
    reported = usage.get("reasoning_reported") is True
    tokens = usage.get("reasoning_tokens")
    known = reported and isinstance(tokens, int) and tokens >= 0
    return {
        **receipt,
        "reported": {
            "usage_reported": usage.get("usage_reported") is True,
            "reasoning_tokens": tokens if known else None,
            "known": known,
        },
        "settled": True,
    }


def mark_unsupported(receipt: dict[str, Any], message: str) -> dict[str, Any]:
    """Record a local adapter refusal without pretending anything was sent."""
    return {**receipt, "state": "unsupported", "delivered": False,
            "wire": [], "reason": message, "settled": True,
            "reported": {"usage_reported": False, "reasoning_tokens": None,
                         "known": False}}


_LABELS = {
    "auto": "供应商默认", "off": "关闭", "minimal": "极低", "low": "轻量",
    "medium": "均衡", "high": "深入", "xhigh": "超高", "max": "最高",
}


def label(effort: str) -> str:
    return _LABELS.get(effort, effort or "未知")


def summary(receipt: dict[str, Any] | None) -> str:
    """One line for a status bar: requested, wire truth, provider report."""
    if not isinstance(receipt, dict) or not receipt:
        return "思考强度：尚无记录"
    requested = label(str(receipt.get("requested") or "auto"))
    state = receipt.get("state")
    wire = "、".join(f"{item['key']}={item['value']}"
                     for item in receipt.get("wire") or []) or "未附带参数"
    parts = [f"选择 {requested}"]
    if state == "sent":
        parts.append(f"已发送 {wire}")
    elif state == "budget":
        reserved = receipt.get("reserved_tokens") or 0
        parts.append(f"已发送预算 {reserved} tokens")
    elif state == "omitted":
        parts.append("请求未附带思考参数")
    elif state == "unsupported":
        parts.append("本地适配器未发送")
    reported = receipt.get("reported") or {}
    if reported.get("known"):
        parts.append(f"供应商回报推理 {reported['reasoning_tokens']:,} tokens")
    elif receipt.get("settled"):
        parts.append("供应商未回报推理用量")
    return " · ".join(parts)


def rows(summary_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Group recorded receipts by requested effort for the usage panel."""
    groups: dict[str, dict[str, Any]] = {}
    for record in (summary_data or {}).get("receipts") or []:
        if not isinstance(record, dict):
            continue
        key = str(record.get("requested") or "unknown")
        slot = groups.setdefault(key, {"requested": key, "label": label(key),
                                       "calls": 0, "sent": 0, "omitted": 0,
                                       "reported": 0, "silent": 0,
                                       "reasoning_tokens": 0})
        slot["calls"] += 1
        state = record.get("state")
        if state in {"sent", "budget"}:
            slot["sent"] += 1
        elif state == "omitted":
            slot["omitted"] += 1
        reported = record.get("reported") or {}
        if reported.get("known"):
            slot["reported"] += 1
            slot["reasoning_tokens"] += int(reported.get("reasoning_tokens") or 0)
        else:
            # "The provider stayed silent" is its own fact, not a missing zero.
            slot["silent"] += 1
    order = ["auto", "off", "minimal", "low", "medium", "high", "xhigh", "max"]
    return sorted(groups.values(),
                  key=lambda row: (order.index(row["requested"])
                                   if row["requested"] in order else len(order),
                                   row["requested"]))
