"""LLM transport with explicit protocol adapters and auditable provider usage.

Legacy config and chat/chat_json/chat_stream entry points remain available.
Native streaming is currently implemented for Chat Completions only.
"""
from __future__ import annotations

import atexit
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from . import config, model_protocols, prompts
from .logging_util import log_model


class ModelRequestError(RuntimeError):
    """Sanitized request/transport failure safe to surface in the desktop UI."""


class ModelResponseError(ModelRequestError):
    """The provider returned an incomplete, blocked or malformed response."""


@dataclass(frozen=True)
class ParsedToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    error: str = ""


def extract_tool_calls(message: dict[str, Any]) -> list[ParsedToolCall]:
    """Accept legacy OpenAI messages and the canonical tool_calls result array."""
    out = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") or raw
        name, call_id = str(fn.get("name") or ""), str(raw.get("id") or "")
        try:
            value = fn.get("arguments", {})
            args = json.loads(value) if isinstance(value, str) else value
            if not isinstance(args, dict):
                raise ValueError("arguments must be object")
            out.append(ParsedToolCall(call_id, name, args, str(raw.get("error") or "")))
        except (TypeError, ValueError):
            out.append(ParsedToolCall(call_id, name, {}, "invalid_arguments"))
    return out


def _resolve_config(tier: str | None = None) -> dict[str, Any]:
    try:
        from . import model_registry
    except ImportError:
        model_registry = None
    if model_registry is not None:
        return model_registry.resolve(tier)
    model = (config.LLM_MODEL_FAST if tier == "fast" else config.LLM_MODEL_STRONG if tier == "strong" else "") or config.LLM_MODEL
    return {"connection_id": "legacy", "protocol": getattr(config, "LLM_PROTOCOL", "chat_completions"),
            "base_url": config.LLM_BASE_URL, "api_key": config.LLM_API_KEY, "model": model,
            "thinking_style": getattr(config, "LLM_THINKING_STYLE", "auto"),
            "reasoning_effort": getattr(config, "LLM_REASONING_EFFORT", "auto"),
            "thinking_budget": getattr(config, "LLM_THINKING_BUDGET", 4096),
            "max_output_tokens": getattr(config, "LLM_MAX_OUTPUT_TOKENS", 8192),
            "context_window": getattr(config, "LLM_CONTEXT_WINDOW", 32768),
            "prompt_cache": getattr(config, "LLM_CACHE_ENABLED", True), "auth_style": "auto"}


def resolved_model_config(tier: str | None = None) -> dict[str, Any]:
    """Return public configuration without API keys or credentials in a URL."""
    result = {k: v for k, v in _resolve_config(tier).items() if k != "api_key"}
    try:
        model_protocols.endpoint(result)
    except ValueError:
        result["base_url"] = "[invalid URL]"
    return result


def capabilities() -> dict[str, Any]:
    return model_protocols.capabilities()


def available(tier: str | None = None) -> bool:
    if not getattr(config, "LLM_ENABLED", True):
        return False
    try:
        profile = _resolve_config(tier)
        if not (profile.get("base_url") and profile.get("model")):
            return False
        model_protocols.endpoint(profile)  # local syntax validation, never a connectivity probe
        return True
    except ValueError:
        return False


def model_for(tier: str | None = None) -> str:
    return str(_resolve_config(tier).get("model") or "")


def _headers() -> dict[str, str]:
    return model_protocols.headers(_resolve_config())


@dataclass
class _ClientSlot:
    client: Any
    active: int = 0
    retired: bool = False


_POOL_LIMIT = 8
_CLIENTS: OrderedDict[tuple, _ClientSlot] = OrderedDict()
_CLIENT_LOCK = threading.RLock()
_FINGERPRINT_KEY = secrets.token_bytes(32)


def _close(slot: _ClientSlot) -> None:
    close = getattr(slot.client, "close", None)
    if close:
        close()


def close_clients() -> None:
    """Retire pooled clients; active requests close their client when released."""
    with _CLIENT_LOCK:
        slots = list(_CLIENTS.values())
        _CLIENTS.clear()
        for slot in slots:
            slot.retired = True
            if not slot.active:
                _close(slot)


reset_clients = close_clients
atexit.register(close_clients)


@contextmanager
def _client(profile: dict, timeout: float | None = None):
    timeout = float(timeout if timeout is not None else profile.get("timeout", config.LLM_TIMEOUT))
    auth = json.dumps(model_protocols.headers(profile), sort_keys=True).encode()
    key = (str(profile.get("connection_id") or ""), profile.get("base_url"),
           hashlib.sha256(auth).digest(), timeout, id(httpx.Client))
    with _CLIENT_LOCK:
        slot = _CLIENTS.get(key)
        if slot is None:
            if len(_CLIENTS) >= _POOL_LIMIT:
                idle = next((k for k, s in _CLIENTS.items() if not s.active), None)
                if idle is not None:
                    _close(_CLIENTS.pop(idle))
            slot = _ClientSlot(httpx.Client(timeout=timeout, limits=httpx.Limits(max_connections=12, max_keepalive_connections=8)))
            if len(_CLIENTS) < _POOL_LIMIT:
                _CLIENTS[key] = slot
            else:
                slot.retired = True
        if key in _CLIENTS:
            _CLIENTS.move_to_end(key)
        slot.active += 1
    try:
        yield slot.client
    finally:
        with _CLIENT_LOCK:
            slot.active -= 1
            if slot.retired and not slot.active:
                _close(slot)


def list_models(tier: str | None = None) -> list[str]:
    """Discover model IDs only; discovery does not prove thinking support."""
    try:
        profile = _resolve_config(tier)
        if not profile.get("base_url"):
            return []
        with _client(profile, min(config.LLM_TIMEOUT, 15)) as cli:
            resp = cli.get(model_protocols.endpoint(profile, models=True), headers=model_protocols.headers(profile))
            resp.raise_for_status()
            data = resp.json()
        protocol = model_protocols.protocol_of(profile)
        if protocol == "ollama":
            return [str(m.get("name") or m.get("model")) for m in data.get("models", []) if m.get("name") or m.get("model")]
        if protocol == "gemini":
            return [str(m["name"]).removeprefix("models/") for m in data.get("models", []) if m.get("name")]
        return [str(m["id"]) for m in data.get("data", []) if m.get("id")]
    except Exception:
        return []


def _fingerprint(messages: list, tools: list | None) -> str:
    raw = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_FINGERPRINT_KEY, raw, hashlib.sha256).hexdigest()


def _log(node: str, model: str, usage: dict[str, Any], t0: float,
         prompt_key: str, status: str = "success", *, profile: dict | None = None, fingerprint: str = "") -> str:
    profile = profile or {}
    return log_model(node=node, route=f"remote({model})", model=model,
                     connection_id=str(profile.get("connection_id") or ""),
                     protocol=str(profile.get("protocol") or "chat_completions"),
                     prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
                     cached_prompt_tokens=usage.get("cached_prompt_tokens"), cache_write_tokens=usage.get("cache_write_tokens", 0),
                     reasoning_tokens=usage.get("reasoning_tokens", 0), cache_reported=usage.get("cache_reported", False),
                     usage_reported=usage.get("usage_reported", False), prompt_fingerprint=fingerprint,
                     latency_ms=int((time.perf_counter() - t0) * 1000), status=status,
                     route_reason="llm-endpoint-configured", prompt_key=prompt_key)


def _safe_error(exc: Exception) -> Exception:
    if isinstance(exc, httpx.HTTPStatusError):
        return ModelRequestError(f"模型接口返回 HTTP {exc.response.status_code}；请检查认证、协议、模型与参数配置。")
    if isinstance(exc, httpx.TimeoutException):
        return ModelRequestError("模型请求超时；请检查服务状态或调整超时配置。")
    if isinstance(exc, httpx.RequestError):
        return ModelRequestError("模型连接失败或传输中断；请检查端点和网络。")
    if isinstance(exc, (ModelRequestError, ValueError)) and not isinstance(exc, json.JSONDecodeError):
        return exc
    return ModelResponseError("模型返回无法解析的响应；请检查协议是否匹配。")


def chat(messages: list[dict[str, Any]], node: str = "unknown", temperature: float = 0.2,
         max_tokens: int = 2048, prompt_key: str = "", tier: str | None = None,
         tools: list[dict] | None = None, tool_choice: Any = None) -> dict[str, Any]:
    """Generate text or native tool calls, retaining opaque state for continuation.

    max_tokens is the visible answer budget. Token-budget thinking reserves
    additional tokens, subject to the profile's hard max_output_tokens cap.
    """
    t0, profile, fingerprint, data = time.perf_counter(), {}, "", {}
    try:
        profile = _resolve_config(tier)
        fingerprint = _fingerprint(messages, tools)
        request = model_protocols.build_request(profile, messages, temperature, max_tokens, tools, tool_choice)
        with _client(profile) as cli:
            resp = cli.post(request["url"], headers=request["headers"], json=request["json"])
            resp.raise_for_status()
            data = resp.json()
        result = model_protocols.parse_response(profile, data, request["tool_names"])
    except Exception as exc:
        actual = (data.get("model") or data.get("modelVersion")) if isinstance(data, dict) else None
        usage = model_protocols.normalize_usage(profile.get("protocol", "chat_completions"), data) if isinstance(data, dict) else {}
        _log(node, str(actual or profile.get("model") or ""), usage, t0, prompt_key, "error", profile=profile, fingerprint=fingerprint)
        raise _safe_error(exc) from None
    status = result.pop("status")
    result["trace_id"] = _log(node, result["model"], result["usage"], t0, prompt_key, status,
                              profile=profile, fingerprint=fingerprint)
    if status == "incomplete":
        raise ModelResponseError("模型输出已截断或未完成；请提高总输出上限或降低思考预算。")
    if status != "success":
        raise ModelResponseError("模型未返回可用结果，可能被策略阻止或生成失败。")
    return result


def chat_json(messages: list[dict[str, Any]], node: str = "unknown", prompt_key: str = "",
              temperature: float = 0.2, max_tokens: int = 2048, tier: str | None = None) -> dict[str, Any] | None:
    """Parse JSON; retry once with feedback and provider continuation state."""
    convo = list(messages)
    for _ in range(2):
        result = chat(convo, node=node, temperature=temperature, max_tokens=max_tokens, prompt_key=prompt_key, tier=tier)
        obj = prompts.extract_json(result["content"])
        if obj is not None:
            return obj
        convo = [*messages, result["assistant_message"], {"role": "user", "content":
                 "你上一次的输出不符合 JSON 协议。请严格按要求只输出一个合法 JSON 对象，不要任何解释、围栏或多余文字。"}]
    return None


def _streamed_tool_call(slot: dict) -> dict:
    """把累积的流式 tool_calls 分片整理为 chat() 同构的调用字典。"""
    raw = str(slot.get("arguments") or "")
    error = ""
    try:
        args = json.loads(raw) if raw.strip() else {}
        if not isinstance(args, dict):
            raise ValueError("arguments must be object")
    except (TypeError, ValueError):
        args, error = {}, "invalid_arguments"
    item = {"id": str(slot.get("id") or "call_" + uuid.uuid4().hex[:16]),
            "name": str(slot.get("name") or ""), "arguments": args}
    if error or not item["name"]:
        item["error"] = error or "missing_name"
    return item


def _streamed_result(profile: dict, actual_model: str, content: str,
                     tool_slots: dict[int, dict], usage: dict) -> dict:
    """由流式累积物组装与 chat()/parse_response 同构的最终结果。"""
    calls = [_streamed_tool_call(tool_slots[i]) for i in sorted(tool_slots)]
    canonical = {"role": "assistant", "content": content}
    if calls:
        canonical["tool_calls"] = [{"id": c["id"], "type": "function",
                                    "function": {"name": c["name"],
                                                 "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                                   for c in calls]
    return {"content": content, "model": actual_model, "provider": "chat_completions",
            "protocol": "chat_completions", "usage": usage, "tool_calls": calls,
            "assistant_message": canonical}


def _stream_events(messages: list[dict[str, Any]], node: str, temperature: float,
                   max_tokens: int, prompt_key: str, tier: str | None,
                   tools: list[dict] | None, allow_tools: bool) -> Iterator[dict]:
    """Chat Completions SSE 事件核心：产出 {"delta": str} 与最终 {"result": dict}。

    allow_tools=False 保持历史 chat_stream 语义（遇到 tool_calls 分片即报错）；
    allow_tools=True 累积 tool_calls 分片，最终结果与 chat(tools=...) 同构。
    一旦产出过内容增量，错误与缺失结束标记一律抛出，绝不当作成功。
    """
    profile = _resolve_config(tier)
    if model_protocols.protocol_of(profile) != "chat_completions":
        result = chat(messages, node=node, temperature=temperature, max_tokens=max_tokens,
                      prompt_key=prompt_key, tier=tier, tools=tools)
        result.pop("status", None)
        yield {"result": result}
        return
    t0, fingerprint = time.perf_counter(), _fingerprint(messages, tools)
    usage, yielded, saw_event, complete, finish = {}, False, False, False, ""
    non_sse_lines, full_result = [], None
    actual_model = str(profile.get("model") or "")
    tool_slots: dict[int, dict] = {}
    content_parts: list[str] = []
    try:
        request = model_protocols.build_request(profile, messages, temperature, max_tokens,
                                                tools=tools, stream=True)
        with _client(profile) as cli:
            with cli.stream("POST", request["url"], headers=request["headers"], json=request["json"]) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        non_sse_lines.append(line)
                        continue
                    saw_event = True
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        complete = True
                        break
                    obj = json.loads(raw)
                    if obj.get("error"):
                        raise ModelResponseError("模型流式生成返回错误。")
                    if obj.get("model"):
                        actual_model = str(obj["model"])
                    if isinstance(obj.get("usage"), dict):
                        usage = model_protocols.normalize_usage("chat_completions", obj)
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    finish = str(choices[0].get("finish_reason") or finish)
                    delta = choices[0].get("delta") or {}
                    if delta.get("tool_calls"):
                        if not allow_tools:
                            raise ModelResponseError("文本流接口不执行工具调用，请使用 chat(tools=...)。")
                        for chunk in delta["tool_calls"]:
                            slot = tool_slots.setdefault(int(chunk.get("index") or 0),
                                                         {"id": "", "name": "", "arguments": ""})
                            if chunk.get("id"):
                                slot["id"] = str(chunk["id"])
                            function = chunk.get("function") or {}
                            if function.get("name"):
                                slot["name"] += str(function["name"])
                            if function.get("arguments"):
                                slot["arguments"] += str(function["arguments"])
                        continue
                    text = delta.get("content") or ""
                    if text:
                        yielded = True
                        content_parts.append(str(text))
                        yield {"delta": str(text)}
        if saw_event and (not complete or finish in {"length", "content_filter"}):
            raise ModelResponseError("模型流式输出中断、截断或缺少正常结束标记。")
        if saw_event and not yielded and not tool_slots:
            raise ModelResponseError("模型流式响应没有返回正文。")
        if not saw_event:
            # Some compatible endpoints return ordinary JSON despite stream=true.
            # Consume that response rather than pay for another generation.
            body = json.loads("\n".join(non_sse_lines))
            usage = model_protocols.normalize_usage("chat_completions", body)
            full_result = model_protocols.parse_response(profile, body, request["tool_names"])
            actual_model = full_result["model"]
            if full_result["status"] != "success" or full_result["tool_calls"] and not allow_tools:
                finish = full_result["finish_reason"]
                raise ModelResponseError("模型输出截断或没有可显示正文；工具调用请使用 chat(tools=...)。")
    except GeneratorExit:
        _log(node, actual_model, usage, t0, prompt_key, "cancelled", profile=profile, fingerprint=fingerprint)
        raise
    except Exception as exc:
        _log(node, actual_model, usage, t0, prompt_key,
             "incomplete" if yielded or finish == "length" else "error",
             profile=profile, fingerprint=fingerprint)
        raise _safe_error(exc) from None
    if not saw_event:
        _log(node, actual_model, usage, t0, prompt_key, profile=profile, fingerprint=fingerprint)
        result = full_result
        result.pop("status", None)
        yield {"result": result}
        return
    if finish and finish not in {"stop", "tool_calls"}:
        _log(node, actual_model, usage, t0, prompt_key, "error", profile=profile, fingerprint=fingerprint)
        raise ModelResponseError("模型未返回可用结果，可能被策略阻止或生成失败。")
    result = _streamed_result(profile, actual_model, "".join(content_parts), tool_slots, usage)
    if not result["content"] and not result["tool_calls"]:
        _log(node, actual_model, usage, t0, prompt_key, "error", profile=profile, fingerprint=fingerprint)
        raise ModelResponseError("模型未返回可用结果，可能被策略阻止或生成失败。")
    _log(node, actual_model, usage, t0, prompt_key, profile=profile, fingerprint=fingerprint)
    yield {"result": result}


def chat_stream(messages: list[dict[str, Any]], node: str = "unknown", temperature: float = 0.2,
                max_tokens: int = 2048, prompt_key: str = "", tier: str | None = None) -> Iterator[str]:
    """Text-only SSE for Chat Completions; other adapters yield one full response.

    Once content has been yielded, errors and missing completion markers are
    raised, never treated as success or retried with duplicate displayed text.
    """
    fallback: str | None = None
    seen_delta = False
    for event in _stream_events(messages, node, temperature, max_tokens, prompt_key, tier,
                                tools=None, allow_tools=False):
        if "delta" in event:
            seen_delta = True
            yield event["delta"]
        else:
            fallback = event["result"]["content"]
    if fallback is not None and not seen_delta:
        yield fallback


def chat_stream_agent(messages: list[dict[str, Any]], node: str = "unknown", temperature: float = 0.2,
                      max_tokens: int = 2048, prompt_key: str = "", tier: str | None = None,
                      tools: list[dict] | None = None) -> Iterator[dict]:
    """智能体流式接口：先产出 {"delta": str} 内容增量，最后产出 {"result": dict}。

    result 与 chat(tools=...) 同构（content/tool_calls/assistant_message/usage），
    供 driver 记账与崩溃对账使用；非 Chat Completions 协议直接回退完整 chat()。
    """
    yield from _stream_events(messages, node, temperature, max_tokens, prompt_key, tier,
                              tools=tools, allow_tools=True)
