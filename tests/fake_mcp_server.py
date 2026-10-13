# -*- coding: utf-8 -*-
"""假 MCP 服务器：stdio JSON-RPC 2.0 最小实现（测试专用）。

提供工具：echo(text) → "echo:<text>"
"""
import json
import sys


def main() -> None:
    # 真实 MCP 服务器同样必须固定 UTF-8 stdio，否则中文在 GBK 控制台炸编码
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in req:
            continue  # 通知：忽略
        method = req.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05",
                      "serverInfo": {"name": "fake", "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "回显文本",
                                 "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            args = (req.get("params") or {}).get("arguments") or {}
            result = {"content": [{"type": "text",
                                   "text": "echo:" + str(args.get("text", ""))}]}
        else:
            result = {}
        sys.stdout.write(json.dumps(
            {"jsonrpc": "2.0", "id": req["id"], "result": result},
            ensure_ascii=True) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
