"""Five wire protocols through real LLM transport, AdaptiveDriver and tool runtime.

Only HTTP transport and configured model identity are replaced. No paid request,
llm.chat stub, driver stub or tool-execution stub is used.
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from src import config, llm, logging_util, model_registry, settings
from src.agent_driver import AdaptiveDriver
from src.agent_runtime import AgentRuntime, ToolSpec


EVIDENCE = "evidence-local-fixture-001"
OPAQUE = "private-provider-continuation"
CALL_ID = "provider-call-001"
ARGUMENTS = {"query": "native protocol audit"}
FINAL = f"已核对证据 {EVIDENCE}。"


def profile(protocol):
    suffix = "/v1beta" if protocol == "gemini" else "" if protocol == "ollama" else "/v1"
    return {"connection_id": "isolated-loop", "profile_id": "loop-model", "protocol": protocol,
            "base_url": "https://native-loop.invalid" + suffix, "api_key": "isolated-loop-key",
            "model": "configured-loop-model", "thinking_style": "auto", "reasoning_effort": "auto",
            "thinking_budget": 4096, "max_output_tokens": 8192, "context_window": 131072,
            "prompt_cache": True, "auth_style": "auto"}


def provider_reply(protocol, wire_name, final=False):
    prompt, cached, completion, reasoning = (160, 100, 30, 4) if final else (120, 60, 20, 2)
    model = "served-" + protocol
    if protocol == "chat_completions":
        message = ({"role": "assistant", "content": FINAL} if final else {
            "role": "assistant", "content": None, "reasoning_content": OPAQUE,
            "tool_calls": [{"id": CALL_ID, "type": "function", "function": {
                "name": wire_name, "arguments": json.dumps(ARGUMENTS)}}]})
        return {"model": model, "choices": [{"message": message, "finish_reason": "stop" if final else "tool_calls"}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                          "prompt_tokens_details": {"cached_tokens": cached},
                          "completion_tokens_details": {"reasoning_tokens": reasoning}}}
    if protocol == "responses":
        output = ([{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": FINAL}]}]
                  if final else [{"type": "reasoning", "id": "rs-fixture", "encrypted_content": OPAQUE},
                                 {"type": "function_call", "call_id": CALL_ID,
                                  "name": wire_name, "arguments": json.dumps(ARGUMENTS)}])
        return {"model": model, "status": "completed", "output": output,
                "usage": {"input_tokens": prompt, "output_tokens": completion,
                          "input_tokens_details": {"cached_tokens": cached},
                          "output_tokens_details": {"reasoning_tokens": reasoning}}}
    if protocol == "anthropic_messages":
        content = ([{"type": "text", "text": FINAL}] if final else [
            {"type": "thinking", "thinking": "provider continuation", "signature": OPAQUE},
            {"type": "tool_use", "id": CALL_ID, "name": wire_name, "input": copy.deepcopy(ARGUMENTS)}])
        return {"model": model, "content": content, "stop_reason": "end_turn" if final else "tool_use",
                "usage": {"input_tokens": prompt - cached - 20, "cache_read_input_tokens": cached,
                          "cache_creation_input_tokens": 20, "output_tokens": completion}}
    if protocol == "gemini":
        # Valid Gemini responses may omit the tool ID; the runtime still needs one.
        parts = ([{"text": FINAL}] if final else [{"thoughtSignature": OPAQUE,
                  "functionCall": {"name": wire_name, "args": copy.deepcopy(ARGUMENTS)}}])
        return {"modelVersion": model, "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": prompt, "cachedContentTokenCount": cached,
                                  "candidatesTokenCount": completion - reasoning, "thoughtsTokenCount": reasoning}}
    message = {"role": "assistant", "content": FINAL if final else ""}
    if not final:
        message.update(thinking=OPAQUE, tool_calls=[{"function": {"name": wire_name, "arguments": copy.deepcopy(ARGUMENTS)}}])
    return {"model": model, "message": message, "done": True, "done_reason": "stop",
            "prompt_eval_count": prompt, "prompt_eval_cached_count": cached, "eval_count": completion}


class NativeProtocolLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model_log = self.root / "logs" / "model_calls.jsonl"
        for owner, attr, value in (
            (settings, "_PATH", self.root / "settings.json"),
            (logging_util, "_MODEL_LOG", self.model_log),
            (logging_util, "_TOOL_LOG", self.root / "logs" / "tool_calls.jsonl"),
            (config, "LLM_ENABLED", True),
        ):
            patch = mock.patch.object(owner, attr, value, create=attr == "LLM_ENABLED")
            patch.start()
            self.addCleanup(patch.stop)
        llm.close_clients()
        self.addCleanup(llm.close_clients)

    def _wire_name(self, protocol, body):
        if protocol == "gemini":
            definitions = body["tools"][0]["functionDeclarations"]
        else:
            definitions = body["tools"]
        self.assertEqual(len(definitions), 1, "Only the isolated read-only test tool may be exposed")
        tool = definitions[0].get("function") or definitions[0]
        name = tool["name"]
        if protocol in {"chat_completions", "responses", "anthropic_messages"}:
            self.assertRegex(name, r"^[A-Za-z0-9_-]{1,64}$")
            self.assertNotEqual(name, "test.probe", "Dotted runtime names need a legal wire alias")
        return name

    def _observation(self, protocol, body, initial, wire_name):
        if protocol == "chat_completions":
            self.assertEqual(body["messages"][-2], initial["choices"][0]["message"])
            result = body["messages"][-1]
            self.assertEqual((result["role"], result["tool_call_id"]), ("tool", CALL_ID))
            self.assertNotIn("name", result)
            return json.loads(result["content"])
        if protocol == "responses":
            self.assertEqual(body["input"][-3:-1], initial["output"])
            result = body["input"][-1]
            self.assertEqual((result["type"], result["call_id"]), ("function_call_output", CALL_ID))
            return json.loads(result["output"])
        if protocol == "anthropic_messages":
            self.assertEqual(body["messages"][-2]["content"], initial["content"])
            result = body["messages"][-1]["content"][0]
            self.assertEqual((result["type"], result["tool_use_id"]), ("tool_result", CALL_ID))
            return json.loads(result["content"])
        if protocol == "gemini":
            self.assertEqual(body["contents"][-2]["parts"], initial["candidates"][0]["content"]["parts"])
            result = body["contents"][-1]["parts"][0]["functionResponse"]
            self.assertEqual(result["name"], wire_name)
            self.assertNotIn("id", result, "Do not send a synthetic local ID as a provider-generated ID")
            return result["response"]
        self.assertEqual(body["messages"][-2], initial["message"])
        result = body["messages"][-1]
        self.assertEqual((result["role"], result["tool_name"]), ("tool", wire_name))
        return json.loads(result["content"])

    def _run_protocol(self, protocol):
        requests, handler_calls, transport_assertions = [], [], []
        initial, wire_name = None, None
        def probe(arguments):
            handler_calls.append(copy.deepcopy(arguments))
            return {"ok": True, "evidence_id": EVIDENCE, "query": arguments["query"]}

        def handle(request):
            nonlocal initial, wire_name
            try:
                self.assertEqual(request.url.host, "native-loop.invalid")
                self.assertEqual(request.method, "POST")
                body = json.loads(request.content)
                requests.append(body)
                current_name = self._wire_name(protocol, body)
                if len(requests) == 1:
                    self.assertFalse(handler_calls)
                    wire_name = current_name
                    initial = provider_reply(protocol, wire_name)
                    return httpx.Response(200, json=initial)
                self.assertEqual(len(requests), 2, "The loop must finish after one tool round")
                self.assertEqual(current_name, wire_name)
                self.assertEqual(len(handler_calls), 1)
                observation = self._observation(protocol, body, initial, wire_name)
                self.assertTrue(observation["ok"])
                self.assertEqual(observation["evidence_id"], EVIDENCE)
                self.assertEqual(observation["query"], ARGUMENTS["query"])
                return httpx.Response(200, json=provider_reply(protocol, wire_name, final=True))
            except AssertionError as exc:
                transport_assertions.append(str(exc))
                raise

        real_client = httpx.Client
        with mock.patch.object(model_registry, "resolve", return_value=profile(protocol)), \
             mock.patch.object(llm.httpx, "Client", side_effect=lambda **kw:
                               real_client(transport=httpx.MockTransport(handle), **kw)):
            runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=self.root / "sessions")
            runtime.tools = {"test.probe": ToolSpec("test.probe", "read", "Return local evidence metadata", probe,
                {"type": "object", "properties": {"query": {"type": "string"}},
                 "required": ["query"], "additionalProperties": False})}
            result = runtime.start("先调用证据核查工具，再据返回的 evidence_id 汇报。", agent="research")

        self.assertEqual(transport_assertions, [])
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(result["driver"], "adaptive")
        self.assertEqual(result["content"], FINAL)
        self.assertEqual(result["steps"], 2)
        self.assertEqual(result["tokens"], 330)
        self.assertTrue(result["usage_complete"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(handler_calls), 1)
        self.assertEqual(handler_calls[0]["query"], ARGUMENTS["query"])
        sid = result["session_id"]
        self.assertEqual(handler_calls[0]["__session_id"], sid)
        private = json.loads(AdaptiveDriver(runtime).path(sid).read_text(encoding="utf-8"))
        assistant, tool = private["messages"][2:4]
        self.assertEqual(assistant["_provider_state"]["protocol"], protocol)
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "test.probe")
        self.assertEqual(tool["name"], "test.probe")
        self.assertEqual(tool["tool_call_id"], assistant["tool_calls"][0]["id"])
        self.assertEqual(json.loads(tool["content"])["evidence_id"], EVIDENCE)
        public_events = runtime.sessions.read(sid)
        self.assertEqual(sum(e["type"] == "tool_call" for e in public_events), 1)
        self.assertEqual(sum(e["type"] == "tool_result" for e in public_events), 1)
        self.assertNotIn(OPAQUE, json.dumps(public_events))
        rows = [json.loads(line) for line in self.model_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["status"], "success")
            self.assertEqual(row["protocol"], protocol)
            self.assertEqual(row["connection_id"], "isolated-loop")
            self.assertEqual(row["model"], "served-" + protocol)
            self.assertTrue(row["cache_reported"])
        self.assertEqual(sum(r["prompt_tokens"] for r in rows), 280)
        self.assertEqual(sum(r["completion_tokens"] for r in rows), 50)
        self.assertEqual(sum(r["cached_prompt_tokens"] for r in rows), 160)
        self.assertNotIn("isolated-loop-key", json.dumps(rows))

    def test_chat_completions_native_tool_loop(self):
        self._run_protocol("chat_completions")

    def test_responses_native_tool_loop(self):
        self._run_protocol("responses")

    def test_anthropic_messages_native_tool_loop(self):
        self._run_protocol("anthropic_messages")

    def test_gemini_native_tool_loop(self):
        self._run_protocol("gemini")

    def test_ollama_native_tool_loop(self):
        self._run_protocol("ollama")


if __name__ == "__main__":
    unittest.main()
