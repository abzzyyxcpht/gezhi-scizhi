"""Offline HTTP contracts for protocol adapters, tools, thinking and real usage."""
from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from unittest import mock

import httpx

from src import config, llm


MESSAGES = [{"role": "system", "content": "stable rules"}, {"role": "user", "content": "query"}]
TOOLS = [{"type": "function", "function": {"name": "research.status", "description": "Status",
          "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}},
                         "required": ["task_id"]}}}]


def settings(protocol="chat_completions", **kw):
    return {"connection_id": "unit", "protocol": protocol, "base_url": "https://unit.invalid/v1",
            "api_key": "secret-unit", "model": "unit-model", "thinking_style": "auto",
            "reasoning_effort": "auto", "thinking_budget": 4096, "max_output_tokens": 8192,
            "context_window": 32768, "prompt_cache": True, "auth_style": "auto", **kw}


def response(protocol, *, tools=False):
    args = {"task_id": "t1"}
    if protocol == "chat_completions":
        message = {"role": "assistant", "content": "answer"}
        if tools:
            message.update(content=None, reasoning_content="opaque continuation", tool_calls=[
                {"id": "call1", "type": "function", "function": {"name": "research.status", "arguments": json.dumps(args)}}])
        return {"model": "served", "choices": [{"message": message, "finish_reason": "tool_calls" if tools else "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 12, "prompt_tokens_details": {"cached_tokens": 60},
                          "completion_tokens_details": {"reasoning_tokens": 2}}}
    if protocol == "responses":
        output = ([{"id": "rs1", "type": "reasoning", "encrypted_content": "opaque"},
                   {"type": "function_call", "call_id": "call1", "name": "research.status", "arguments": json.dumps(args)}]
                  if tools else [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]}])
        return {"model": "served", "status": "completed", "output": output,
                "usage": {"input_tokens": 100, "output_tokens": 12,
                          "input_tokens_details": {"cached_tokens": 60}, "output_tokens_details": {"reasoning_tokens": 2}}}
    if protocol == "anthropic_messages":
        content = ([{"type": "thinking", "thinking": "opaque", "signature": "sig1"},
                    {"type": "tool_use", "id": "call1", "name": "research.status", "input": args}]
                   if tools else [{"type": "text", "text": "answer"}])
        return {"model": "served", "content": content, "stop_reason": "tool_use" if tools else "end_turn",
                "usage": {"input_tokens": 30, "cache_read_input_tokens": 60, "cache_creation_input_tokens": 10, "output_tokens": 12}}
    if protocol == "gemini":
        parts = ([{"functionCall": {"id": "call1", "name": "research.status", "args": args}, "thoughtSignature": "sig1"}]
                 if tools else [{"text": "answer"}])
        return {"modelVersion": "served", "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 100, "cachedContentTokenCount": 60, "candidatesTokenCount": 10, "thoughtsTokenCount": 2}}
    return {"model": "served", "message": {"role": "assistant", "content": "" if tools else "answer", **(
                {"tool_calls": [{"function": {"name": "research.status", "arguments": args}}]} if tools else {})},
            "done": True, "done_reason": "stop", "prompt_eval_count": 100, "prompt_eval_cached_count": 60, "eval_count": 12}


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        if hasattr(llm, "close_clients"):
            llm.close_clients()
            self.addCleanup(llm.close_clients)
        self.log = self.stack.enter_context(mock.patch.object(llm, "log_model", return_value="trace-unit"))
        self.stack.enter_context(mock.patch.object(config, "LLM_BASE_URL", "https://unit.invalid/v1"))
        self.stack.enter_context(mock.patch.object(config, "LLM_API_KEY", "secret-unit"))
        self.stack.enter_context(mock.patch.object(config, "LLM_MODEL", "unit-model"))
        self.requests = []

    def connect(self, profile, replies):
        self.stack.enter_context(mock.patch.object(llm, "_resolve_config", return_value=profile, create=True))
        replies = iter(replies)
        def handle(request):
            self.requests.append(request)
            reply = next(replies)
            if isinstance(reply, httpx.Response):
                return reply
            return httpx.Response(200, json=reply)
        real_client = httpx.Client
        self.factory = self.stack.enter_context(mock.patch.object(llm.httpx, "Client", side_effect=lambda **kw:
            real_client(transport=httpx.MockTransport(handle), **kw)))

    def body(self, index=-1):
        return json.loads(self.requests[index].content)

    def test_chat_retains_trace_and_exposes_actual_cache_usage(self):
        self.connect(settings(), [response("chat_completions")])
        result = llm.chat(MESSAGES)
        self.assertEqual(result["content"], "answer")
        self.assertEqual(result["trace_id"], "trace-unit")
        self.assertIn("usage", result)
        self.assertEqual(result["usage"]["cached_prompt_tokens"], 60)
        self.assertEqual(result["model"], "served")

    def test_five_protocols_use_real_endpoints_headers_and_usage(self):
        endpoints = {"chat_completions": "/v1/chat/completions", "responses": "/v1/responses",
                     "anthropic_messages": "/v1/messages", "gemini": "/v1beta/models/unit-model:generateContent",
                     "ollama": "/api/chat"}
        for protocol, path in endpoints.items():
            with self.subTest(protocol=protocol), ExitStack() as scope:
                old = self.stack
                self.stack = scope
                profile = settings(protocol, base_url="https://unit.invalid" if protocol in {"gemini", "ollama"} else "https://unit.invalid/v1")
                self.connect(profile, [response(protocol)])
                r = llm.chat(MESSAGES)
                req = self.requests[-1]
                self.assertEqual(req.url.path, path)
                self.assertEqual(r["content"], "answer")
                self.assertEqual(r["protocol"], protocol)
                self.assertEqual(r["usage"]["prompt_tokens"], 100)
                self.assertEqual(r["usage"]["completion_tokens"], 12)
                self.assertEqual(r["usage"]["cached_prompt_tokens"], 60)
                self.assertTrue(r["usage"]["cache_reported"])
                if protocol == "anthropic_messages":
                    self.assertEqual(req.headers["x-api-key"], "secret-unit")
                    self.assertEqual(req.headers["anthropic-version"], "2023-06-01")
                    self.assertNotIn("authorization", req.headers)
                    self.assertEqual(r["usage"]["cache_write_tokens"], 10)
                elif protocol == "gemini":
                    self.assertEqual(req.headers["x-goog-api-key"], "secret-unit")
                    self.assertNotIn("key=", str(req.url))
                else:
                    self.assertEqual(req.headers["authorization"], "Bearer secret-unit")
                llm.close_clients()
                self.stack = old

    def test_unknown_cache_is_distinct_from_zero(self):
        absent = response("chat_completions")
        absent["usage"].pop("prompt_tokens_details")
        zero = response("chat_completions")
        zero["usage"]["prompt_tokens_details"]["cached_tokens"] = 0
        self.connect(settings(), [absent, zero])
        a, b = llm.chat(MESSAGES), llm.chat(MESSAGES)
        self.assertIsNone(a["usage"]["cached_prompt_tokens"])
        self.assertFalse(a["usage"]["cache_reported"])
        self.assertEqual(b["usage"]["cached_prompt_tokens"], 0)
        self.assertTrue(b["usage"]["cache_reported"])

    def test_auto_never_invents_reasoning_support(self):
        self.connect(settings(), [response("chat_completions")])
        llm.chat(MESSAGES)
        self.assertNotIn("reasoning_effort", self.body())
        self.assertNotIn("thinking", self.body())

    def test_unknown_model_explicit_effort_is_rejected_before_http(self):
        self.connect(settings(reasoning_effort="high"), [])
        with self.assertRaisesRegex(ValueError, "思考|thinking|reasoning"):
            llm.chat(MESSAGES)
        self.assertFalse(self.requests)

    def test_responses_maps_effort_and_encrypted_continuation(self):
        self.connect(settings("responses", thinking_style="openai_effort", reasoning_effort="high"), [response("responses")])
        llm.chat(MESSAGES)
        self.assertEqual(self.body()["reasoning"], {"effort": "high"})
        self.assertIn("reasoning.encrypted_content", self.body()["include"])
        self.assertFalse(self.body()["store"])
        self.assertNotIn("temperature", self.body())

    def test_anthropic_budget_is_reserved_beside_visible_output(self):
        self.connect(settings("anthropic_messages", model="claude-sonnet-4-5", thinking_style="anthropic_budget", reasoning_effort="high"), [response("anthropic_messages")])
        llm.chat(MESSAGES, max_tokens=2048)
        self.assertEqual(self.body()["thinking"], {"type": "enabled", "budget_tokens": 4096})
        self.assertEqual(self.body()["max_tokens"], 6144)
        self.assertNotIn("temperature", self.body())

    def test_anthropic_budget_over_cap_is_not_silently_clamped(self):
        self.connect(settings("anthropic_messages", thinking_style="anthropic_budget", reasoning_effort="high", max_output_tokens=4096), [])
        with self.assertRaisesRegex(ValueError, "预算|budget|上限"):
            llm.chat(MESSAGES, max_tokens=2048)
        self.assertFalse(self.requests)

    def test_anthropic_adaptive_and_gemini_level_payloads(self):
        cases = [(settings("anthropic_messages", thinking_style="anthropic_adaptive", reasoning_effort="medium"),
                  lambda b: self.assertEqual((b["thinking"], b["output_config"]), ({"type": "adaptive"}, {"effort": "medium"}))),
                 (settings("gemini", model="gemini-3-flash", thinking_style="gemini_level", reasoning_effort="low"),
                  lambda b: self.assertEqual(b["generationConfig"]["thinkingConfig"], {"thinkingLevel": "LOW"}))]
        for profile, check in cases:
            with self.subTest(protocol=profile["protocol"]), ExitStack() as scope:
                old, self.stack = self.stack, scope
                self.connect(profile, [response(profile["protocol"])])
                llm.chat(MESSAGES)
                check(self.body())
                llm.close_clients()
                self.stack = old

    def test_off_is_distinct_and_unsupported_off_is_rejected(self):
        self.connect(settings("ollama", model="gpt-oss:20b", reasoning_effort="off"), [])
        with self.assertRaisesRegex(ValueError, "关闭|off|disable"):
            llm.chat(MESSAGES)
        self.assertFalse(self.requests)

    def test_five_tool_roundtrips_preserve_ids_arguments_and_opaque_state(self):
        for protocol in ("chat_completions", "responses", "anthropic_messages", "gemini", "ollama"):
            with self.subTest(protocol=protocol), ExitStack() as scope:
                old, self.stack = self.stack, scope
                first = response(protocol, tools=True)
                self.connect(settings(protocol), [first, response(protocol)])
                r = llm.chat(MESSAGES, tools=TOOLS, tool_choice="auto")
                call = r["tool_calls"][0]
                self.assertEqual(call["name"], "research.status")
                self.assertEqual(call["arguments"], {"task_id": "t1"})
                self.assertTrue(call["id"])
                r2 = llm.chat([*MESSAGES, r["assistant_message"], {"role": "tool", "tool_call_id": call["id"],
                             "name": call["name"], "content": '{"ok":true}'}], tools=TOOLS)
                self.assertEqual(r2["content"], "answer")
                body = self.body()
                if protocol == "chat_completions":
                    self.assertEqual(body["messages"][2]["reasoning_content"], "opaque continuation")
                    self.assertEqual(body["messages"][-1]["tool_call_id"], call["id"])
                elif protocol == "responses":
                    self.assertIn(first["output"][0], body["input"])
                    self.assertEqual(body["input"][-1]["type"], "function_call_output")
                elif protocol == "anthropic_messages":
                    self.assertEqual(body["messages"][1]["content"][0]["signature"], "sig1")
                    self.assertEqual(body["messages"][-1]["content"][0]["tool_use_id"], call["id"])
                elif protocol == "gemini":
                    self.assertEqual(body["contents"][1]["parts"][0]["thoughtSignature"], "sig1")
                    self.assertEqual(body["contents"][-1]["parts"][0]["functionResponse"]["response"], {"ok": True})
                else:
                    self.assertEqual(body["messages"][-1]["tool_name"], call["name"])
                llm.close_clients()
                self.stack = old

    def test_connection_pool_reuses_and_isolates_credentials(self):
        profile = settings()
        self.connect(profile, [response("chat_completions")] * 3)
        llm.chat(MESSAGES)
        llm.chat(MESSAGES)
        self.assertEqual(self.factory.call_count, 1)
        profile["api_key"] = "another-key"
        llm.chat(MESSAGES)
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.requests[-1].headers["authorization"], "Bearer another-key")

    def test_http_error_logged_without_leaking_provider_body_or_key(self):
        self.connect(settings(), [httpx.Response(401, json={"error": {"message": "secret-unit private prompt"}})])
        with self.assertRaises(Exception) as caught:
            llm.chat(MESSAGES)
        self.assertNotIn("secret-unit", str(caught.exception))
        self.assertNotIn("private prompt", str(caught.exception))
        self.assertEqual(self.log.call_args.kwargs["status"], "error")
        self.assertNotIn("secret-unit", str(self.log.call_args))

    def test_truncation_is_not_logged_as_success(self):
        reply = response("chat_completions")
        reply["choices"][0]["finish_reason"] = "length"
        self.connect(settings(), [reply])
        with self.assertRaisesRegex(Exception, "截断|incomplete|truncat"):
            llm.chat(MESSAGES)
        self.assertEqual(self.log.call_args.kwargs["status"], "incomplete")
        self.assertEqual(self.log.call_args.kwargs["prompt_tokens"], 100)

    def test_stream_interruption_after_content_is_reported(self):
        content = 'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        self.connect(settings(), [httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})])
        stream = llm.chat_stream(MESSAGES)
        self.assertEqual(next(stream), "partial")
        with self.assertRaisesRegex(Exception, "中断|结束|incomplete|truncat"):
            list(stream)
        self.assertEqual(self.log.call_args.kwargs["status"], "incomplete")

    def test_other_protocols_explicitly_use_nonstreaming_generator(self):
        self.connect(settings("responses"), [response("responses")])
        self.assertEqual(list(llm.chat_stream(MESSAGES)), ["answer"])
        self.assertFalse(self.body().get("stream", False))
        catalog = {p["id"]: p for p in llm.capabilities()["protocols"]}
        self.assertFalse(catalog["responses"]["native_streaming"])

    def test_error_response_with_valid_usage_retains_actual_usage_and_model(self):
        reply = response("gemini")
        reply["candidates"] = []
        reply["promptFeedback"] = {"blockReason": "SAFETY"}
        self.connect(settings("gemini"), [reply])
        with self.assertRaises(Exception):
            llm.chat(MESSAGES)
        self.assertEqual(self.log.call_args.kwargs["prompt_tokens"], 100)
        self.assertEqual(self.log.call_args.kwargs["model"], "served")

    def test_anthropic_invalid_cache_component_marks_usage_incomplete(self):
        reply = response("anthropic_messages")
        reply["usage"]["cache_creation_input_tokens"] = "not-a-count"
        self.connect(settings("anthropic_messages"), [reply])
        r = llm.chat(MESSAGES)
        self.assertFalse(r["usage"]["cache_reported"])
        self.assertFalse(r["usage"]["usage_reported"])

    def test_provider_state_cannot_be_replayed_under_a_different_model(self):
        profile = settings("anthropic_messages")
        self.connect(profile, [response("anthropic_messages", tools=True)])
        r = llm.chat(MESSAGES, tools=TOOLS)
        profile["model"] = "another-model"
        with self.assertRaisesRegex(ValueError, "模型|model"):
            llm.chat([*MESSAGES, r["assistant_message"]], tools=TOOLS)
        self.assertEqual(len(self.requests), 1)

    def test_openai_nonreasoning_model_rejects_effort_even_with_explicit_style(self):
        self.connect(settings(model="gpt-4o", thinking_style="openai_effort", reasoning_effort="high"), [])
        with self.assertRaisesRegex(ValueError, "思考|reasoning"):
            llm.chat(MESSAGES)
        self.assertFalse(self.requests)

    def test_gemini_budget_does_not_silently_stand_in_for_gemini3_level(self):
        self.connect(settings("gemini", model="gemini-3-pro", thinking_style="gemini_budget", reasoning_effort="high"), [])
        with self.assertRaisesRegex(ValueError, "level|Level|Gemini"):
            llm.chat(MESSAGES)

    def test_unknown_stop_reason_is_not_success(self):
        reply = response("gemini")
        reply["candidates"][0]["finishReason"] = "UNEXPECTED_TOOL_CALL"
        self.connect(settings("gemini"), [reply])
        with self.assertRaises(Exception):
            llm.chat(MESSAGES)
        self.assertEqual(self.log.call_args.kwargs["status"], "error")

    def test_deepseek_cache_miss_is_not_cache_write(self):
        reply = response("chat_completions")
        reply["usage"] = {"prompt_tokens": 100, "completion_tokens": 12,
                          "prompt_cache_hit_tokens": 60, "prompt_cache_miss_tokens": 40}
        self.connect(settings(model="deepseek-test"), [reply])
        r = llm.chat(MESSAGES)
        self.assertEqual(r["usage"]["cached_prompt_tokens"], 60)
        self.assertEqual(r["usage"]["cache_write_tokens"], 0)

    def test_invalid_cache_read_values_are_unknown(self):
        replies = []
        for value in (-1, True, "60", 101, None):
            reply = response("chat_completions")
            reply["usage"]["prompt_tokens_details"]["cached_tokens"] = value
            replies.append(reply)
        self.connect(settings(), replies)
        for _ in replies:
            r = llm.chat(MESSAGES)
            self.assertIsNone(r["usage"]["cached_prompt_tokens"])
            self.assertFalse(r["usage"]["cache_reported"])

    def test_auth_style_is_independent_of_messages_protocol(self):
        self.connect(settings("anthropic_messages", auth_style="bearer"), [response("anthropic_messages")])
        llm.chat(MESSAGES)
        self.assertEqual(self.requests[-1].headers["authorization"], "Bearer secret-unit")
        self.assertNotIn("x-api-key", self.requests[-1].headers)

    def test_no_key_means_no_auth_header_for_local_models(self):
        self.connect(settings("ollama", api_key=""), [response("ollama")])
        llm.chat(MESSAGES)
        self.assertNotIn("authorization", self.requests[-1].headers)

    def test_tool_choice_is_translated_and_unsupported_force_is_rejected(self):
        from src import model_protocols as protocols
        choice = {"type": "function", "function": {"name": "research.status"}}
        result = protocols.build_request(settings("responses"), MESSAGES, .2, 2048, TOOLS, choice)
        self.assertEqual(result["json"]["tool_choice"], {"type": "function", "name": result["json"]["tools"][0]["name"]})
        result = protocols.build_request(settings("anthropic_messages"), MESSAGES, .2, 2048, TOOLS, choice)
        self.assertEqual(result["json"]["tool_choice"], {"type": "tool", "name": result["json"]["tools"][0]["name"]})
        result = protocols.build_request(settings("gemini"), MESSAGES, .2, 2048, TOOLS, choice)
        self.assertEqual(result["json"]["toolConfig"]["functionCallingConfig"],
                         {"mode": "ANY", "allowedFunctionNames": ["research.status"]})
        with self.assertRaisesRegex(ValueError, "tool_choice"):
            protocols.build_request(settings("ollama"), MESSAGES, .2, 2048, TOOLS, choice)

    def test_gemini_without_provider_tool_id_uses_name_and_preserves_signature(self):
        reply = response("gemini", tools=True)
        del reply["candidates"][0]["content"]["parts"][0]["functionCall"]["id"]
        self.connect(settings("gemini"), [reply, response("gemini")])
        r = llm.chat(MESSAGES, tools=TOOLS)
        call = r["tool_calls"][0]
        llm.chat([*MESSAGES, r["assistant_message"], {"role": "tool", "tool_call_id": call["id"], "content": "result"}], tools=TOOLS)
        native = self.body()["contents"]
        self.assertNotIn("id", native[-1]["parts"][0]["functionResponse"])
        self.assertEqual(native[-1]["parts"][0]["functionResponse"]["name"], "research.status")
        self.assertEqual(native[-2]["parts"][0]["thoughtSignature"], "sig1")

    def test_malformed_tool_arguments_are_returned_as_errors(self):
        reply = response("chat_completions", tools=True)
        reply["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "not-json"
        self.connect(settings(), [reply])
        r = llm.chat(MESSAGES, tools=TOOLS)
        self.assertEqual(r["tool_calls"][0]["error"], "invalid_arguments")
        self.assertEqual(r["tool_calls"][0]["arguments"], {})

    def test_stream_usage_final_chunk_and_actual_model_are_logged_once(self):
        content = '\n\n'.join([
            'data: {"model":"served-stream","choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":12,"prompt_tokens_details":{"cached_tokens":60}}}',
            'data: [DONE]', ''])
        self.connect(settings(), [httpx.Response(200, text=content)])
        self.assertEqual(list(llm.chat_stream(MESSAGES)), ["answer"])
        self.assertEqual(self.log.call_count, 1)
        self.assertEqual(self.log.call_args.kwargs["cached_prompt_tokens"], 60)
        self.assertEqual(self.log.call_args.kwargs["model"], "served-stream")
        self.assertEqual(self.body()["stream_options"], {"include_usage": True})

    def test_pool_eviction_closes_idle_clients_and_stays_bounded(self):
        profile = settings()
        self.connect(profile, [response("chat_completions")] * 12)
        first = None
        for index in range(12):
            profile["api_key"] = "key-" + str(index)
            llm.chat(MESSAGES)
            if first is None:
                first = next(iter(llm._CLIENTS.values())).client
        self.assertLessEqual(len(llm._CLIENTS), llm._POOL_LIMIT)
        self.assertTrue(first.is_closed)

    def test_reset_during_active_request_defers_client_close(self):
        self.connect(settings(), [])
        with llm._client(settings()) as client:
            llm.close_clients()
            self.assertFalse(client.is_closed)
        self.assertTrue(client.is_closed)

    def test_prompt_fingerprint_never_contains_prompt_or_key(self):
        self.connect(settings(), [response("chat_completions")])
        llm.chat(MESSAGES)
        digest = self.log.call_args.kwargs["prompt_fingerprint"]
        self.assertRegex(digest, r"^[a-f0-9]{64}$")
        self.assertNotIn("stable rules", str(self.log.call_args))
        self.assertNotIn("secret-unit", str(self.log.call_args))

    def test_ignored_stream_consumes_first_json_without_another_paid_request(self):
        self.connect(settings(), [response("chat_completions")])
        self.assertEqual(list(llm.chat_stream(MESSAGES)), ["answer"])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.log.call_count, 1)
        self.assertEqual(self.log.call_args.kwargs["cached_prompt_tokens"], 60)

    def test_dotted_runtime_names_are_safe_on_wire_and_roundtrip_back(self):
        from src import model_protocols as protocols
        for protocol in ("chat_completions", "responses", "anthropic_messages"):
            with self.subTest(protocol=protocol):
                profile = settings(protocol)
                req = protocols.build_request(profile, MESSAGES, .2, 2048, TOOLS)
                definition = req["json"]["tools"][0]
                wire = (definition.get("function") or definition)["name"]
                self.assertRegex(wire, r"^[A-Za-z0-9_-]{1,64}$")
                self.assertNotEqual(wire, "research.status")
                raw = response(protocol, tools=True)
                if protocol == "chat_completions":
                    raw["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = wire
                elif protocol == "responses":
                    raw["output"][1]["name"] = wire
                else:
                    raw["content"][1]["name"] = wire
                parsed = protocols.parse_response(profile, raw, req["tool_names"])
                self.assertEqual(parsed["tool_calls"][0]["name"], "research.status")
                again = protocols.build_request(profile, [*MESSAGES, parsed["assistant_message"],
                    {"role": "tool", "tool_call_id": "call1", "name": "research.status", "content": "result"}], .2, 2048, TOOLS)
                self.assertEqual(again["tool_names"], req["tool_names"])
                if protocol == "chat_completions":
                    self.assertNotIn("name", again["json"]["messages"][-1])

    def test_native_function_aliases_are_collision_free(self):
        from src import model_protocols as protocols
        tools = [{"type": "function", "function": {"name": n}} for n in
                 ("research.status", "research_status", "research-status", "中" * 100)]
        req = protocols.build_request(settings(), MESSAGES, .2, 2048, tools)
        names = [t["function"]["name"] for t in req["json"]["tools"]]
        self.assertEqual(len(set(names)), 4)
        for name in names:
            self.assertRegex(name, r"^[A-Za-z0-9_-]{1,64}$")

    def test_gemini_uses_json_schema_parameter_field(self):
        from src import model_protocols as protocols
        req = protocols.build_request(settings("gemini"), MESSAGES, .2, 2048, TOOLS)
        fn = req["json"]["tools"][0]["functionDeclarations"][0]
        self.assertEqual(fn["parametersJsonSchema"], TOOLS[0]["function"]["parameters"])
        self.assertNotIn("parameters", fn)


if __name__ == "__main__":
    unittest.main()
