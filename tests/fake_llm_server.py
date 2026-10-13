# -*- coding: utf-8 -*-
"""LLM 假端点：OpenAI Chat Completions 兼容的脚本化 HTTP 服务器（测试专用）。

用法：
    python tests/fake_llm_server.py --port 0   # 0 = 自动挑空闲端口，首行打印 LISTENING <port>

行为由请求消息正文中的场景标记（SCN_*）确定性触发，用于复现真实端点的
边界行为：HTTP 错误码、坏 JSON 正文、SSE 多 chunk、中途断流、截断、
tool_calls 分片（含跨 chunk 拆散的 JSON 参数）等。请求计数器经
GET /_state 暴露，供测试断言重试次数（如 chat_json 恰好发起两次请求）。

与 scripts/mock_llm_server.py 的区别：mock 面向演示（按提示语义作答），
本假端点面向测试（按标记触发脚本场景，行为逐字节确定）。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_TEXT = "假端点默认回复。"
GOOD_JSON_TEXT = '{"ok": true, "reply": "第二次请求输出合法 JSON"}'
BAD_JSON_TEXT = "这不是 JSON，第一次故意失败。"
STREAM_TEXT = "流式正文：格致 SciZhi 假端点逐字输出。"

# tool_calls 场景：两个工具的 arguments 都被故意拆到多个 chunk，
# 且第二个工具的 JSON 参数在键名中间断开（验证分片累积后再解析）。
_TOOL_CHUNKS = [
    {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_search", "function": {"name": "research.search", "arguments": ""}}]}}]},
    {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": "{\"query\": \"大模型智能体\", "}}]}}]},
    {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": "\"limit\": 5}"}}]}}]},
    {"choices": [{"delta": {"tool_calls": [
        {"index": 1, "id": "call_note", "function": {"name": "notebook.save", "arguments": "{\"ti"}}]}}]},
    {"choices": [{"delta": {"tool_calls": [
        {"index": 1, "function": {"arguments": "tle\": \"检索笔记\"}"}}]}}]},
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
     "usage": {"prompt_tokens": 32, "completion_tokens": 18}},
]

_HTTP_CODES = {"SCN_HTTP_429": 429, "SCN_HTTP_500": 500, "SCN_HTTP_503": 503}

_state_lock = threading.Lock()
_state: dict = {"requests": {}, "last_auth": ""}


def _scenario(text: str) -> str:
    """按标记匹配场景；标记之外的请求走默认应答。"""
    for marker in ("SCN_HTTP_429", "SCN_HTTP_500", "SCN_HTTP_503", "SCN_GARBAGE",
                   "SCN_BAD_JSON_ONCE", "SCN_BAD_JSON_ALWAYS", "SCN_STREAM_TEXT",
                   "SCN_STREAM_ABORT", "SCN_STREAM_LENGTH", "SCN_STREAM_EMPTY",
                   "SCN_STREAM_TOOLS", "SCN_TOOLS_JSON"):
        if marker in text:
            return marker
    return "default"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102 —— 静默
        pass

    def _send_json(self, obj, code: int = 200, content_type: str = "application/json; charset=utf-8") -> None:
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, chunks: list[dict], done: bool = True) -> None:
        """写 SSE 分片；done=False 表示中途断流（写完即关连接，无 [DONE]）。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        if done:
            self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        if not done:  # 模拟端点/网络中途断开：关连接且声明不再复用
            self.close_connection = True
            self.connection.close()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/_state"):
            with _state_lock:
                self._send_json(dict(_state))
        elif self.path.rstrip("/").endswith("/models"):
            self._send_json({"data": [{"id": "fake-strong"}, {"id": "fake-fast"}]})
        else:
            self._send_json({"error": {"message": "not found"}}, 404)

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": {"message": "bad request"}}, 400)
            return
        text = " ".join(str(m.get("content", "")) for m in req.get("messages") or [])
        scenario = _scenario(text)
        with _state_lock:
            _state["requests"][scenario] = _state["requests"].get(scenario, 0) + 1
            _state["last_auth"] = self.headers.get("Authorization", "")
        stream = bool(req.get("stream"))

        if scenario in _HTTP_CODES:
            self._send_json({"error": {"message": f"脚本化失败 {scenario}"}}, _HTTP_CODES[scenario])
            return
        if scenario == "SCN_GARBAGE":
            self._send_json(b"}{not-json", 200)
            return
        if scenario == "SCN_BAD_JSON_ALWAYS":
            self._send_json(self._chat_json_payload(BAD_JSON_TEXT))
            return
        if scenario == "SCN_BAD_JSON_ONCE":
            with _state_lock:
                seen = _state["requests"][scenario]
            content = BAD_JSON_TEXT if seen == 1 else GOOD_JSON_TEXT
            self._send_json(self._chat_json_payload(content))
            return
        if scenario == "SCN_TOOLS_JSON":
            self._send_json({"model": "fake-strong", "usage": {"prompt_tokens": 20, "completion_tokens": 9},
                             "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                                          "tool_calls": [{"id": "call_direct", "type": "function",
                                                          "function": {"name": "research.search",
                                                                       "arguments": "{\"query\": \"直出\"}"}}]}}]})
            return
        if stream:
            if scenario == "SCN_STREAM_TEXT":
                chunks = [{"choices": [{"delta": {"content": STREAM_TEXT[i:i + 3]}}]}
                          for i in range(0, len(STREAM_TEXT), 3)]
                chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 15, "completion_tokens": 21}})
                self._send_sse(chunks)
                return
            if scenario == "SCN_STREAM_ABORT":
                self._send_sse([{"choices": [{"delta": {"content": "断流前"}}]},
                                {"choices": [{"delta": {"content": "的两段"}}]}], done=False)
                return
            if scenario == "SCN_STREAM_LENGTH":
                self._send_sse([{"choices": [{"delta": {"content": "被截断"}, "finish_reason": "length"}],
                                 "usage": {"prompt_tokens": 8, "completion_tokens": 3}}])
                return
            if scenario == "SCN_STREAM_EMPTY":
                self._send_sse([])
                return
            if scenario == "SCN_STREAM_TOOLS":
                self._send_sse(_TOOL_CHUNKS)
                return
        self._send_json(self._chat_json_payload(DEFAULT_TEXT))

    @staticmethod
    def _chat_json_payload(content: str) -> dict:
        return {"model": "fake-strong",
                "choices": [{"finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"LISTENING {srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
