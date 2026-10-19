# -*- coding: utf-8 -*-
"""上下文 token 估算（启发式，非 tokenizer）与 token 口径压缩阈值测试。

口径：ASCII 约 4 字符/token，CJK 约 1.5 字符/token，向上取整求和（偏保守）。
硬阈值 = context_window（token 数），软阈值 0.7 / 再压缩 0.9 保留。
"""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from src import llm, model_registry, prompts, settings
from src.agent_driver import AdaptiveDriver
from src.agent_runtime import AgentRuntime
from src.agents.specs import ROSTER
from tests.test_driver_resilience import driver_state, response
from tests.test_driver_streaming import stream_batches, stream_result


class EstimateTokensTest(unittest.TestCase):
    def setUp(self):
        self.driver = AdaptiveDriver(mock.Mock())

    def test_ascii_is_four_chars_per_token(self):
        self.assertEqual(self.driver._estimate_tokens("a" * 400), 100)

    def test_cjk_is_one_point_five_chars_per_token(self):
        self.assertEqual(self.driver._estimate_tokens("汉" * 150), 100)

    def test_mixed_rounds_up_each_part(self):
        # 2 ASCII → 1 token；1 CJK → ceil(1/1.5) = 1 token
        self.assertEqual(self.driver._estimate_tokens("ab汉"), 2)

    def test_messages_list_is_serialized_first(self):
        messages = [{"role": "user", "content": "汉" * 150}]
        # 正文 100 token + JSON 包装（ASCII 部分）若干 token
        self.assertGreaterEqual(self.driver._estimate_tokens(messages), 100)
        self.assertGreater(self.driver._estimate_tokens(messages), 100)

    def test_estimate_is_conservative_high(self):
        # 纯 CJK 真实 token 数约等于字符数（1:1 甚至更高），估算 1.5 字符/token 必不低估
        text = "科学研究需要严谨的实验设计与可复现的数据分析流程" * 10
        self.assertGreaterEqual(self.driver._estimate_tokens(text), len(text) // 2)


class TokenThresholdTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = {"connection_id": "connection", "profile_id": "profile", "model": "model",
                        "protocol": "responses", "base_url": "https://token.example/v1",
                        "thinking_style": "auto", "reasoning_effort": "auto", "thinking_budget": 4096,
                        "context_window": 400, "max_output_tokens": 8192, "api_key": "test-key"}
        for patch in (mock.patch.object(settings, "_PATH", self.root / "settings.json"),
                      mock.patch.object(llm, "available", return_value=True),
                      mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(self.profile)),
                      mock.patch.object(prompts, "agent_system", return_value="Test system."),
                      # These deliberately tiny windows measure compression, not
                      # the changing length of product-level role instructions.
                      mock.patch.dict(ROSTER, {"research": replace(
                          ROSTER["research"], prompt="科研智能体：根据工具回执汇总。")})):
            patch.start()
            self.addCleanup(patch.stop)

    def runtime(self):
        rt = AgentRuntime(offline=False, llm_enabled=True, session_root=self.root)
        rt._register("test.read", "read", "Read", lambda _: {"ok": True, "rows": 12},
                     rt._obj_schema({}))
        return rt

    def test_soft_threshold_triggers_compression_in_token_terms(self):
        runtime = self.runtime()
        filler = "填" * 300  # 300 CJK ≈ 200 token/条，窗口 400、软线 280
        steps = [
            response(text=filler, calls=[("c1", "test.read", {})]),
            response(text=filler, calls=[("c2", "test.read", {})]),
            response(text=filler, calls=[("c3", "test.read", {})]),
            response(text="汇总完成", calls=[]),
        ]
        calls = {"n": 0}

        def chat_impl(messages, **kwargs):
            if kwargs.get("node") == "context_compress":
                return {"content": "在线摘要：已完成早期消息折叠。", "tool_calls": [],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
            item = steps[calls["n"]]
            calls["n"] += 1
            return item

        with mock.patch.object(llm, "chat", side_effect=chat_impl):
            result = runtime.start("验证压缩")
        self.assertEqual(result["status"], "done")
        sid = result["session_id"]
        self.assertTrue(any(e["type"] == "context_compressed" for e in runtime.sessions.read(sid)),
                        "超过 0.7 软阈值（token 口径）应先触发压缩而非报错")
        self.assertEqual(result["context"]["measurement"], "heuristic_tokens")
        self.assertLessEqual(result["context"]["tokens"], result["context"]["limit_tokens"])

    def test_hard_limit_error_when_compression_cannot_shrink(self):
        # 尾部有 tool_calls 但切点 <= 2 时压缩无处可切（boundary 保护），
        # 超硬限须报 context_budget 而非继续请求模型
        self.profile["context_window"] = 100
        runtime = self.runtime()
        big = response("汉" * 300, calls=[("c1", "test.read", {})])  # 首条助手消息 ≈ 200+ token
        with mock.patch.object(llm, "chat", return_value=big) as chat:
            result = runtime.start("远超窗口")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "context_budget")
        self.assertEqual(chat.call_count, 1)

    def test_compression_still_works_with_streaming_token_guard(self):
        """token 口径压缩与流式兼容；载荷留足余量，避免旧字节口径的边界抖动。"""
        self.profile["protocol"] = "chat_completions"
        runtime = self.runtime()
        big_rows = {"ok": True, "rows": "数" * 120}  # 单条 tool 结果 ≈ 90 token
        runtime._register("test.read", "read", "Read", lambda _: big_rows, runtime._obj_schema({}))

        def tool_batch(cid):
            return [{"result": stream_result(tool="test.read", call_id=cid)}]

        steps = stream_batches(
            tool_batch("c1"), tool_batch("c2"), tool_batch("c3"),
            [{"delta": "汇总"}, {"delta": "完成"}, {"result": stream_result("汇总完成")}])

        def chat_impl(messages, **kwargs):
            if kwargs.get("node") == "context_compress":
                return {"content": "在线摘要：已折叠早期消息。", "tool_calls": [],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
            raise AssertionError("agent_step 不应走同步 chat")

        with mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(self.profile)), \
                mock.patch.object(llm, "chat_stream_agent", side_effect=steps), \
                mock.patch.object(llm, "chat", side_effect=chat_impl):
            result = runtime.start("压缩触发后仍流式")
        self.assertEqual(result["status"], "done")
        sid = result["session_id"]
        self.assertTrue(any(e["type"] == "context_compressed" for e in runtime.sessions.read(sid)))
        deltas = [e for e in runtime.sessions.read(sid) if e["type"] == "assistant_delta"]
        self.assertEqual([e["payload"]["delta"] for e in deltas], ["汇总", "完成"])

    def test_resume_restored_state_uses_token_limit(self):
        """旧状态缺 context_limit_tokens 时 _result 不报错（get 兜底）。"""
        runtime = self.runtime()
        driver = AdaptiveDriver(runtime)
        messages = [{"role": "system", "content": "Test system."},
                    {"role": "user", "content": "恢复我"}]
        state = driver_state(runtime, "tokresume1", messages)
        driver._save(state)
        with mock.patch.object(llm, "chat", return_value=response("继续完成")):
            result = runtime.continue_session("tokresume1")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["context"]["limit_tokens"], 400)


if __name__ == "__main__":
    unittest.main()
