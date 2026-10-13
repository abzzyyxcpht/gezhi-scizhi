"""MCP stdio 与 2025 Streamable HTTP 工具客户端。

对接任意 MCP 服务器（官方协议子集）：initialize → notifications/initialized
→ tools/list → tools/call。配置 data/mcp_servers.json：

    {"servers": [
        {"name": "fs", "command": "npx", "autoload": true,
         "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
    ]}

在线服务器的每个工具动态注册为引擎动作 `mcp_<server>_<tool>`，
workflows/*.yaml 直接引用——这是通用智能体对接开放工具生态的层。
autoload=false 的服务器仅在 /mcp 与 doctor 里按需探测，不参与启动注册。

HTTP 范围：2025-03-26 / 06-18 / 11-25 initialize 会话，POST JSON/SSE，
Session-Id、协商版本及 DELETE。不实现 2024 HTTP+SSE、2026 无会话协议、
OAuth discovery、独立 GET 订阅与 SSE 断线续传；不重放可能执行过的工具。
规范：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
"""
from __future__ import annotations

import json
import codecs
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import httpx

from . import config

PROTOCOL_VERSION = "2024-11-05"
HTTP_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
HTTP_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
CONFIG_PATH = config.DATA_DIR / "mcp_servers.json"
_OWNED_ACTIONS: dict[str, Any] = {}
_REGISTRATION_LOCK = threading.RLock()
_CONFIG_LOCK = threading.RLock()


def _validate_server(value: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("MCP server 必须为对象")
    allowed = {"name", "command", "args", "env", "enabled", "autoload", "transport",
               "secret_env_keys", "url", "headers", "secret_header_keys"}
    if set(value) - allowed:
        raise ValueError("MCP server 含不支持的字段")
    result = {"transport": "stdio", "args": [], "env": {}, "enabled": True,
              "autoload": False, **(previous or {}), **value}
    result.pop("secret_env_keys", None)
    result.pop("secret_header_keys", None)
    name = result.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name):
        raise ValueError("MCP 名称须为 1–80 位字母、数字、点、下划线或横线")
    if not isinstance(result.get("transport"), str) or result["transport"] not in {"stdio", "streamable_http"}:
        raise ValueError("MCP transport 须为 stdio 或 streamable_http")
    for key in ("enabled", "autoload"):
        if type(result.get(key)) is not bool:
            raise ValueError(f"MCP {key} 必须为布尔值")
    if result["transport"] == "streamable_http":
        result["url"] = _validate_url(result.get("url"))
        result["headers"] = _validate_headers(result.get("headers", {}), (previous or {}).get("headers", {}))
        for key in ("command", "args", "env"):
            result.pop(key, None)
        return result
    command = result.get("command")
    if not isinstance(command, str) or not command.strip() or len(command) > 4096 or any(c in command for c in "\x00\r\n"):
        raise ValueError("MCP command 必须为有效可执行文件名或路径")
    result.pop("url", None)
    result.pop("headers", None)
    args = result.get("args")
    if not isinstance(args, list) or len(args) > 128:
        raise ValueError("MCP args 必须为参数数组")
    original = (previous or {}).get("args") or []
    args = [original[i] if arg is None and i < len(original) else arg for i, arg in enumerate(args)]
    if any(not isinstance(arg, str) or len(arg) > 16384 or "\x00" in arg for arg in args):
        raise ValueError("MCP args 每项必须为文本；null 只能保留已有脱敏参数")
    result["args"] = args
    env = result.get("env")
    if not isinstance(env, dict) or len(env) > 128:
        raise ValueError("MCP env 必须为环境变量对象")
    original_env = (previous or {}).get("env") or {}
    clean_env = {}
    for key, item in env.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key):
            raise ValueError("MCP env 环境变量名无效")
        if item is None and key in original_env:
            item = original_env[key]
        if not isinstance(item, str) or len(item) > 32768 or "\x00" in item:
            raise ValueError("MCP env 值必须为文本；null 只能保留已有密钥")
        clean_env[key] = item
    result["env"] = clean_env
    return result


def _validate_url(value: Any) -> str:
    # Disallow all query strings, rather than guessing which parameter contains
    # a credential. Errors deliberately never echo the supplied endpoint.
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise ValueError("MCP URL 必须为有效 HTTP(S) 地址")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                 and not parsed.password and "@" not in parsed.netloc
                 and not parsed.query and not parsed.fragment and "?" not in value and "#" not in value
                 and "\\" not in value and (parsed.port is None or 1 <= parsed.port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("MCP URL 须为 HTTP(S) 地址，禁止内嵌凭据、查询参数及片段；凭据请使用 headers")
    return value


def _validate_headers(value: Any, previous: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError("MCP headers 必须为最多 64 项的对象")
    reserved = {"accept", "content-type", "mcp-session-id", "mcp-protocol-version", "host",
                "content-length", "transfer-encoding", "connection", "upgrade", "proxy-authorization",
                "proxy-connection", "te", "trailer"}
    old = {key.lower(): item for key, item in previous.items()}
    seen = set()
    headers = {}
    for key, item in value.items():
        if (not isinstance(key, str) or len(key) > 128
                or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                or key.lower() in reserved or key.lower() in seen):
            raise ValueError("MCP header 名称无效、重复或属于协议控制头")
        seen.add(key.lower())
        if item is None:
            item = old.get(key.lower())
        if (not isinstance(item, str) or len(item) > 32768
                or any(not 32 <= ord(character) <= 126 for character in item)):
            raise ValueError("MCP header 值须为可打印 ASCII 文本；null 只能保留已有值")
        headers[key] = item
    return headers


def _read_servers() -> list[dict[str, Any]]:
    if not CONFIG_PATH.exists():
        return []
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("MCP 配置必须包含 servers 数组")
    servers = [_validate_server(s) for s in data["servers"]]
    if len({s["name"] for s in servers}) != len(servers):
        raise ValueError("MCP 服务器名称不能重复")
    return servers


def load_servers() -> list[dict[str, Any]]:
    try:
        with _CONFIG_LOCK:
            return _read_servers()
    except (ValueError, OSError, UnicodeError):
        return []


def _secret_values(server: dict[str, Any]) -> list[str]:
    values = [str(value) for value in (server.get("env") or {}).values() if value]
    for value in (server.get("headers") or {}).values():
        if isinstance(value, str) and value:
            values.append(value)
            if value.lower().startswith(("bearer ", "basic ")):
                values.append(value.split(" ", 1)[1])
    if server.get("url"):
        values.append(server["url"])
    args = server.get("args") or []
    for i, arg in enumerate(args):
        if re.search(r"(?i)(?:token|secret|password|api[-_]?key|authorization)", arg):
            if "=" in arg:
                values.append(arg.split("=", 1)[1])
            elif arg.startswith("-") and i + 1 < len(args):
                values.append(args[i + 1])
    return [value for value in values if value]


def _redact_error(error: Any, server: dict[str, Any]) -> str:
    text = str(error)
    for secret in sorted(_secret_values(server), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def public_server(server: dict[str, Any]) -> dict[str, Any]:
    result = dict(server)
    if server.get("transport") == "streamable_http":
        result["headers"] = {key: None for key in server.get("headers", {})}
        result["secret_header_keys"] = sorted(result["headers"])
        return result
    secrets = _secret_values(server)
    result["env"] = {key: None for key in (server.get("env") or {})}
    result["secret_env_keys"] = sorted(result["env"])
    result["args"] = [None if any(secret in arg for secret in secrets) else arg
                      for arg in server.get("args", [])]
    return result


def handle(req: dict[str, Any]) -> dict[str, Any]:
    """Host-requested CRUD. Reading/saving never probes or starts a process."""
    from . import settings
    from .state_store import write_json
    try:
        command = req.get("cmd")
        if command == "mcp_probe":
            return probe_named(str(req.get("name") or ""))
        with _CONFIG_LOCK:
            servers = _read_servers()
            if command == "mcp_save":
                incoming = req.get("server")
                previous = next((s for s in servers if isinstance(incoming, dict) and s["name"] == incoming.get("name")), None)
                server = _validate_server(incoming, previous)
                servers = [s for s in servers if s["name"] != server["name"]] + [server]
                write_json(CONFIG_PATH, {"servers": servers})
            elif command == "mcp_delete":
                name = req.get("name")
                if not any(s["name"] == name for s in servers):
                    raise ValueError("MCP 服务器不存在")
                servers = [s for s in servers if s["name"] != name]
                write_json(CONFIG_PATH, {"servers": servers})
            elif command != "mcp_config":
                raise ValueError("未知 MCP 配置命令")
            return {"ok": True, "servers": [public_server(s) for s in servers],
                    "mcp_enabled": settings.load().get("mcp_enabled", True)}
    except (ValueError, OSError, UnicodeError) as exc:
        return {"ok": False, "error": str(exc)}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()


def _result_text(result: dict[str, Any]) -> str:
    texts = [item["text"] for item in (result.get("content") or [])
             if isinstance(item, dict) and item.get("type") == "text"
             and isinstance(item.get("text"), str) and item["text"]]
    if texts:
        return "\n".join(texts)
    return json.dumps(result.get("structuredContent", result), ensure_ascii=False)


def _close_quietly(client: Any) -> None:
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001 —— cleanup must not turn a structured failure into an exception
            pass


class Client:
    """单会话客户端：spawn 子进程 → initialize → 调用 → close。"""

    def __new__(cls, server: dict[str, Any], timeout: float = 10.0):
        # Keep Client a class: integrations already subclass it and tests use
        # object.__new__(Client) to exercise the existing stdio parser.
        if cls is Client and isinstance(server, dict) and server.get("transport") == "streamable_http":
            return object.__new__(HttpClient)
        return object.__new__(cls)

    def __init__(self, server: dict[str, Any], timeout: float = 10.0):
        from . import settings
        if not settings.load().get("mcp_enabled", True) or not server.get("enabled", True):
            raise ValueError("MCP 服务器已停用")
        server = _validate_server(server)
        self.server = server
        self.timeout = timeout
        env = {**os.environ, **(server.get("env") or {})}
        self._proc = subprocess.Popen(
            [server["command"], *server.get("args", [])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", env=env)
        self._id = 0
        self._lock = threading.Lock()

    def _readline(self, timeout: float | None = None) -> str:
        """带超时的单行读取（Windows 管道不支持 select，用读线程实现）。"""
        box: dict[str, str] = {}

        def reader() -> None:
            try:
                assert self._proc.stdout
                box["line"] = self._proc.stdout.readline()
            except Exception:  # noqa: BLE001
                box["line"] = ""

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(self.timeout if timeout is None else max(0.0, timeout))
        if "line" not in box:
            raise TimeoutError(f"MCP 服务器 {self.server['name']} 响应超时")
        return box["line"]

    def _request(self, method: str, params: dict | None = None) -> Any:
        with self._lock:
            self._id += 1
            rid = self._id
            req: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                req["params"] = params
            assert self._proc.stdin
            self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"MCP 服务器 {self.server['name']} 响应超时")
                raw_line = self._readline(remaining)
                if not raw_line:
                    raise RuntimeError(f"MCP 服务器 {self.server['name']} 已关闭输出")
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict) or msg.get("id") != rid:
                    continue  # 通知/服务端请求：忽略
                if "error" in msg:
                    raise RuntimeError(f"MCP 错误：{msg['error']}")
                return msg.get("result", {})

    def initialize(self) -> dict[str, Any]:
        res = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "gezhi", "version": "0.2.0"}})
        self.protocol_version = res.get("protocolVersion", PROTOCOL_VERSION) if isinstance(res, dict) else PROTOCOL_VERSION
        try:
            assert self._proc.stdin
            self._proc.stdin.write(json.dumps(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            self._proc.stdin.flush()
        except Exception:  # noqa: BLE001
            pass
        return res

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        if not isinstance(result, dict) or not isinstance(result.get("tools", []), list):
            raise RuntimeError("MCP tools/list 返回格式错误")
        return [tool for tool in result.get("tools", [])
                if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"]]

    def call_tool_result(self, name: str, arguments: dict | None = None) -> dict[str, Any]:
        """保留 MCP 原始结果字段；JSON-RPC 错误仍由 _request 抛出。"""
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError("MCP arguments 必须为对象")
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            raise RuntimeError("MCP tools/call 返回格式错误：需要对象")
        if "isError" in result and not isinstance(result["isError"], bool):
            raise RuntimeError("MCP isError 必须为布尔值")
        if "content" in result and not isinstance(result["content"], list):
            raise RuntimeError("MCP content 必须为数组")
        if "structuredContent" in result and not isinstance(result["structuredContent"], dict):
            raise RuntimeError("MCP structuredContent 必须为对象")
        return result

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        result = self.call_tool_result(name, arguments)
        text = _result_text(result)
        if result.get("isError") is True:
            raise RuntimeError(f"MCP 工具 {name} 执行失败：{text}")
        return text

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self._proc.kill()
            self._proc.wait(timeout=3)
        finally:
            if self._proc.stdout:
                self._proc.stdout.close()


class _HttpSessionExpired(RuntimeError):
    pass


class HttpClient(Client):
    """2025 Streamable HTTP, with bounded POST responses and no redirects.

The public construction entry remains Client(server). The client advertises no
sampling/elicitation/roots capabilities. Server pings are answered; unsupported
server requests get -32601. Tool calls are never automatically replayed.
"""

    def __init__(self, server: dict[str, Any], timeout: float = 10.0):
        from . import settings
        if not settings.load().get("mcp_enabled", True) or not server.get("enabled", True):
            raise ValueError("MCP 服务器已停用")
        self.server = _validate_server(server)
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("MCP timeout 必须为正数")
        self._id = 0
        self._lock = threading.RLock()
        self._session_id = ""
        self.protocol_version = ""
        self._initialized = False
        self._closed = False
        self._initialization: dict[str, Any] = {}
        # Keep bridge startup and stdio-only users free of the HTTP import cost.
        import httpx
        self._client = httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False)

    def _headers(self) -> dict[str, str]:
        headers = {**self.server.get("headers", {}), "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self.protocol_version:
            headers["Mcp-Protocol-Version"] = self.protocol_version
        return headers

    def _chunks(self, response: httpx.Response, deadline: float):
        received = 0
        for chunk in response.iter_bytes():
            received += len(chunk)
            if received > HTTP_MAX_RESPONSE_BYTES:
                raise RuntimeError("MCP HTTP 响应超过 8 MiB 上限")
            if time.monotonic() > deadline:
                raise TimeoutError("MCP HTTP 请求超过总等待上限")
            yield chunk

    @staticmethod
    def _decode_message(raw: str | bytes) -> dict[str, Any]:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8-sig")
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RuntimeError("MCP HTTP 响应包含无效 JSON") from None
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise RuntimeError("MCP HTTP 响应必须为单个 JSON-RPC 2.0 对象")
        return value

    def _sse_messages(self, response: httpx.Response, deadline: float):
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        buffer = ""
        data: list[str] = []
        first_line = True
        for chunk in self._chunks(response, deadline):
            buffer += decoder.decode(chunk)
            while True:
                newline = re.search(r"\r\n|\r|\n", buffer)
                if newline is None or (newline.group() == "\r" and newline.end() == len(buffer)):
                    break
                line, buffer = buffer[:newline.start()], buffer[newline.end():]
                if first_line:
                    line = line.removeprefix("\ufeff")
                    first_line = False
                if not line:
                    payload = "\n".join(data)
                    data = []
                    # Empty priming events do not contain an RPC message.
                    if payload.strip():
                        yield self._decode_message(payload)
                elif line.startswith("data:"):
                    value = line[5:]
                    data.append(value[1:] if value.startswith(" ") else value)
                elif line == "data":
                    data.append("")
                # Comments, event/id/retry fields are not tool results. Resume
                # is deliberately unsupported; EOF before a result is an error.
        buffer += decoder.decode(b"", final=True)
        # A trailing CR was held back in case the next chunk started with LF.
        # At EOF it is a complete line ending; a lone CR here is a blank line.
        if buffer == "\r" and "\n".join(data).strip():
            yield self._decode_message("\n".join(data))

    def _rpc_result(self, message: dict[str, Any], request_id: int) -> tuple[bool, Any]:
        if "method" in message:
            if "id" in message:
                rid = message["id"]
                if type(rid) not in (str, int):
                    raise RuntimeError("MCP HTTP 服务端请求 ID 无效")
                reply = {"jsonrpc": "2.0", "id": rid}
                if message["method"] == "ping":
                    reply["result"] = {}
                else:
                    reply["error"] = {"code": -32601, "message": "Client capability not supported"}
                self._send(reply, notification=True)
            return False, None
        if type(message.get("id")) is not type(request_id) or message.get("id") != request_id:
            raise RuntimeError("MCP HTTP 响应 ID 与请求不匹配")
        if ("result" in message) == ("error" in message):
            raise RuntimeError("MCP HTTP 响应须且仅须包含 result 或 error")
        if "error" in message:
            error = message["error"]
            code = error.get("code") if isinstance(error, dict) else None
            suffix = f"（code={code}）" if type(code) is int else ""
            # Remote error bodies may echo credentials or signed URLs. Keep
            # only the machine-readable code, never message/data/headers.
            raise RuntimeError("MCP HTTP JSON-RPC 错误" + suffix)
        return True, message["result"]

    def _send(self, payload: dict[str, Any], *, notification: bool = False) -> Any:
        import httpx
        if self._closed:
            raise RuntimeError("MCP HTTP 客户端已关闭")
        deadline = time.monotonic() + self.timeout
        try:
            with self._client.stream("POST", self.server["url"], headers=self._headers(),
                                     json=payload, follow_redirects=False) as response:
                if response.status_code == 404 and self._session_id:
                    raise _HttpSessionExpired("MCP HTTP 会话已失效")
                if not 200 <= response.status_code < 300:
                    raise RuntimeError(f"MCP HTTP 请求失败（HTTP {response.status_code}）")
                if notification:
                    if response.status_code != 202:
                        raise RuntimeError("MCP HTTP 通知须返回 202 Accepted")
                    return None
                if payload.get("method") == "initialize":
                    session_id = response.headers.get("Mcp-Session-Id", "")
                    if session_id and (len(session_id) > 4096 or any(not 33 <= ord(c) <= 126 for c in session_id)):
                        raise RuntimeError("MCP HTTP 会话标识格式无效")
                    self._session_id = session_id
                media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if media_type == "application/json":
                    message = self._decode_message(b"".join(self._chunks(response, deadline)))
                    found, result = self._rpc_result(message, payload["id"])
                    if found:
                        return result
                elif media_type == "text/event-stream":
                    for message in self._sse_messages(response, deadline):
                        found, result = self._rpc_result(message, payload["id"])
                        if found:
                            return result
                else:
                    raise RuntimeError("MCP HTTP 响应须为 application/json 或 text/event-stream")
                raise RuntimeError("MCP HTTP 响应结束前未收到请求结果；原请求未重试")
        except httpx.HTTPError as exc:
            # httpx exceptions can include full URLs and request headers.
            raise RuntimeError(f"MCP HTTP 连接失败（{type(exc).__name__}）；原请求未重试") from None
        except UnicodeError:
            raise RuntimeError("MCP HTTP 响应不是有效 UTF-8") from None

    def _request(self, method: str, params: dict | None = None) -> Any:
        with self._lock:
            if method != "initialize" and not self._initialized:
                raise RuntimeError("MCP HTTP 客户端尚未初始化")
            self._id += 1
            payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
            if params is not None:
                payload["params"] = params
            try:
                return self._send(payload)
            except _HttpSessionExpired:
                self._session_id, self.protocol_version, self._initialized = "", "", False
                # Start a fresh session as required by the 2025 transport. Do
                # not replay the RPC: it may have had externally visible effects.
                if method != "initialize":
                    self.initialize()
                raise RuntimeError("MCP HTTP 会话失效并已重新初始化；原请求未重试") from None

    def initialize(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("MCP HTTP 客户端已关闭")
            if self._initialized:
                return dict(self._initialization)
            result = self._request("initialize", {
                "protocolVersion": HTTP_PROTOCOL_VERSIONS[0], "capabilities": {},
                "clientInfo": {"name": "gezhi", "version": "0.2.0"}})
            if not isinstance(result, dict) or result.get("protocolVersion") not in HTTP_PROTOCOL_VERSIONS:
                raise RuntimeError("MCP HTTP 协商版本不支持；支持 2025-03-26、2025-06-18、2025-11-25")
            self.protocol_version = result["protocolVersion"]
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"}, notification=True)
            self._initialization = result
            self._initialized = True
            return dict(result)

    def call_tool_result(self, name: str, arguments: dict | None = None) -> dict[str, Any]:
        result = super().call_tool_result(name, arguments)
        if result.get("isError") is True:
            def redact(value):
                if isinstance(value, str):
                    return _redact_error(value, self.server)
                if isinstance(value, dict):
                    return {key: redact(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [redact(item) for item in value]
                return value
            result = redact(result)
        return result

    def close(self) -> None:
        import httpx
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._session_id:
                    with self._client.stream("DELETE", self.server["url"], headers=self._headers(),
                                             follow_redirects=False) as response:
                        # 405 means the server does not offer explicit session
                        # termination; 404 means it is already gone.
                        if not (200 <= response.status_code < 300 or response.status_code in {404, 405}):
                            raise RuntimeError(f"MCP HTTP 会话关闭失败（HTTP {response.status_code}）")
            except httpx.HTTPError as exc:
                raise RuntimeError(f"MCP HTTP 会话关闭失败（{type(exc).__name__}）") from None
            finally:
                self._session_id = ""
                self._client.close()


def call_named(server_name: str, tool: str,
               arguments: dict[str, Any] | None = None,
               timeout: float = 10.0) -> dict[str, Any]:
    """按服务器名调用一个 MCP 工具；未配置或失败时返回 ok=False，不抛错。"""
    from . import settings
    base = {"ok": False, "server": server_name, "tool": tool, "text": "",
            "structured_content": None, "content": []}
    if not settings.load().get("mcp_enabled", True):
        return {**base, "error": "MCP 工具已在设置中停用"}
    servers = {str(s.get("name")): s for s in load_servers()}
    if server_name not in servers:
        known = "、".join(servers) or "（空）"
        return {**base, "error": f"未知 MCP 服务器：{server_name}。已配置：{known}"}
    if not servers[server_name].get("enabled", True):
        return {**base, "error": "MCP 服务器已停用"}
    if not isinstance(tool, str) or not tool.strip() or not isinstance(arguments if arguments is not None else {}, dict):
        return {**base, "error": "MCP tool 须为非空文本，arguments 须为对象"}
    cli = None
    try:
        cli = Client(servers[server_name], timeout=timeout)
        cli.initialize()
        result = cli.call_tool_result(tool, arguments)
        text = _result_text(result)
        output = {**base, "ok": result.get("isError") is not True, "text": text,
                  "structured_content": result.get("structuredContent"),
                  "content": result.get("content", []), "isError": result.get("isError", False)}
        if not output["ok"]:
            output["error"] = f"MCP 工具 {tool} 执行失败：{text}"
        return output
    except Exception as exc:  # noqa: BLE001
        return {**base, "error": _redact_error(f"{type(exc).__name__}: {exc}", servers[server_name])}
    finally:
        _close_quietly(cli)


def probe_named(name: str, timeout: float = 8.0) -> dict[str, Any]:
    """Explicit discovery request. Disabled servers never reach Client."""
    from . import settings
    base = {"name": name, "ok": False, "tools": [], "error": ""}
    server = next((s for s in load_servers() if s["name"] == name), None)
    if server is None:
        return {**base, "error": "MCP 服务器不存在"}
    if not settings.load().get("mcp_enabled", True) or not server.get("enabled", True):
        return {**base, "error": "MCP 服务器已停用"}
    if (server.get("transport", "stdio") == "stdio"
            and not shutil.which(server["command"]) and not Path(server["command"]).is_file()):
        return {**base, "error": f"命令不存在：{server['command']}"}
    client = None
    try:
        client = Client(server, timeout=timeout)
        client.initialize()
        tools = client.list_tools()
        version = getattr(client, "protocol_version", PROTOCOL_VERSION)
        return {**base, "ok": True, "transport": server.get("transport", "stdio"),
                "tool_count": len(tools), "protocol_version": version if isinstance(version, str) else PROTOCOL_VERSION,
                "tools": [{
            "name": t.get("name", ""), "description": t.get("description", ""),
            "inputSchema": t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {"type": "object"},
            "annotations": t.get("annotations") if isinstance(t.get("annotations"), dict) else {}}
            for t in tools]}
    except Exception as exc:  # noqa: BLE001
        return {**base, "error": _redact_error(f"{type(exc).__name__}: {exc}", server)}
    finally:
        _close_quietly(client)


def status_all(timeout: float = 8.0, *, autoload_only: bool = False) -> list[dict[str, Any]]:
    """逐台探测：在线返回工具清单，离线返回原因（永不抛错）。"""
    return [probe_named(server["name"], timeout=timeout) for server in load_servers()
            if not autoload_only or server.get("autoload", False)]


def register_actions() -> int:
    """把 autoload 在线服务器的工具注册为引擎动作 mcp_<server>_<tool>。"""
    with _REGISTRATION_LOCK:
        return _register_actions()


def _register_actions() -> int:
    from .orchestrator.engine import ACTIONS
    from . import settings
    for name, action in _OWNED_ACTIONS.items():
        if ACTIONS.get(name) is action:
            ACTIONS.pop(name, None)
    _OWNED_ACTIONS.clear()
    if not settings.load().get("mcp_enabled", True):
        return 0
    servers = {s["name"]: s for s in load_servers()}
    n = 0
    for st in status_all(timeout=6.0, autoload_only=True):
        if not st["ok"]:
            continue
        server = servers.get(st["name"])
        if not server:
            continue
        if not server.get("autoload", False) or not server.get("enabled", True):
            continue
        sname = _safe(st["name"])
        for tool in st["tools"]:
            tname = _safe(tool["name"])
            aname = f"mcp_{sname}_{tname}"
            if aname in ACTIONS:
                continue

            def _make(server=server, tool=tool, aname=aname):
                def _action(ctx, params):
                    live = next((s for s in load_servers() if s["name"] == server["name"]), None)
                    if (not settings.load().get("mcp_enabled", True) or live is None or not live.get("enabled", True)):
                        raise RuntimeError("MCP 工具已在设置中停用")
                    cli = Client(live, timeout=config.LLM_TIMEOUT)
                    try:
                        cli.initialize()
                        result = cli.call_tool_result(tool["name"], params.get("arguments"))
                        text = _result_text(result)
                        if result.get("isError") is True:
                            raise RuntimeError(f"MCP 工具 {tool['name']} 执行失败：{text}")
                        ctx.emit("node", node=aname,
                                 message=f"MCP {server['name']}.{tool['name']} 调用完成")
                        return {"text": text, "content": result.get("content", []),
                                "structured_content": result.get("structuredContent")}
                    finally:
                        _close_quietly(cli)
                return _action

            ACTIONS[aname] = _make()
            _OWNED_ACTIONS[aname] = ACTIONS[aname]
            n += 1
    return n
