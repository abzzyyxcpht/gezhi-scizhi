"""Pure wire adapters. Provider state is replayed unchanged, never rendered as text.

Protocol references verified 2026-09-17:
https://developers.openai.com/api/docs/guides/function-calling
https://platform.claude.com/docs/en/build-with-claude/extended-thinking
https://ai.google.dev/gemini-api/docs/generate-content/thinking
https://docs.ollama.com/api/chat
https://api-docs.deepseek.com/guides/thinking_mode/
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


PROTOCOLS = {
    "chat_completions": ("OpenAI Chat Completions / 兼容接口", ["auto", "none", "openai_effort", "deepseek_thinking"]),
    "responses": ("OpenAI Responses", ["auto", "none", "openai_effort", "deepseek_thinking"]),
    "anthropic_messages": ("Anthropic Messages", ["auto", "none", "anthropic_budget", "anthropic_adaptive"]),
    "gemini": ("Gemini generateContent", ["auto", "none", "gemini_budget", "gemini_level"]),
    "ollama": ("Ollama native", ["auto", "none", "ollama_think"]),
}
STYLES = {"auto": "按已知模型识别", "none": "无思考参数支持", "openai_effort": "OpenAI effort",
          "anthropic_budget": "Claude 手动预算", "anthropic_adaptive": "Claude 自适应强度",
          "gemini_budget": "Gemini token 预算", "gemini_level": "Gemini thinking level",
          "deepseek_thinking": "DeepSeek thinking", "ollama_think": "Ollama think"}
EFFORTS = {"auto": "供应商默认", "off": "关闭思考", "minimal": "最低", "low": "低",
           "medium": "中", "high": "高", "xhigh": "更高", "max": "最高"}


def capabilities() -> dict[str, Any]:
    return {"protocols": [{"id": k, "label": v[0], "native_streaming": k == "chat_completions",
                            "tool_calling": True, "thinking_styles": v[1]} for k, v in PROTOCOLS.items()],
            "thinking_styles": [{"id": k, "label": v} for k, v in STYLES.items()],
            "efforts": [{"id": k, "label": v} for k, v in EFFORTS.items()],
            "notes": ["原生流式目前仅支持 Chat Completions；其余接口完整生成后返回。",
                      "auto 保留供应商默认；off 只有模型支持真正关闭时才可用。",
                      "预算模式：low 使用预算的 1/4，medium 使用 1/2，high 使用完整预算；受模型最小预算约束。",
                      "缓存开关控制显式缓存提示，不能关闭供应商自动缓存；缺少报送时命中率未知。"]}


def protocol_of(profile: dict) -> str:
    protocol = str(profile.get("protocol") or "chat_completions")
    if protocol not in PROTOCOLS:
        raise ValueError(f"不支持的模型协议：{protocol}")
    return protocol


def endpoint(profile: dict, models: bool = False) -> str:
    protocol = protocol_of(profile)
    base = urlsplit(str(profile.get("base_url") or "").rstrip("/"))
    if base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password or base.query or base.fragment:
        raise ValueError("模型地址须为 http(s) URL，不能包含账号密码、查询参数或片段；请将密钥填入独立字段。")
    try:
        port = base.port  # urllib defers numeric/range validation until this property is read.
        if port == 0:
            raise ValueError('zero port')
    except ValueError as exc:
        raise ValueError('模型地址端口无效，请使用数字端口 1–65535。') from exc
    path = base.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages", "/api/chat"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if protocol == "ollama":
        if path.endswith("/v1"):
            path = path[:-3]
        if not path.endswith("/api"):
            path += "/api"
        path += "/tags" if models else "/chat"
    elif protocol == "gemini":
        path = path or "/v1beta"
        path += "/models"
        if not models:
            model = str(profile.get("model") or "").removeprefix("models/")
            path += "/" + quote(model, safe="-._") + ":generateContent"
    else:
        path = path or "/v1"
        path += "/models" if models else {"chat_completions": "/chat/completions", "responses": "/responses",
                                             "anthropic_messages": "/messages"}[protocol]
    return urlunsplit((base.scheme, base.netloc, path, "", ""))


def headers(profile: dict) -> dict[str, str]:
    protocol = protocol_of(profile)
    auth = str(profile.get("auth_style") or "auto").lower().replace("_", "-")
    if auth == "auto":
        auth = {"anthropic_messages": "anthropic", "gemini": "gemini"}.get(protocol, "bearer")
    auth = {"x-api-key": "anthropic", "api-key": "anthropic", "x-goog-api-key": "gemini"}.get(auth, auth)
    if auth not in {"none", "bearer", "anthropic", "gemini"}:
        raise ValueError("未知认证方式，请选择 auto、bearer、anthropic、gemini 或 none。")
    result = {"Content-Type": "application/json"}
    if protocol == "anthropic_messages":
        result["anthropic-version"] = "2023-06-01"
    key = str(profile.get("api_key") or "")
    if key and auth != "none":
        name = {"bearer": "Authorization", "anthropic": "x-api-key", "gemini": "x-goog-api-key"}[auth]
        result[name] = "Bearer " + key if auth == "bearer" else key
    return result


def _openai_reasoner(model: str) -> bool:
    return bool(re.match(r"(?:o[134](?:-|$)|gpt-[5-9](?:[.-]|$))", model))


def _auto_style(protocol: str, model: str) -> str:
    if protocol in {"chat_completions", "responses"}:
        if model.startswith("deepseek"):
            return "deepseek_thinking"
        if _openai_reasoner(model):
            return "openai_effort"
    if protocol == "anthropic_messages":
        if re.search(r"(?:3[-.]7|4[-.]5|(?:sonnet|opus)-4(?:-|$))", model) and not re.search(r"4[-.][6-9]", model):
            return "anthropic_budget"
        if re.search(r"4[-.][6-9]|(?:opus|sonnet|haiku)-[5-9]", model):
            return "anthropic_adaptive"
    if protocol == "gemini":
        if "gemini-2.5" in model:
            return "gemini_budget"
        if re.search(r"gemini-[3-9]", model):
            return "gemini_level"
    if protocol == "ollama":
        return "ollama_think"
    raise ValueError("该模型的思考能力未知；请选供应商默认 auto，或明确选择模型支持的 thinking_style。")


def thinking(profile: dict, visible_tokens: int) -> tuple[dict, int, bool]:
    """Return wire controls, total generation limit, and temperature exclusion."""
    protocol = protocol_of(profile)
    model = str(profile.get("model") or "").lower()
    style = str(profile.get("thinking_style") or "auto")
    effort = str(profile.get("reasoning_effort") or "auto")
    if style not in PROTOCOLS[protocol][1]:
        raise ValueError(f"思考方式 {style} 与协议 {protocol} 不兼容。")
    if effort not in EFFORTS:
        raise ValueError("未知思考强度；请选择支持的 reasoning_effort。")
    cap = int(profile.get("max_output_tokens") or 8192)
    if visible_tokens < 1 or cap < 1:
        raise ValueError("输出 token 上限必须为正整数。")
    if visible_tokens > cap:
        raise ValueError(f"请求输出 {visible_tokens} tokens 超过模型配置上限 {cap}。")
    exclude_temp = protocol in {"chat_completions", "responses"} and _openai_reasoner(model)
    # New Claude generations reject non-default temperature even without explicit thinking.
    exclude_temp |= protocol == "anthropic_messages" and bool(re.search(r"4[-.][7-9]|(?:opus|sonnet)-[5-9]", model))
    if style == "none":
        if effort != "auto":
            raise ValueError("none 表示不支持思考控制；请用 auto，或选择能落实关闭/强度的思考方式。")
        return {}, visible_tokens, exclude_temp
    if effort == "auto":
        return {}, visible_tokens, exclude_temp
    if style == "auto":
        style = _auto_style(protocol, model)
    off = effort == "off"
    controls: dict[str, Any] = {}
    reserve = 0
    if style == "openai_effort":
        if re.match(r"gpt-(?:3|4)(?:[.o-]|$)", model):
            raise ValueError("此 OpenAI 模型不支持 reasoning 思考强度参数。")
        if effort == "max":
            raise ValueError("OpenAI effort 不接受 max；请按该模型能力选择 high/xhigh。")
        if off and (re.match(r"o[134](?:-|$)", model) or model in {"gpt-5", "gpt-5-mini", "gpt-5-nano"}):
            raise ValueError("此模型不支持关闭思考 off。")
        if model.startswith(("o1", "o3", "o4")) and effort in {"minimal", "xhigh"}:
            raise ValueError("此模型仅支持 low、medium、high 思考强度。")
        value = "none" if off else effort
        controls = {"reasoning": {"effort": value}} if protocol == "responses" else {"reasoning_effort": value}
        exclude_temp = True
    elif style == "deepseek_thinking":
        if effort not in {"off", "low", "high", "max"}:
            raise ValueError("DeepSeek 原生强度为 low/high/max；medium 或 xhigh 会映射为 high，请直接选择真实等级。")
        if protocol == "responses":
            controls = {"reasoning": {"effort": "none" if off else effort}}
        else:
            controls = {"thinking": {"type": "disabled" if off else "enabled"}}
            if not off:
                controls["reasoning_effort"] = effort
        exclude_temp = not off
    elif style in {"anthropic_budget", "gemini_budget"}:
        if effort not in {"off", "low", "medium", "high"}:
            raise ValueError("预算思考支持 off/low/medium/high；请通过 thinking_budget 设置具体预算。")
        if style == "anthropic_budget" and re.search(r"4[-.][7-9]|(?:opus|sonnet)-[5-9]", model):
            raise ValueError("此 Claude 版本不支持手动预算，请选择 anthropic_adaptive。")
        if style == "gemini_budget" and "gemini-2.5-pro" in model and off:
            raise ValueError("Gemini 2.5 Pro 不支持关闭思考 off。")
        if style == "gemini_budget" and re.search(r"gemini-[3-9]", model):
            raise ValueError("Gemini 3 及以后请用 gemini_level，不能以预算替代思考等级。")
        budget = int(profile.get("thinking_budget") or 0)
        minimum = 1024 if style == "anthropic_budget" else (512 if "flash-lite" in model else 128)
        if not off and budget < minimum:
            raise ValueError(f"思考预算至少为 {minimum} tokens。")
        amount = 0 if off else max(minimum, budget // {"low": 4, "medium": 2, "high": 1}[effort])
        reserve = amount
        if style == "anthropic_budget":
            controls = {"thinking": {"type": "disabled"} if off else {"type": "enabled", "budget_tokens": amount}}
            exclude_temp |= not off
        else:
            maximum = 32768 if "2.5-pro" in model else 24576
            if amount > maximum:
                raise ValueError(f"此 Gemini 预算上限为 {maximum} tokens。")
            controls = {"generationConfig": {"thinkingConfig": {"thinkingBudget": amount}}}
    elif style == "anthropic_adaptive":
        if re.search(r"4[-.]5|3[-.]7", model):
            raise ValueError("此 Claude 版本不支持 adaptive；请选择 anthropic_budget。")
        if effort not in {"off", "low", "medium", "high", "max"}:
            raise ValueError("Claude adaptive 不支持此强度，请使用模型支持的 low/medium/high/max。")
        if off and any(x in model for x in ("mythos", "fable")):
            raise ValueError("此模型不支持关闭思考 off。")
        controls = {"thinking": {"type": "disabled" if off else "adaptive"}}
        if not off:
            controls["output_config"] = {"effort": effort}
        exclude_temp = True
    elif style == "gemini_level":
        if "gemini-2.5" in model:
            raise ValueError("Gemini 2.5 不支持 thinkingLevel，请选 gemini_budget。")
        if off:
            raise ValueError("此 Gemini level 适配不提供真正关闭思考；low/minimal 不等于 off。")
        if effort not in {"minimal", "low", "medium", "high"}:
            raise ValueError("Gemini level 不支持此思考强度。")
        if "pro" in model and effort == "minimal":
            raise ValueError("此 Gemini Pro 模型不支持 minimal。")
        controls = {"generationConfig": {"thinkingConfig": {"thinkingLevel": effort.upper()}}}
    elif style == "ollama_think":
        if "gpt-oss" in model:
            if effort not in {"low", "medium", "high"}:
                raise ValueError("Ollama GPT-OSS 仅支持 low/medium/high，不能关闭思考 off。")
        elif effort not in {"off", "low", "medium", "high", "max"}:
            raise ValueError("Ollama think 不支持此强度。")
        controls = {"think": False if off else effort}
    total = visible_tokens + reserve
    if total > cap:
        raise ValueError(f"思考预算 {reserve} + 正文请求 {visible_tokens} 超过总输出上限 {cap}；请提高上限或降低预算。")
    return controls, total, exclude_temp


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    raise ValueError("当前消息适配器仅接收文本 content；多模态内容需要专门适配。")


def _state(message: dict, profile: dict) -> dict:
    state = message.get("_provider_state") or {}
    if state and (state.get("protocol") != protocol_of(profile) or
                  state.get("connection_id", "") != str(profile.get("connection_id") or "")):
        raise ValueError("工具续接消息来自不同协议或连接；不能丢弃原始状态后继续，请重新开始该轮对话。")
    if state and state.get("model") not in (None, profile.get("model")):
        raise ValueError("工具续接消息属于不同模型，不能复用其签名或推理状态；请重新开始该轮对话。")
    return state


def _function(call: dict) -> tuple[str, Any]:
    fn = call.get("function") or call
    return str(fn.get("name") or ""), fn.get("arguments", {})


def _arguments(raw: Any) -> tuple[dict, str]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value, ""
    except (ValueError, TypeError):
        return {}, "invalid_arguments"


def _tool_definition(tool: dict) -> dict:
    fn = tool.get("function") if tool.get("type") == "function" else tool
    if not isinstance(fn, dict) or not fn.get("name"):
        raise ValueError("工具定义必须包含 function.name 与 parameters。")
    return {"name": fn["name"], "description": str(fn.get("description") or ""),
            "parameters": copy.deepcopy(fn.get("parameters") or {"type": "object", "properties": {}})}


def _tool_aliases(protocol: str, definitions: list[dict]) -> dict[str, str]:
    """Translate runtime names without collisions; opaque responses keep wire names."""
    pattern = r"[A-Za-z0-9_.:-]{1,128}" if protocol in {"gemini", "ollama"} else r"[A-Za-z0-9_-]{1,64}"
    originals = [str(f["name"]) for f in definitions]
    if len(set(originals)) != len(originals):
        raise ValueError("工具名称重复。")
    aliases = {name: name for name in originals if re.fullmatch(pattern, name)}
    used = set(aliases.values())
    for name in originals:
        if name in aliases:
            continue
        prefix = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:40] or "tool"
        salt = 0
        while True:
            digest = hashlib.sha256((name + ":" + str(salt)).encode()).hexdigest()[:16]
            candidate = prefix + "_" + digest
            if candidate not in used:
                break
            salt += 1
        aliases[name] = candidate
        used.add(candidate)
    return aliases


def _choice(choice: Any) -> tuple[str, str]:
    if choice is None:
        return "auto", ""
    if isinstance(choice, str) and choice in {"auto", "none", "required"}:
        return choice, ""
    if isinstance(choice, dict) and choice.get("type") == "function":
        name = str((choice.get("function") or {}).get("name") or choice.get("name") or "")
        if name:
            return "function", name
    raise ValueError("不支持的 tool_choice，请使用 auto/none/required 或指定 function。")


def _append_role(items: list, role: str, key: str, blocks: list) -> None:
    if items and items[-1].get("role") == role:
        items[-1][key].extend(blocks)
    else:
        items.append({"role": role, key: blocks})


def build_request(profile: dict, messages: list[dict], temperature: float, max_tokens: int,
                  tools: list[dict] | None = None, tool_choice: Any = None, stream: bool = False) -> dict:
    protocol = protocol_of(profile)
    if not profile.get("model"):
        raise ValueError("尚未配置模型名称。")
    controls, total, exclude_temp = thinking(profile, max_tokens)
    definitions = [_tool_definition(t) for t in tools or []]
    aliases = _tool_aliases(protocol, definitions)
    # Include earlier aliases when a continuation omits tool definitions.
    for message in messages:
        for wire, original in (message.get("_provider_state") or {}).get("tool_names", {}).items():
            if original in aliases and aliases[original] != wire:
                raise ValueError("工具定义变化导致名称映射冲突，请重新开始该轮对话。")
            aliases[original] = wire
    wire_names = {wire: original for original, wire in aliases.items()}
    for definition in definitions:
        definition["name"] = aliases[definition["name"]]
    system, native, names = [], [], {}
    for msg in messages:
        if not isinstance(msg, dict):
            raise ValueError("消息必须为对象。")
        role = str(msg.get("role") or "")
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            raise ValueError("不支持的消息角色。")
        state = _state(msg, profile)
        text = _text(msg.get("content"))
        for call in msg.get("tool_calls") or []:
            name = _function(call)[0]
            names[str(call.get("id") or "")] = aliases.get(name, name)
        if protocol in {"anthropic_messages", "gemini"} and role in {"system", "developer"}:
            system.append(text)
            continue
        if protocol == "chat_completions":
            item = copy.deepcopy(state.get("message")) if state else {"role": role, "content": text}
            if not state:
                for key in ("tool_calls", "tool_call_id", "name"):
                    if key in msg and not (key == "name" and role == "tool"):
                        item[key] = copy.deepcopy(msg[key])
                for call in item.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    fn["name"] = aliases.get(fn.get("name"), fn.get("name"))
            native.append(item)
        elif protocol == "responses":
            if role == "assistant" and state:
                native.extend(copy.deepcopy(state["items"]))
            elif role == "tool":
                native.append({"type": "function_call_output", "call_id": msg.get("tool_call_id"), "output": text})
            else:
                if text:
                    native.append({"role": role, "content": text})
                for call in msg.get("tool_calls") or []:
                    name, args = _function(call)
                    native.append({"type": "function_call", "call_id": call.get("id"), "name": aliases.get(name, name),
                                   "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)})
        elif protocol == "anthropic_messages":
            if role == "assistant":
                blocks = copy.deepcopy(state["blocks"]) if state else ([{"type": "text", "text": text}] if text else [])
                if not state:
                    for call in msg.get("tool_calls") or []:
                        name, raw = _function(call)
                        args, error = _arguments(raw)
                        if error:
                            raise ValueError("无法回放无效工具参数。")
                        blocks.append({"type": "tool_use", "id": call.get("id"), "name": aliases.get(name, name), "input": args})
                _append_role(native, "assistant", "content", blocks)
            elif role == "tool":
                _append_role(native, "user", "content", [{"type": "tool_result", "tool_use_id": msg.get("tool_call_id"), "content": text}])
            else:
                _append_role(native, "user", "content", [{"type": "text", "text": text}])
        elif protocol == "gemini":
            if role == "assistant":
                parts = copy.deepcopy(state["parts"]) if state else ([{"text": text}] if text else [])
                if not state:
                    for call in msg.get("tool_calls") or []:
                        name, raw = _function(call)
                        args, error = _arguments(raw)
                        if error:
                            raise ValueError("无法回放无效工具参数。")
                        parts.append({"functionCall": {"id": call.get("id"), "name": aliases.get(name, name), "args": args}})
                for call, part in zip(msg.get("tool_calls") or [], [p for p in parts if p.get("functionCall")]):
                    names["id:" + str(call.get("id"))] = part["functionCall"].get("id")
                _append_role(native, "model", "parts", parts)
            elif role == "tool":
                call_id = str(msg.get("tool_call_id") or "")
                name = msg.get("name") or names.get(call_id)
                if not name:
                    raise ValueError("Gemini 工具结果缺少工具名称。")
                try:
                    result = json.loads(text)
                except (TypeError, ValueError):
                    result = {"result": text}
                if not isinstance(result, dict):
                    result = {"result": result}
                fn = {"name": aliases.get(name, name), "response": result}
                if names.get("id:" + call_id):
                    fn["id"] = names["id:" + call_id]
                _append_role(native, "user", "parts", [{"functionResponse": fn}])
            else:
                _append_role(native, "user", "parts", [{"text": text}])
        else:
            item = copy.deepcopy(state["message"]) if state else {"role": role, "content": text}
            if role == "developer":
                item["role"] = "system"
            if role == "tool":
                name = msg.get("name") or names.get(str(msg.get("tool_call_id") or ""))
                if not name:
                    raise ValueError("Ollama 工具结果缺少工具名称。")
                item["tool_name"] = aliases.get(name, name)
            elif not state and msg.get("tool_calls"):
                item["tool_calls"] = []
                for call in msg["tool_calls"]:
                    name, raw = _function(call)
                    args, error = _arguments(raw)
                    if error:
                        raise ValueError("无法回放无效工具参数。")
                    item["tool_calls"].append({"function": {"name": aliases.get(name, name), "arguments": args}})
            native.append(item)
    model = str(profile["model"])
    if protocol == "chat_completions":
        limit_key = "max_completion_tokens" if _openai_reasoner(model.lower()) or "reasoning_effort" in controls and "thinking" not in controls else "max_tokens"
        payload = {"model": model, "messages": native, limit_key: total, "stream": stream}
        if stream:
            payload["stream_options"] = {"include_usage": True}
    elif protocol == "responses":
        payload = {"model": model, "input": native, "max_output_tokens": total, "store": False}
        if not model.lower().startswith("deepseek"):
            payload["include"] = ["reasoning.encrypted_content"]
    elif protocol == "anthropic_messages":
        payload = {"model": model, "messages": native, "max_tokens": total}
        if system:
            payload["system"] = [{"type": "text", "text": "\n\n".join(system)}]
            if profile.get("prompt_cache", True):
                payload["system"][-1]["cache_control"] = {"type": "ephemeral"}
    elif protocol == "gemini":
        payload = {"contents": native, "generationConfig": {"maxOutputTokens": total}}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
    else:
        payload = {"model": model, "messages": native, "stream": False,
                   "options": {"num_predict": total, "num_ctx": int(profile.get("context_window") or 32768)}}
    if not exclude_temp:
        target = payload["generationConfig"] if protocol == "gemini" else payload["options"] if protocol == "ollama" else payload
        target["temperature"] = temperature
    for key, value in controls.items():
        if key == "generationConfig":
            payload[key].update(value)
        else:
            payload[key] = value
    if tools:
        mode, chosen = _choice(tool_choice)
        chosen = aliases.get(chosen, chosen)
        if chosen and chosen not in {f["name"] for f in definitions}:
            raise ValueError("tool_choice 指向未声明的工具。")
        if protocol == "chat_completions":
            payload["tools"] = [{"type": "function", "function": f} for f in definitions]
            payload["tool_choice"] = {"type": "function", "function": {"name": chosen}} if chosen else mode
        elif protocol == "responses":
            payload["tools"] = [{"type": "function", **f, "strict": False} for f in definitions]
            payload["tool_choice"] = {"type": "function", "name": chosen} if chosen else mode
        elif protocol == "anthropic_messages":
            if controls.get("thinking", {}).get("type") in {"enabled", "adaptive"} and mode in {"required", "function"}:
                raise ValueError("Claude thinking 与强制工具调用组合不受此适配器支持，请选 auto。")
            payload["tools"] = [{"name": f["name"], "description": f["description"], "input_schema": f["parameters"]} for f in definitions]
            payload["tool_choice"] = {"type": "tool", "name": chosen} if chosen else {"type": "any" if mode == "required" else mode}
        elif protocol == "gemini":
            payload["tools"] = [{"functionDeclarations": [{"name": f["name"], "description": f["description"],
                                "parametersJsonSchema": f["parameters"]} for f in definitions]}]
            choice = {"mode": {"auto": "AUTO", "none": "NONE", "required": "ANY", "function": "ANY"}[mode]}
            if chosen:
                choice["allowedFunctionNames"] = [chosen]
            payload["toolConfig"] = {"functionCallingConfig": choice}
        else:
            if mode in {"required", "function"}:
                raise ValueError("Ollama native 不支持强制 tool_choice，请用 auto/none。")
            if mode != "none":
                payload["tools"] = [{"type": "function", "function": f} for f in definitions]
    elif tool_choice not in (None, "auto", "none"):
        raise ValueError("指定 tool_choice 前必须提供工具定义。")
    return {"url": endpoint(profile), "headers": headers(profile), "json": payload, "tool_names": wire_names}


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_usage(protocol: str, data: dict) -> dict:
    usage = data.get("usageMetadata") if protocol == "gemini" else data if protocol == "ollama" else data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    read = write = reasoning = None
    if protocol == "anthropic_messages":
        plain = _count(usage.get("input_tokens"))
        read = _count(usage.get("cache_read_input_tokens"))
        write = _count(usage.get("cache_creation_input_tokens"))
        prompt = None if plain is None else plain + (read or 0) + (write or 0)
        if any(key in usage and _count(usage[key]) is None for key in
               ("cache_read_input_tokens", "cache_creation_input_tokens")):
            prompt = None
        completion = _count(usage.get("output_tokens"))
    elif protocol == "gemini":
        prompt = _count(usage.get("promptTokenCount"))
        read = _count(usage.get("cachedContentTokenCount"))
        reasoning = _count(usage.get("thoughtsTokenCount"))
        completion = _count(usage.get("candidatesTokenCount"))
        if completion is not None:
            completion += reasoning or 0
    elif protocol == "ollama":
        prompt = _count(usage.get("prompt_eval_count"))
        completion = _count(usage.get("eval_count"))
        read = _count(usage.get("prompt_eval_cached_count"))
    else:
        is_response = protocol == "responses"
        prompt = _count(usage.get("input_tokens" if is_response else "prompt_tokens"))
        completion = _count(usage.get("output_tokens" if is_response else "completion_tokens"))
        details = usage.get("input_tokens_details" if is_response else "prompt_tokens_details") or {}
        output = usage.get("output_tokens_details" if is_response else "completion_tokens_details") or {}
        if isinstance(details, dict):
            read = _count(details.get("cached_tokens"))
            write = _count(details.get("cache_write_tokens"))
        if isinstance(output, dict):
            reasoning = _count(output.get("reasoning_tokens"))
        if read is None:
            read = _count(usage.get("prompt_cache_hit_tokens"))
    if prompt is None or read is not None and read > prompt:
        read = None
    return {"prompt_tokens": prompt or 0, "completion_tokens": completion or 0,
            "cached_prompt_tokens": read, "cache_write_tokens": write or 0, "reasoning_tokens": reasoning or 0,
            "cache_reported": read is not None, "usage_reported": prompt is not None and completion is not None}


def parse_response(profile: dict, data: dict, tool_names: dict[str, str] | None = None) -> dict:
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("模型返回了错误或无效响应。")
    protocol = protocol_of(profile)
    state = {"protocol": protocol, "connection_id": str(profile.get("connection_id") or ""), "model": profile.get("model")}
    if tool_names:
        state["tool_names"] = dict(tool_names)
    calls, texts = [], []
    finish, status = "", "success"
    actual_model = data.get("model") or data.get("modelVersion") or profile["model"]
    def add_call(raw, call_id, name):
        args, error = _arguments(raw)
        name = (tool_names or {}).get(name, name)
        item = {"id": str(call_id or "call_" + uuid.uuid4().hex[:16]), "name": str(name or ""), "arguments": args}
        if error or not name:
            item["error"] = error or "missing_name"
        calls.append(item)
    if protocol == "chat_completions":
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise ValueError("Chat Completions 响应缺少 assistant message。")
        message = choices[0]["message"]
        state["message"] = copy.deepcopy(message)
        texts.append(_text(message.get("content")))
        finish = str(choices[0].get("finish_reason") or "stop")
        for call in message.get("tool_calls") or []:
            name, args = _function(call)
            add_call(args, call.get("id"), name)
    elif protocol == "responses":
        if not isinstance(data.get("output"), list):
            raise ValueError("Responses 响应缺少 output 数组。")
        state["items"] = copy.deepcopy(data["output"])
        finish = str(data.get("status") or "completed")
        for item in data["output"]:
            if item.get("type") == "message":
                for block in item.get("content") or []:
                    if block.get("type") == "output_text":
                        texts.append(_text(block.get("text")))
                    elif block.get("type") == "refusal":
                        finish = "refusal"
            elif item.get("type") == "function_call":
                add_call(item.get("arguments"), item.get("call_id"), item.get("name"))
    elif protocol == "anthropic_messages":
        if not isinstance(data.get("content"), list):
            raise ValueError("Anthropic 响应缺少 content 内容块。")
        state["blocks"] = copy.deepcopy(data["content"])
        finish = str(data.get("stop_reason") or "end_turn")
        for block in data["content"]:
            if block.get("type") == "text":
                texts.append(_text(block.get("text")))
            elif block.get("type") == "tool_use":
                add_call(block.get("input"), block.get("id"), block.get("name"))
    elif protocol == "gemini":
        candidates = data.get("candidates") or []
        if not candidates:
            raise ValueError("Gemini 未返回候选结果，可能被安全策略阻止。")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        state["parts"] = copy.deepcopy(parts)
        finish = str(candidates[0].get("finishReason") or "STOP")
        for part in parts:
            if "text" in part and not part.get("thought"):
                texts.append(_text(part["text"]))
            if part.get("functionCall"):
                fn = part["functionCall"]
                add_call(fn.get("args"), fn.get("id"), fn.get("name"))
    else:
        if not isinstance(data.get("message"), dict):
            raise ValueError("Ollama 响应缺少 message。")
        state["message"] = copy.deepcopy(data["message"])
        texts.append(_text(data["message"].get("content")))
        finish = str(data.get("done_reason") or "stop") if data.get("done") else "incomplete"
        for call in data["message"].get("tool_calls") or []:
            name, args = _function(call)
            add_call(args, call.get("id"), name)
    if finish in {"length", "max_tokens", "MAX_TOKENS", "incomplete", "in_progress", "queued"}:
        status = "incomplete"
    elif finish not in {"chat_completions": {"stop", "tool_calls", "function_call"},
                        "responses": {"completed"}, "anthropic_messages": {"end_turn", "tool_use", "stop_sequence"},
                        "gemini": {"STOP"}, "ollama": {"stop"}}[protocol]:
        status = "error"
    content = "".join(texts)
    canonical = {"role": "assistant", "content": content, "_provider_state": state}
    if calls:
        canonical["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"],
                                    "arguments": json.dumps(c["arguments"], ensure_ascii=False)}} for c in calls]
    if not content and not calls and status == "success":
        status = "error"
    return {"content": content, "model": str(actual_model), "provider": protocol,
            "protocol": protocol, "usage": normalize_usage(protocol, data), "finish_reason": finish,
            "tool_calls": calls, "assistant_message": canonical, "status": status}
