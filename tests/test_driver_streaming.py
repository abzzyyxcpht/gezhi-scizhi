"""AdaptiveDriver 流式输出测试：增量事件序列、能力回退、异常路径与记账一致性。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import llm, model_registry, prompts, settings
from src.agent_driver import AdaptiveDriver
from src.agent_runtime import AgentRuntime


def response(text="", tool=None, call_id="call-1", args=None):
    calls = [{"id": call_id, "name": tool, "arguments": args or {}}] if tool else []
    return {"content": text, "tool_calls": calls, "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "assistant_message": {"role": "assistant", "content": text,
                                  "tool_calls": [{"id": c["id"], "type": "function", "function": {
                                      "name": c["name"], "arguments": "{}"}} for c in calls]}}


def stream_result(text="", tool=None, call_id="call-1", args=None):
    """与 chat() 同构的流式最终结果。"""
    return response(text=text, tool=tool, call_id=call_id, args=args)


def stream_batches(*batches):
    """按模型调用顺序弹出批次，避免重复消费同一事件序列。"""
    queue = [list(batch) for batch in batches]
    def next_batch(*args, **kwargs):
        return iter(queue.pop(0))
    return next_batch


class DriverStreamingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for patch in (mock.patch.object(settings, "_PATH", self.root / "settings.json"),
                      mock.patch.object(llm, "available", return_value=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def runtime(self):
        return AgentRuntime(offline=False, llm_enabled=True, session_root=self.root)

    def event_types(self, runtime, sid):
        return [e["type"] for e in runtime.sessions.read(sid)]

    def test_streaming_emits_delta_events_before_assistant_message(self):
        runtime = self.runtime()
        events = [{"delta": "你好"}, {"delta": "，世界"}, {"result": stream_result("你好，世界")}]
        with mock.patch.object(llm, "chat_stream_agent", side_effect=lambda *a, **k: iter(events)) as stream, \
                mock.patch.object(llm, "chat", return_value=response("不应走同步")) as chat:
            result = runtime.start("流式汇报")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["content"], "你好，世界")
        chat.assert_not_called()
        stream.assert_called_once()
        sid = result["session_id"]
        relevant = [e for e in runtime.sessions.read(sid)
                    if e["type"] in {"assistant_delta", "assistant_message"}]
        self.assertEqual([e["type"] for e in relevant],
                         ["assistant_delta", "assistant_delta", "assistant_message"])
        self.assertEqual([e["payload"]["delta"] for e in relevant[:2]], ["你好", "，世界"])
        self.assertTrue(all(e["payload"]["step"] == 1 for e in relevant))
        self.assertEqual(relevant[-1]["payload"]["content"], "你好，世界")

    def test_no_streaming_capability_falls_back_to_chat_with_zero_deltas(self):
        profile = {"connection_id": "connection", "profile_id": "profile", "model": "model",
                   "protocol": "responses", "base_url": "https://first.example/v1",
                   "thinking_style": "auto", "reasoning_effort": "auto", "thinking_budget": 4096,
                   "context_window": 32768, "max_output_tokens": 8192, "api_key": "test-key"}
        runtime = self.runtime()
        with mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(profile)), \
                mock.patch.object(llm, "chat_stream_agent") as stream, \
                mock.patch.object(llm, "chat", return_value=response("同步完成")) as chat:
            result = runtime.start("无流式能力")
        self.assertEqual(result["status"], "done")
        stream.assert_not_called()
        chat.assert_called_once()
        self.assertNotIn("assistant_delta", self.event_types(runtime, result["session_id"]))

    def test_stream_disabled_by_setting_falls_back_to_chat(self):
        settings.save({"agent_stream_enabled": False})
        runtime = self.runtime()
        with mock.patch.object(llm, "chat_stream_agent") as stream, \
                mock.patch.object(llm, "chat", return_value=response("同步完成")) as chat:
            result = runtime.start("开关关闭")
        self.assertEqual(result["status"], "done")
        stream.assert_not_called()
        chat.assert_called_once()
        self.assertNotIn("assistant_delta", self.event_types(runtime, result["session_id"]))

    def test_mid_stream_error_goes_model_error_without_fallback(self):
        def broken(*args, **kwargs):
            yield {"delta": "片段一"}
            raise llm.ModelRequestError("传输中断")

        runtime = self.runtime()
        with mock.patch.object(llm, "chat_stream_agent", side_effect=broken), \
                mock.patch.object(llm, "chat", return_value=response("不应重试")) as chat:
            result = runtime.start("中途断流")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "model_error")
        self.assertFalse(result["usage_complete"])
        chat.assert_not_called()
        sid = result["session_id"]
        types = self.event_types(runtime, sid)
        self.assertIn("assistant_delta", types)
        self.assertNotIn("assistant_message", types)
        # 半份增量之后不得再出现后续的 assistant_message / completed。
        self.assertEqual(types[-1], "error")

    def test_pre_stream_error_falls_back_to_sync_path(self):
        runtime = self.runtime()
        with mock.patch.object(llm, "chat_stream_agent",
                               side_effect=llm.ModelRequestError("端点不可用")), \
                mock.patch.object(llm, "chat", return_value=response("回退完成")) as chat:
            result = runtime.start("流式建立失败")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["content"], "回退完成")
        chat.assert_called_once()
        # 首个增量产出前失败：没有内容外泄，不留增量事件。
        self.assertNotIn("assistant_delta", self.event_types(runtime, result["session_id"]))

    def test_compression_still_works_with_streaming(self):
        profile = {"connection_id": "connection", "profile_id": "profile", "model": "model",
                   "protocol": "chat_completions", "base_url": "https://stream.example/v1",
                   "thinking_style": "auto", "reasoning_effort": "auto", "thinking_budget": 4096,
                   "context_window": 400, "max_output_tokens": 8192, "api_key": "test-key"}
        runtime = self.runtime()
        # 单条 tool 结果 ≈ 90 token：第 3 轮后稳超 280 token 软线，留足余量避免边界抖动
        big_rows = {"ok": True, "rows": "数" * 120}
        runtime._register("test.read", "read", "Read", lambda _: big_rows,
                          runtime._obj_schema({}))
        def tool_batch(cid):
            return [{"result": stream_result(tool="test.read", call_id=cid)}]
        steps = stream_batches(
            tool_batch("c1"), tool_batch("c2"), tool_batch("c3"), tool_batch("c4"),
            [{"delta": "汇总"}, {"delta": "完成"}, {"result": stream_result("汇总完成")}])

        def chat_impl(messages, **kwargs):
            if kwargs.get("node") == "context_compress":
                return {"content": "在线摘要：已折叠早期消息。", "tool_calls": [],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
            raise AssertionError("agent_step 不应走同步 chat")

        with mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(profile)), \
                mock.patch.object(prompts, "agent_system", return_value="Test system."), \
                mock.patch.object(llm, "chat_stream_agent", side_effect=steps), \
                mock.patch.object(llm, "chat", side_effect=chat_impl):
            result = runtime.start("压缩触发后仍流式")
        self.assertEqual(result["status"], "done")
        sid = result["session_id"]
        self.assertTrue(any(e["type"] == "context_compressed" for e in runtime.sessions.read(sid)))
        deltas = [e for e in runtime.sessions.read(sid) if e["type"] == "assistant_delta"]
        self.assertEqual([e["payload"]["delta"] for e in deltas], ["汇总", "完成"])
        self.assertTrue(all(e["payload"]["step"] == 5 for e in deltas))

    def test_token_accounting_matches_non_streaming(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "usage_reported": True}
        sync_result = response("同步完成")
        sync_result["usage"] = usage
        stream_events = [{"delta": "流"}, {"result": stream_result("流式完成")}]
        stream_events[-1]["result"]["usage"] = usage

        with mock.patch.object(llm, "chat", return_value=sync_result):
            sync = self.runtime().start("同步会话")
        self.assertEqual(sync["status"], "done")
        with mock.patch.object(llm, "chat_stream_agent", side_effect=lambda *a, **k: iter(stream_events)):
            streamed = self.runtime().start("流式会话")
        self.assertEqual(streamed["status"], "done")
        self.assertEqual(streamed["tokens"], sync["tokens"])
        self.assertEqual(streamed["tokens"], 15)

    def test_streaming_tool_calls_are_replayed_into_messages(self):
        runtime = self.runtime()
        calls = [{"id": "c1", "name": "workspace.read", "arguments": {}}]
        result = stream_result(tool="workspace.read")
        result["tool_calls"] = calls
        result["assistant_message"]["tool_calls"] = [
            {"id": "c1", "type": "function", "function": {"name": "workspace.read", "arguments": "{}"}}]
        events = stream_batches(
            [{"result": result}],
            [{"delta": "已读取"}, {"result": stream_result("已读取")}])
        with mock.patch.object(llm, "chat_stream_agent", side_effect=events):
            result_final = runtime.start("流式工具调用")
        self.assertEqual(result_final["status"], "done")
        saved = json.loads(AdaptiveDriver(runtime).path(result_final["session_id"]).read_text(encoding="utf-8"))
        assistant_ids = {c.get("id") for m in saved["messages"] if m.get("role") == "assistant"
                         for c in (m.get("tool_calls") or [])}
        self.assertIn("c1", assistant_ids)
        for m in saved["messages"]:
            if m.get("role") == "tool":
                self.assertIn(m.get("tool_call_id"), assistant_ids)


if __name__ == "__main__":
    unittest.main()
