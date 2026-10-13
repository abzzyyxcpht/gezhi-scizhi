# -*- coding: utf-8 -*-
"""LLM 层集成测试：对 tests/fake_llm_server.py 假端点验证错误映射、
重试、流式分片与 tool_calls 累积。全程 127.0.0.1 回环，不触外网。
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import httpx
import pytest

from src import config, llm, logging_util

_SERVER = Path(__file__).resolve().parent / "fake_llm_server.py"


def _server_state(port: int) -> dict:
    with httpx.Client(timeout=10) as cli:
        return cli.get(f"http://127.0.0.1:{port}/_state").json()


@pytest.fixture(scope="module")
def port():
    """模块级假端点子进程；自动挑端口，退出时终止。"""
    proc = subprocess.Popen(
        [sys.executable, str(_SERVER), "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8")
    line = (proc.stdout.readline() or "").strip()
    if not line.startswith("LISTENING"):
        proc.kill()
        raise RuntimeError(f"假端点启动失败：{line!r}")
    yield int(line.split()[1])
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture()
def fake_llm(port, monkeypatch, tmp_path):
    """把 src.config 指向假端点，模型日志隔离到临时文件。"""
    monkeypatch.setattr(config, "LLM_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "fake-key")
    monkeypatch.setattr(config, "LLM_MODEL", "fake-strong")
    monkeypatch.setattr(config, "LLM_MODEL_FAST", "fake-fast")
    monkeypatch.setattr(config, "LLM_MODEL_STRONG", "fake-strong")
    log_path = tmp_path / "model_calls.jsonl"
    monkeypatch.setattr(logging_util, "_MODEL_LOG", log_path)
    return log_path


def _log_rows(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---- chat：HTTP 错误码与连接错误映射 ----

def test_chat_success(fake_llm):
    r = llm.chat([{"role": "user", "content": "你好"}], node="unittest", prompt_key="fake.v1")
    assert "假端点默认回复" in r["content"]
    assert r["usage"]["prompt_tokens"] == 11
    assert r["trace_id"]
    rows = _log_rows(fake_llm)
    assert rows[-1]["status"] == "success"
    assert rows[-1]["prompt_key"] == "fake.v1"


@pytest.mark.parametrize("marker,code", [("SCN_HTTP_429", 429), ("SCN_HTTP_500", 500), ("SCN_HTTP_503", 503)])
def test_chat_http_error_mapped(marker, code, fake_llm):
    with pytest.raises(llm.ModelRequestError, match=f"HTTP {code}"):
        llm.chat([{"role": "user", "content": marker}], node="unittest")
    assert _log_rows(fake_llm)[-1]["status"] == "error"


def test_chat_malformed_body_mapped(fake_llm):
    """端点返回非法 JSON 正文 → ModelResponseError（无法解析），且记 error 日志。"""
    with pytest.raises(llm.ModelResponseError, match="无法解析"):
        llm.chat([{"role": "user", "content": "SCN_GARBAGE"}], node="unittest")
    assert _log_rows(fake_llm)[-1]["status"] == "error"


def test_chat_connection_refused_is_safe_error(fake_llm, monkeypatch):
    """端点不可达（连接被拒）→ 映射为「连接失败或传输中断」，不泄露底层异常。"""
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:1/v1")
    with pytest.raises(llm.ModelRequestError, match="连接失败或传输中断"):
        llm.chat([{"role": "user", "content": "x"}], node="unittest")


def test_chat_recovers_after_transient_failure(port, fake_llm):
    """脚本化 5xx 后同一端点恢复成功（错误映射不污染后续请求）。"""
    with pytest.raises(llm.ModelRequestError):
        llm.chat([{"role": "user", "content": "SCN_HTTP_500"}], node="unittest")
    r = llm.chat([{"role": "user", "content": "恢复"}], node="unittest")
    assert "假端点默认回复" in r["content"]


# ---- chat_json：坏 JSON 重试路径 ----

def test_chat_json_retries_once_then_succeeds(port, fake_llm):
    obj = llm.chat_json([{"role": "user", "content": "SCN_BAD_JSON_ONCE 请输出 JSON"}],
                        node="unittest", prompt_key="fake.json")
    assert obj == {"ok": True, "reply": "第二次请求输出合法 JSON"}
    assert _server_state(port)["requests"]["SCN_BAD_JSON_ONCE"] == 2, "应恰好重试一次"


def test_chat_json_none_after_two_failures(port, fake_llm):
    obj = llm.chat_json([{"role": "user", "content": "SCN_BAD_JSON_ALWAYS"}], node="unittest")
    assert obj is None
    assert _server_state(port)["requests"]["SCN_BAD_JSON_ALWAYS"] == 2, "最多重试一次后放弃"


# ---- chat_stream：多 chunk / 断流 / 截断 / 空流 / 文本流拒绝工具 ----

def test_chat_stream_multi_chunk(fake_llm):
    chunks = list(llm.chat_stream([{"role": "user", "content": "SCN_STREAM_TEXT"}], node="unittest"))
    assert len(chunks) >= 5, "应按 3 字一片切成多 chunk"
    assert "".join(chunks) == "流式正文：格致 SciZhi 假端点逐字输出。"
    assert len(_log_rows(fake_llm)) == 1, "流式应只记一条调用日志"


def test_chat_stream_aborts_midway(fake_llm):
    """中途断流：已产出的 delta 保留，随后必须抛错，绝不当作成功。"""
    it = llm.chat_stream([{"role": "user", "content": "SCN_STREAM_ABORT"}], node="unittest")
    seen: list[str] = []
    with pytest.raises(llm.ModelResponseError, match="中断"):
        while True:
            seen.append(next(it))
    assert "".join(seen) == "断流前的两段"


def test_chat_stream_truncated_finish_reason(fake_llm):
    with pytest.raises(llm.ModelResponseError):
        list(llm.chat_stream([{"role": "user", "content": "SCN_STREAM_LENGTH"}], node="unittest"))


def test_chat_stream_empty_body_rejected(fake_llm):
    with pytest.raises(llm.ModelResponseError, match="没有返回正文"):
        list(llm.chat_stream([{"role": "user", "content": "SCN_STREAM_EMPTY"}], node="unittest"))


def test_chat_stream_rejects_tool_calls(fake_llm):
    """chat_stream 是纯文本接口：遇到 tool_calls 分片必须报错并指引 chat(tools=...)。"""
    with pytest.raises(llm.ModelResponseError, match="不执行工具调用"):
        list(llm.chat_stream([{"role": "user", "content": "SCN_STREAM_TOOLS"}], node="unittest"))


# ---- chat_stream_agent：tool_calls 分片累积 ----

def test_agent_stream_accumulates_tool_call_fragments(fake_llm):
    """跨 chunk 拆散的 id/name/arguments 必须累积后拼成与 chat(tools=...) 同构的结果。"""
    events = list(llm.chat_stream_agent([{"role": "user", "content": "SCN_STREAM_TOOLS"}],
                                        node="unittest", tools=[{"type": "function", "function": {
                                            "name": "research.search", "parameters": {"type": "object"}}}]))
    deltas = [e["delta"] for e in events if "delta" in e]
    results = [e["result"] for e in events if "result" in e]
    assert deltas == [], "本场景不应产出文本增量"
    assert len(results) == 1
    result = results[0]
    assert result["usage"]["completion_tokens"] == 18
    calls = {c["id"]: c for c in result["tool_calls"]}
    assert calls["call_search"]["name"] == "research.search"
    assert calls["call_search"]["arguments"] == {"query": "大模型智能体", "limit": 5}
    # 第二个工具的 JSON 参数在键名中间断开，累积后仍解析为完整对象
    assert calls["call_note"]["name"] == "notebook.save"
    assert calls["call_note"]["arguments"] == {"title": "检索笔记"}
    # assistant_message 与 chat() 同构，含 wire 形态 tool_calls 供续接
    wire = result["assistant_message"]["tool_calls"]
    assert [c["function"]["name"] for c in wire] == ["research.search", "notebook.save"]


def test_agent_stream_text_result_same_shape_as_chat(fake_llm):
    events = list(llm.chat_stream_agent([{"role": "user", "content": "SCN_STREAM_TEXT"}], node="unittest"))
    assert "".join(e["delta"] for e in events if "delta" in e) == "流式正文：格致 SciZhi 假端点逐字输出。"
    result = [e["result"] for e in events if "result" in e][0]
    assert result["content"] == "流式正文：格致 SciZhi 假端点逐字输出。"
    assert result["tool_calls"] == []


def test_chat_direct_tool_calls_parse(fake_llm):
    """非流式直出 tool_calls 的响应走 chat() 同一解析路径。"""
    r = llm.chat([{"role": "user", "content": "SCN_TOOLS_JSON"}],
                 tools=[{"type": "function", "function": {"name": "research.search",
                                                          "parameters": {"type": "object"}}}])
    assert r["tool_calls"][0]["name"] == "research.search"
    assert r["tool_calls"][0]["arguments"] == {"query": "直出"}


# ---- _safe_error 映射矩阵（纯单元，无需端点） ----

class SafeErrorMappingTest(unittest.TestCase):
    def _status_error(self, code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "http://127.0.0.1/v1/chat/completions")
        response = httpx.Response(code, request=request)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    def test_http_status_error(self):
        exc = llm._safe_error(self._status_error(429))
        self.assertIsInstance(exc, llm.ModelRequestError)
        self.assertIn("HTTP 429", str(exc))

    def test_timeout_error(self):
        exc = llm._safe_error(httpx.TimeoutException("slow"))
        self.assertIn("超时", str(exc))

    def test_connect_error(self):
        exc = llm._safe_error(httpx.ConnectError("refused"))
        self.assertIn("连接失败或传输中断", str(exc))

    def test_json_decode_becomes_response_error(self):
        exc = llm._safe_error(json.JSONDecodeError("bad", "x", 0))
        self.assertIsInstance(exc, llm.ModelResponseError)
        self.assertIn("无法解析", str(exc))

    def test_model_request_error_passes_through(self):
        original = llm.ModelRequestError("保持原样")
        self.assertIs(llm._safe_error(original), original)


if __name__ == "__main__":
    unittest.main()
