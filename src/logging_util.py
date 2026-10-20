"""JSONL 调用日志与真实供应商用量统计；未知缓存不等于零命中。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import LOG_DIR

_MODEL_LOG = LOG_DIR / "model_calls.jsonl"
_TOOL_LOG = LOG_DIR / "tool_calls.jsonl"
_LOG_LOCK = threading.RLock()


def _append(path: Path, record: dict[str, Any]) -> None:
    # Windows append handles can race at EOF: protect open/write/close together.
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        with _LOG_LOCK:
            content = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return []
    out: list[dict[str, Any]] = []
    for line in content.splitlines():
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _optional_count(value: Any) -> int | None:
    """Accept nonnegative integer counts, including legacy integer strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            number = int(value)
        except ValueError:
            return None
        return number if number >= 0 else None
    return None


def _count(value: Any) -> int:
    return _optional_count(value) or 0


def _label(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) and value else default


def _cache_read(record: dict[str, Any]) -> int | None:
    """Read tokens are a subset of normalized FULL input, never cache writes."""
    if record.get("cache_reported") is not True:
        return None
    # Missing output usage does not invalidate a fully reported input/cache pair.
    prompt = _optional_count(record.get("prompt_tokens"))
    cached = _optional_count(record.get("cached_prompt_tokens"))
    if prompt is None or cached is None or cached > prompt:
        return None
    return cached


def _empty_model_stats() -> dict[str, Any]:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "cached_prompt_tokens": 0, "cache_write_tokens": 0,
            "reasoning_tokens": 0, "cache_reported_calls": 0, "cache_unknown_calls": 0,
            "cache_eligible_prompt_tokens": 0, "cache_hit_rate": None,
            "cache_reported_ratio": None, "usage_reported_calls": 0,
            "usage_unknown_calls": 0, "failures": 0, "by_status": {},
            "avg_latency_ms": 0, "_latency_sum": 0}


def _add_model(stats: dict[str, Any], record: dict[str, Any]) -> None:
    prompt = _count(record.get("prompt_tokens"))
    output = _count(record.get("completion_tokens"))
    cached = _cache_read(record)
    status = _label(record.get("status"), "unknown")
    stats["calls"] += 1
    stats["prompt_tokens"] += prompt
    stats["completion_tokens"] += output
    stats["total_tokens"] += prompt + output
    stats["cache_write_tokens"] += _count(record.get("cache_write_tokens"))
    # Reasoning is part of normalized output; do not add it to total twice.
    stats["reasoning_tokens"] += _count(record.get("reasoning_tokens"))
    stats["_latency_sum"] += _count(record.get("latency_ms"))
    stats["failures"] += int(status != "success")
    stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
    if cached is None:
        stats["cache_unknown_calls"] += 1
    else:
        stats["cached_prompt_tokens"] += cached
        stats["cache_eligible_prompt_tokens"] += prompt
        stats["cache_reported_calls"] += 1
    reported = (record.get("usage_reported") is True
                and _optional_count(record.get("prompt_tokens")) is not None
                and _optional_count(record.get("completion_tokens")) is not None)
    stats["usage_reported_calls" if reported else "usage_unknown_calls"] += 1


def _finish_model(stats: dict[str, Any]) -> None:
    calls = stats["calls"]
    latency = stats.pop("_latency_sum")
    stats["avg_latency_ms"] = latency // calls if calls else 0
    denominator = stats["cache_eligible_prompt_tokens"]
    stats["cache_hit_rate"] = (stats["cached_prompt_tokens"] / denominator
                               if denominator else None)
    stats["cache_reported_ratio"] = stats["cache_reported_calls"] / calls if calls else None


def usage_summary() -> dict[str, Any]:
    """保留 model/tool/recent 旧字段，新增逐连接、协议、实际模型缓存统计。

    by_model 的 key 是 [connection_id, protocol, model或route回退] 的 JSON 编码，
    避免名称内分隔符碰撞；调用方应使用 value 内的独立身份字段显示。
    cache_hit_rate = 有效已报告缓存读取总量 / 同批调用完整输入总量，范围 0..1。
    未报告或无有效分母时为 None；cache_write_tokens 不进入命中分子。
    """
    models = _read_jsonl(_MODEL_LOG)
    tools = _read_jsonl(_TOOL_LOG)
    model = _empty_model_stats()
    by_model: dict[str, dict[str, Any]] = {}
    by_node: dict[str, dict[str, int]] = {}
    for record in models:
        _add_model(model, record)
        connection = _label(record.get("connection_id"))
        protocol = _label(record.get("protocol"))
        actual_model = _label(record.get("model"))
        route = _label(record.get("model_route"), "unknown")
        key = json.dumps([connection, protocol, actual_model or f"route:{route}"],
                         ensure_ascii=False, separators=(",", ":"))
        if key not in by_model:
            by_model[key] = {**_empty_model_stats(), "connection_id": connection,
                             "protocol": protocol, "model": actual_model, "route": route}
        _add_model(by_model[key], record)
        node = _label(record.get("node"), "unknown")
        slot = by_node.setdefault(node, {"calls": 0, "tokens": 0})
        slot["calls"] += 1
        slot["tokens"] += _count(record.get("prompt_tokens")) + _count(record.get("completion_tokens"))
    _finish_model(model)
    for group in by_model.values():
        _finish_model(group)
    model.update(by_model=by_model, by_node=by_node)

    by_tool: dict[str, dict[str, int]] = {}
    tool_latency = tool_failures = 0
    for record in tools:
        name = _label(record.get("tool"), "unknown")
        slot = by_tool.setdefault(name, {"calls": 0, "failures": 0})
        slot["calls"] += 1
        if record.get("status") != "success":
            slot["failures"] += 1
            tool_failures += 1
        tool_latency += _count(record.get("latency_ms"))

    recent = [{"ts": _label(r.get("ts")), "node": _label(r.get("node")),
               "route": _label(r.get("model_route")), "model": _label(r.get("model")),
               "protocol": _label(r.get("protocol")), "connection_id": _label(r.get("connection_id")),
               "tokens": _count(r.get("prompt_tokens")) + _count(r.get("completion_tokens")),
               "latency_ms": _count(r.get("latency_ms")), "status": _label(r.get("status")),
               "cached_prompt_tokens": _cache_read(r), "cache_reported": _cache_read(r) is not None}
              for r in reversed(models[-8:])]
    receipts = [r["reasoning"] for r in models
                if isinstance(r.get("reasoning"), dict)]
    return {"model": model,
            "tool": {"calls": len(tools), "avg_latency_ms": tool_latency // len(tools) if tools else 0,
                     "failures": tool_failures, "by_tool": by_tool},
            "recent": recent,
            "reasoning": {"receipts": receipts, "recorded": len(receipts),
                          "calls": len(models)}}


def _cache_text(stats: dict[str, Any]) -> str:
    rate = stats["cache_hit_rate"]
    rate_text = (f"{rate:.1%}" if rate is not None else
                 "无有效输入" if stats["cache_reported_calls"] else "未报告")
    read_text = f"{stats['cached_prompt_tokens']:,}" if stats["cache_reported_calls"] else "未报告"
    return (f"缓存读取 {read_text}｜写入 {stats['cache_write_tokens']:,}"
            f"｜命中率 {rate_text}｜已报告 {stats['cache_reported_calls']}/{stats['calls']} 次"
            f"｜未知 {stats['cache_unknown_calls']} 次")


def format_reasoning_text() -> str:
    """思考强度三阶段回执的纯文本视图（CLI/TUI 与桌面共用同一份事实）。

    只陈述日志里真实记录的内容：请求了什么、请求是否真的带上了思考参数、
    供应商有没有回报推理用量。没有记录时就明说没有记录，不按档位猜。
    """
    from . import reasoning_receipt as rr
    summary = usage_summary().get("reasoning") or {}
    recorded, calls = summary.get("recorded", 0), summary.get("calls", 0)
    if not recorded:
        return (f"思考强度：尚无回执记录（模型调用 {calls} 次）。"
                if calls else "思考强度：尚无模型调用。")
    lines = [f"思考强度回执：{recorded}/{calls} 次调用留下记录"]
    for row in rr.rows(summary):
        parts = [f"{row['calls']} 次", f"实际附带参数 {row['sent']} 次",
                 f"未附带 {row['omitted']} 次"]
        if row["reported"]:
            parts.append(f"回报 {row['reasoning_tokens']:,} tokens（{row['reported']} 次）")
        if row["silent"]:
            parts.append(f"供应商未回报 {row['silent']} 次")
        lines.append(f"  {row['label']}（{row['requested']}）：" + " · ".join(parts))
    return "\n".join(lines)


def format_usage_text() -> str:
    """渲染 CLI / TUI 用量面板，命中率不替代成本估算。"""
    usage = usage_summary()
    model, tool = usage["model"], usage["tool"]
    lines = [f"模型调用：{model['calls']} 次｜tokens {model['total_tokens']:,}"
             f"（输入 {model['prompt_tokens']:,} / 输出 {model['completion_tokens']:,}）"
             f"｜均延迟 {model['avg_latency_ms']}ms｜失败 {model['failures']}",
             _cache_text(model) + f"｜推理 {model['reasoning_tokens']:,}",
             f"工具调用：{tool['calls']} 次｜均延迟 {tool['avg_latency_ms']}ms｜失败 {tool['failures']}"]
    if model["by_model"]:
        lines.append("按模型（连接 / 协议 / 实际模型）：")
        for group in sorted(model["by_model"].values(), key=lambda value: -value["total_tokens"]):
            identity = " / ".join((group["connection_id"] or "旧连接", group["protocol"] or "未记录协议",
                                    group["model"] or group["route"]))
            lines.append(f"  {identity}：{group['calls']}次｜输入 {group['prompt_tokens']:,}"
                         f" / 输出 {group['completion_tokens']:,} / 推理 {group['reasoning_tokens']:,}"
                         f"｜均延迟 {group['avg_latency_ms']}ms｜失败 {group['failures']}")
            lines.append(f"    {_cache_text(group)}")
    reasoning_text = format_reasoning_text()
    if reasoning_text:
        lines.append(reasoning_text)
    if model["by_node"]:
        detail = "  ".join(f"{name}:{value['calls']}次/{value['tokens']:,}tok"
                           for name, value in sorted(model["by_node"].items(),
                                                     key=lambda item: -item[1]["tokens"])[:6])
        lines.append(f"按节点：{detail}")
    if usage["recent"]:
        lines.append("最近调用：")
        for record in usage["recent"][:5]:
            mark = "✔" if record["status"] == "success" else "✘"
            lines.append(f"  {mark} [{record['ts']}] {record['node']} {record['route']}"
                         f" {record['tokens']:,}tok {record['latency_ms']}ms")
    return "\n".join(lines)


def log_model(node: str, route: str, prompt_tokens: int, completion_tokens: int,
              latency_ms: int, status: str, route_reason: str = "", prompt_key: str = "", *,
              connection_id: str = "", model: str = "", protocol: str = "",
              cached_prompt_tokens: int | None = None, cache_write_tokens: int = 0,
              reasoning_tokens: int = 0, cache_reported: bool = False,
              prompt_fingerprint: str = "", usage_reported: bool | None = None,
              reasoning: dict[str, Any] | None = None) -> str:
    """记录归一化完整输入、缓存读写与实际模型；不保存原始提示词或密钥。

    供应商字段解释由 llm 适配器负责；usage_reported=None 为旧调用方未知。
    prompt_key 是模板版本，prompt_fingerprint 只接受调用方已生成的摘要。
    reasoning 是思考强度的三阶段回执（请求/实际发送/供应商回报），只保留
    档位、是否真的发送、发送的键名与回报的 token 数，不含提示词内容。
    """
    tid = f"gz-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:12]}"
    record = {"prompt_tokens": prompt_tokens, "cached_prompt_tokens": cached_prompt_tokens,
              "cache_reported": cache_reported, "usage_reported": usage_reported}
    cached = _cache_read(record)
    valid_usage = (usage_reported is True and _optional_count(prompt_tokens) is not None
                   and _optional_count(completion_tokens) is not None)
    _append(_MODEL_LOG, {
        "trace_id": tid, "node": _label(node, "unknown"), "model_route": _label(route),
        "connection_id": _label(connection_id), "model": _label(model), "protocol": _label(protocol),
        "prompt_tokens": _count(prompt_tokens), "completion_tokens": _count(completion_tokens),
        "latency_ms": _count(latency_ms), "cost_cny": None,
        "route_reason": _label(route_reason), "status": _label(status, "unknown"),
        "prompt_key": _label(prompt_key), "prompt_fingerprint": _label(prompt_fingerprint),
        "cached_prompt_tokens": cached, "cache_reported": cached is not None,
        "cache_write_tokens": _count(cache_write_tokens), "reasoning_tokens": _count(reasoning_tokens),
        "usage_reported": None if usage_reported is None else valid_usage,
        "reasoning": reasoning,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    return tid


def log_tool(node: str, tool: str, args_digest: str, result_digest: str,
             latency_ms: int, status: str) -> None:
    """记录一次工具调用（外部 API / 统计计算等）。"""
    _append(_TOOL_LOG, {
        "trace_id": f"gz-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:12]}",
        "node": node, "tool": tool, "args": args_digest, "result": result_digest,
        "latency_ms": _count(latency_ms), "status": status,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
