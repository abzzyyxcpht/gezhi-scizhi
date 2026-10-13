"""OpenAI 兼容的本地 Mock 端点（离线验证 LLM 链路 / 生成 model_calls.jsonl 样例）。

用法：
    python scripts/mock_llm_server.py --port 0        # 0 = 自动挑空闲端口，首行打印 LISTENING <port>

行为：
- GET  /v1/models            → mock-strong / mock-fast 两个模型
- POST /v1/chat/completions  → 按提示内容返回对应 JSON（抽取/综述/相关性/计划）；
                               响应正文以 `[模型名]` 开头，便于断言双轨路由；
                               提示含 RETRY_JSON 时**第一次**返回坏 JSON（测重试路径）；
                               stream=true 时走 SSE 分片。

仅供离线演示与测试，不参与生产链路。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = ["mock-strong", "mock-fast"]
_seen: dict[str, int] = {}
_lock = threading.Lock()


def _titles(text: str) -> list[str]:
    """Echo titles from v2 JSON materials, with legacy prompt compatibility."""
    if "外部材料（JSON 数据" in text:
        try:
            payload = text.split("外部材料（JSON 数据", 1)[1].split("\n", 1)[1].lstrip()
            value, _ = json.JSONDecoder().raw_decode(payload)
            records = value.get("records", []) if isinstance(value, dict) else value
            if isinstance(records, list):
                return [r["title"] for r in records if isinstance(r, dict) and isinstance(r.get("title"), str)]
        except (ValueError, IndexError):
            pass
    return re.findall(r"- 《(.+?)》", text)


def _respond(messages: list[dict]) -> str:
    """按提示特征返回结构化内容；返回正文带模型名前缀。"""
    text = " ".join(str(m.get("content", "")) for m in messages)
    # Classify the requested output, never field names inside source material.
    instruction_text = "\n".join(str(m.get("content", "")).split("外部材料（JSON 数据", 1)[0]
                                  for m in messages if m.get("role") == "user")
    schema = re.search(r'请输出\s+JSON[：:]\s*\{\s*"([^"]+)"', instruction_text)
    kind = schema.group(1) if schema else ""
    if "RETRY_JSON" in text:
        with _lock:
            n = _seen.get("retry", 0) + 1
            _seen["retry"] = n
        if n == 1:
            return "这不是 JSON，第一次故意失败。"
    if kind == "reviews":
        return json.dumps({"reviews": [
            {"idx": 0, "keep": True, "reason": "主题词高度重合，保留"},
            {"idx": 1, "keep": False, "reason": "仅方法名相关，主题不符"},
        ]}, ensure_ascii=False)
    if kind == "method":
        return json.dumps({"method": "提出基于检索增强的框架",
                           "innovation": "首次在长文档场景验证",
                           "conclusion": "检索增强带来稳定增益",
                           "limitation": None}, ensure_ascii=False)
    if kind == "plan":
        return json.dumps({"plan": ["按方法范式分节", "横向比较创新点"]}, ensure_ascii=False)
    if kind == "sections":
        # 从提示里的清单回显真实题名（否则会被"支撑文献逐字回池校验"清空）
        titles = _titles(text)
        def pick(a, b):
            return titles[a:b]
        return json.dumps({"sections": [
            {"title": "1 研究背景与问题", "points": "梳理领域脉络与核心问题",
             "refs": pick(0, 2)},
            {"title": "2 方法分类与比较", "points": "按方法范式归类并横向比较",
             "refs": pick(2, 4)},
            {"title": "3 研究空白与方向", "points": "归纳尚未解决的问题",
             "refs": pick(4, 6)},
        ]}, ensure_ascii=False)
    return "Mock 端点已收到请求。"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102 —— 静默
        pass

    def _send_json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send_json({"data": [{"id": m} for m in MODELS]})
        else:
            self._send_json({"error": {"message": "not found"}}, 404)

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": {"message": "bad request"}}, 400)
            return
        model = req.get("model") or MODELS[0]
        content = f"[{model}] " + _respond(req.get("messages") or [])
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for i in range(0, len(content), 10):
                chunk = {"choices": [{"delta": {"content": content[i:i + 10]}}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self._send_json({
                "model": model,
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 128, "completion_tokens": 64},
            })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = srv.server_address[1]
    print(f"LISTENING {port}", flush=True)   # 测试脚本据此拿到端口
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
