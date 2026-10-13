# -*- coding: utf-8 -*-
"""LLM 客户端端到端测试：对本机 Mock 端点跑通 chat / chat_json / chat_stream 与双轨路由。

Mock 端点在 scripts/mock_llm_server.py（子进程，自动挑端口），全程离线。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, llm, logging_util

_SERVER = Path(__file__).resolve().parent.parent / "scripts" / "mock_llm_server.py"


@unittest.skipUnless(Path(sys.executable).exists(), "无 python 解释器")
class MockLlmTest(unittest.TestCase):
    """整个类共用一个 Mock 端点子进程。"""

    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen(
            [sys.executable, str(_SERVER), "--port", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8")
        line = cls.proc.stdout.readline().strip()
        if not line.startswith("LISTENING"):
            raise RuntimeError(f"Mock 端点启动失败：{line!r}")
        cls.port = int(line.split()[1])

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        log_path = Path(self._tmp.name) / "model_calls.jsonl"
        self._patches = [
            mock.patch.object(config, "LLM_ENABLED", True, create=True),
            mock.patch.object(config, "LLM_BASE_URL", f"http://127.0.0.1:{self.port}/v1"),
            mock.patch.object(config, "LLM_API_KEY", "mock-key"),
            mock.patch.object(config, "LLM_MODEL", "mock-strong"),
            mock.patch.object(config, "LLM_MODEL_FAST", "mock-fast"),
            mock.patch.object(logging_util, "_MODEL_LOG", log_path),
        ]
        for p in self._patches:
            p.start()
        self.log_path = log_path

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    # ---- 基础 ----
    def test_available_and_models(self):
        self.assertTrue(llm.available())
        models = llm.list_models()
        self.assertIn("mock-strong", models)
        self.assertIn("mock-fast", models)

    def test_chat_logs_call_with_prompt_key(self):
        r = llm.chat([{"role": "user", "content": "你好"}], node="unittest",
                     prompt_key="unittest.v1")
        self.assertIn("Mock 端点已收到请求", r["content"])
        self.assertTrue(r["trace_id"])
        rows = [json.loads(x) for x in self.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["prompt_key"], "unittest.v1")
        self.assertEqual(rows[-1]["model_route"], "remote(mock-strong)")
        self.assertGreater(rows[-1]["prompt_tokens"], 0)

    # ---- 双轨路由 ----
    def test_tier_routing_echoes_model(self):
        fast = llm.chat([{"role": "user", "content": "x"}], tier="fast")["content"]
        strong = llm.chat([{"role": "user", "content": "x"}], tier="strong")["content"]
        self.assertTrue(fast.startswith("[mock-fast]"), fast[:40])
        self.assertTrue(strong.startswith("[mock-strong]"), strong[:40])

    # ---- JSON 协议与重试 ----
    def test_chat_json_parses(self):
        obj = llm.chat_json(
            [{"role": "user", "content": '请输出 JSON：{"reviews":[...]} RETRY_JSON'}],
            node="unittest", prompt_key="lit_review.v1")
        # 提示里带 RETRY_JSON：第一次必失败，第二次成功 → 正好验证 reflect 重试
        self.assertIsNotNone(obj)
        self.assertIn("reviews", obj)

    def test_chat_json_returns_none_after_two_failures(self):
        with mock.patch.object(llm.prompts, "extract_json", return_value=None):
            self.assertIsNone(llm.chat_json([{"role": "user", "content": "x"}]))

    def test_chat_json_real_prompt_templates(self):
        """真实的三个节点模板都能拿到合规 JSON（端到端契约）。"""
        key, msgs = llm.prompts.extract_one("主题", {"title": "Paper A", "abstract": "摘要"})
        obj = llm.chat_json(msgs, node="extract", prompt_key=key)
        self.assertIn("method", obj)
        key, msgs = llm.prompts.review_frame("主题", [{"title": "Paper A", "method": "m"}])
        obj = llm.chat_json(msgs, node="review_frame", prompt_key=key)
        self.assertTrue(obj["sections"])

    # ---- 流式 ----
    def test_chat_stream_yields_chunks(self):
        chunks = list(llm.chat_stream([{"role": "user", "content": "stream"}], node="unittest"))
        self.assertGreaterEqual(len(chunks), 2)
        self.assertIn("Mock 端点已收到请求", "".join(chunks))
        rows = [json.loads(x) for x in self.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1, "流式应只记一条调用日志")

    def test_chat_stream_consumes_json_when_endpoint_ignores_stream(self):
        """端点忽略 stream 时消费首份 JSON，避免再次请求导致重复计费。"""
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def raise_for_status(self): pass
            def iter_lines(self):
                # 端点无视 stream：回来的是整段 JSON，里面没有 data: 行
                yield json.dumps({"choices": [{"message": {"content": "[ignored] 整段"}}],
                                  "usage": {}})

        class FakePostResp:
            def raise_for_status(self): pass
            def json(self):
                return {"choices": [{"message": {"content": "[fallback] 回退正文"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 4}}

        class FakeCli:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def stream(self, *a, **kw): return FakeResp()
            def post(self, *a, **kw): raise AssertionError("不得重复发送已生成的请求")

        with mock.patch.object(llm.httpx, "Client", lambda **kw: FakeCli()):
            out = "".join(llm.chat_stream([{"role": "user", "content": "x"}], node="unittest"))
        self.assertEqual(out, "[ignored] 整段", "应消费首份非流式响应")
        rows = [json.loads(x) for x in self.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1, "回退路径也应记录一次调用")


if __name__ == "__main__":
    unittest.main()
