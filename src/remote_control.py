"""Explicitly enabled, token-protected phone controls for the existing engine.

This module never starts an engine, evaluates a shell command, or serves a
filesystem path supplied by a remote client. The application owns its lifetime.
"""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import secrets
import socket
import tempfile
import threading
from typing import Any, Callable
from urllib.parse import urlsplit


MAX_BODY_BYTES = 64 * 1024
MAX_EXPORT_BYTES = 8 * 1024 * 1024
_PAGE = Path(__file__).resolve().parent.parent / "assets" / "remote-control.html"
_state_lock = threading.RLock()
_dispatch_lock = threading.Lock()
_server: _ControlServer | None = None
_thread: threading.Thread | None = None
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")

# Field allowlists matter as much as command allowlists: future dispatcher
# additions must not silently grant the phone filesystem or shell access.
_FIELDS: dict[str, set[str]] = {
    "list": set(), "workbench_status": set(), "workflows": set(),
    "sessions": set(), "loop_list": set(),
    "nodes": {"workflow_id"}, "usage": set(), "plugin_commands": set(),
    "poll": {"task_id", "since", "art_rev", "full"},
    "pause": {"task_id"}, "resume": {"task_id"}, "cancel": {"task_id"},
    "workflow_run": {"workflow_id", "topic", "offline"},
    "loop_start": {"workflow_id", "topic", "offline", "interval_seconds", "max_iterations"},
    "loop_stop": {"loop_id"}, "loop_resume": {"loop_id"},
    "session_snapshot": {"session_id"},
    "session_activity": {"session_id", "limit"},
    "model_control_catalog": {"stage"},
    'session_history': {'session_id', 'query', 'page', 'page_size', 'snapshot'},
    'session_history_message': {'session_id', 'message_id', 'generation', 'offset', 'limit'},
    'session_tool_outputs': {'session_id', 'offset', 'limit'},
    'session_tool_output_read': {'session_id', 'output_id', 'offset', 'limit'},
    'session_tool_output_search': {'session_id', 'output_id', 'query', 'offset', 'limit'},
    "product_readiness": set(), "product_demo": set(),
    "evidence_brief": {"task_id"},
    "steer_status": {"session_id"},
    "steer_add": {"session_id", "expected_turn_id", "steer_request_id", "request"},
    'review_start': {'record_id', 'expected_version', 'review_request_id', 'mode'},
    'review_list': {'record_id'}, 'review_get': {'job_id'}, 'review_cancel': {'job_id'},
    'review_resume': {'job_id'}, 'review_accept': {'job_id'}, 'review_dismiss': {'job_id'},
    'review_events': {'job_id', 'cursor', 'limit', 'tail'},
    "queue_list": {"session_id"}, "queue_add": {"session_id", "request", "queue_request_id"},
    "queue_pause": {"session_id"}, "queue_resume": {"session_id"},
    "queue_remove": {"session_id", "ticket_id"}, "queue_discard": {"session_id", "ticket_id"},
    "queue_reorder": {"session_id", "ordered_ids"},
    "session_status": {"session_id"}, "session_compact": {"session_id"},
    "session_events": {"session_id", "cursor", "limit", "tail"},
    "session_export": {"session_id", "format"},
    "agent_approve": {"session_id", "tool_call_id", "scope"},
    "agent_deny": {"session_id", "tool_call_id"},
    "agent_continue": {"session_id"}, "agent_cancel": {"session_id"},
    "model_capabilities": set(), "model_registry": set(), "research_audit": {"task_id"},
    # 项目工作室只读列表命令：逐项核对过 handler，均不读文件内容、不产生副作用。
    "project_list": set(), "project_files": {"project_id", "path"},
    "chat_sessions": {"project_id", "include_archived", "query"},
    "notebook_list": {"kind", "status", "task_id"},
    "todo_list": {"project_id", "session_id"},
    "schedule_list": {"project_id", "session_id"},
    "hook_list": set(), "custom_commands": {"query"},
}
_READ_ONLY = frozenset({"list", "workbench_status", "workflows", "sessions", "loop_list",
                        "session_activity", "model_control_catalog",
                        "poll", "nodes", "usage", "plugin_commands", "session_snapshot", "session_status", "session_events",
                        "model_capabilities", "model_registry", "research_audit", "product_readiness", "evidence_brief", "queue_list", "steer_status",
                        'review_list', 'review_get', 'review_events',
                        'session_history', 'session_history_message',
                        'session_tool_outputs', 'session_tool_output_read', 'session_tool_output_search',
                        "project_list", "project_files", "chat_sessions", "notebook_list",
                        "todo_list", "schedule_list", "hook_list", "custom_commands"})
# These controls serialize their own state per session and must remain usable
# while a remote approval/continue owns the long-running dispatch slot.
_CONCURRENT_CONTROLS = frozenset({'agent_cancel', 'steer_add', 'queue_add', 'queue_pause', 'queue_resume',
                                'review_start', 'review_cancel', 'review_resume', 'review_accept', 'review_dismiss',
                                'queue_remove', 'queue_discard', 'queue_reorder'})
_REQUIRED = {
    "session_activity": {"session_id"},
    'session_history': {'session_id'},
    'session_history_message': {'session_id', 'message_id', 'generation'},
    'session_tool_outputs': {'session_id'},
    'session_tool_output_read': {'session_id', 'output_id'},
    'session_tool_output_search': {'session_id', 'output_id', 'query'},
    'review_start': {'record_id', 'expected_version', 'review_request_id', 'mode'},
    **{name: {'job_id'} for name in ('review_get', 'review_cancel', 'review_resume', 'review_accept', 'review_dismiss', 'review_events')},
    "steer_status": {"session_id"},
    "steer_add": {"session_id", "expected_turn_id", "steer_request_id", "request"},
    "queue_list": {"session_id"}, "queue_add": {"session_id", "request", "queue_request_id"},
    "queue_pause": {"session_id"}, "queue_resume": {"session_id"},
    "queue_remove": {"session_id", "ticket_id"}, "queue_discard": {"session_id", "ticket_id"},
    "queue_reorder": {"session_id", "ordered_ids"},
    "poll": {"task_id"}, "pause": {"task_id"}, "resume": {"task_id"},
    "cancel": {"task_id"}, "loop_stop": {"loop_id"}, "loop_resume": {"loop_id"},
    "session_snapshot": {"session_id"}, "session_export": {"session_id"},
    "session_status": {"session_id"}, "session_compact": {"session_id"},
    "session_events": {"session_id"},
    "workflow_run": {"workflow_id", "topic"},
    "loop_start": {"workflow_id", "topic", "interval_seconds", "max_iterations"},
    "agent_approve": {"session_id", "tool_call_id"},
    "agent_deny": {"session_id", "tool_call_id"},
    "agent_continue": {"session_id"}, "agent_cancel": {"session_id"}, "research_audit": {"task_id"},
    "project_files": {"project_id"},
}


def _validated(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or not isinstance(request.get("cmd"), str):
        raise ValueError("请求必须包含命令名称")
    cmd = request["cmd"]
    if cmd not in _FIELDS:
        raise PermissionError("手机控制不支持此命令")
    if set(request) - {"cmd"} - _FIELDS[cmd]:
        raise ValueError("请求包含不支持的字段")
    if not _REQUIRED.get(cmd, set()).issubset(request):
        raise ValueError("请求缺少必要字段")
    if cmd == "model_control_catalog":
        from .model_registry import ROUTES
        if request.get("stage", "planning") not in ROUTES:
            raise ValueError("无效的科研阶段")
    if cmd == "session_activity" and (type(request.get("limit", 40)) is not int or not 1 <= request.get("limit", 40) <= 100):
        raise ValueError("活动条数须为 1–100")
    if cmd == 'review_start' and (not isinstance(request['mode'], str) or request['mode'] not in {'local', 'model'}
            or type(request['expected_version']) is not int or request['expected_version'] < 1):
        raise ValueError('复核需要有效记录版本与 local/model 方式')
    if cmd == 'steer_add' and (not isinstance(request['request'], str)
            or not request['request'].strip() or len(request['request']) > 8000):
        raise ValueError('补充要求须为 1–8000 字符')
    if cmd == 'queue_add' and (not isinstance(request['request'], str)
            or not request['request'].strip() or len(request['request']) > 32000):
        raise ValueError('队列请求应为1–32000字符的文本')
    if cmd == 'queue_reorder' and (not isinstance(request['ordered_ids'], list)
            or len(request['ordered_ids']) > 20
            or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in request['ordered_ids'])
            or len(set(request['ordered_ids'])) != len(request['ordered_ids'])):
        raise ValueError('队列顺序应为不重复的票据ID列表')
    for key, value in request.items():
        if key.endswith("_id") and key != 'message_id' and (not isinstance(value, str) or not _ID.fullmatch(value)):
            raise ValueError("任务或会话标识无效")
        if key in {"offline", "full"} and not isinstance(value, bool):
            raise ValueError("布尔参数无效")
        if key == "topic" and (not isinstance(value, str) or not value.strip() or len(value) > 8000):
            raise ValueError("请输入 1 至 8000 字的研究主题")
        if key == "since" and (type(value) is not int or value < 0):
            raise ValueError("事件游标无效")
        if key == 'cursor' and value is not None and (not isinstance(value, str) or len(value) > 2048):
            raise ValueError('事件游标无效')
        if key == 'limit' and (type(value) is not int or not 1 <= value <= (20000 if cmd == 'session_history_message'
                else 4000 if cmd == 'session_tool_output_read' else 500)):
            raise ValueError('事件分页大小须为 1–500')
        if key in {'message_id', 'page', 'page_size'} and (type(value) is not int or not 1 <= value <= (100 if key == 'page_size' else 10**9)):
            raise ValueError('历史页码、消息标识或分页大小无效')
        if key == 'offset' and (type(value) is not int or not 0 <= value <= 10**9):
            raise ValueError('消息文本偏移无效')
        if key == 'generation' and (not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value)):
            raise ValueError('历史版本标识无效')
        if key == 'snapshot' and value is not None and (not isinstance(value, str) or len(value) > 1024):
            raise ValueError('历史快照无效')
        if key == 'query' and cmd == 'session_history' and (not isinstance(value, str) or len(value) > 1000):
            raise ValueError('历史搜索关键词过长或无效')
        if key == 'tail' and type(value) is not bool:
            raise ValueError('tail 必须为布尔值')
        if key == "art_rev" and (not isinstance(value, str) or len(value) > 160):
            raise ValueError("产物版本无效")
        if key == "interval_seconds" and (type(value) not in {int, float} or not 1 <= value <= 604800):
            raise ValueError("轮询间隔须介于 1 秒和 7 天")
        if key == "max_iterations" and (type(value) is not int or not 1 <= value <= 100):
            raise ValueError("轮次上限须介于 1 和 100")
        if key == "scope" and (not isinstance(value, str) or value not in {"once", "session"}):
            raise ValueError("审批范围无效")
        if key == "format" and (not isinstance(value, str) or value not in {"markdown", "md", "json"}):
            raise ValueError("仅支持 Markdown 和 JSON 导出")
    return dict(request)


def _export_session(dispatch: Callable, request: dict[str, Any]) -> dict[str, Any]:
    """Read only a file just generated in a fresh server-owned directory."""
    with tempfile.TemporaryDirectory(prefix="scizhi-phone-export-") as directory:
        export_root = Path(directory).resolve()
        forwarded = {**request, "dir": str(export_root)}
        result = dispatch(forwarded)
        if not result.get("ok"):
            return {"ok": False, "error": "会话导出失败，请在桌面查看详情"}
        candidate = result.get("path")
        if not isinstance(candidate, str):
            raise ValueError("导出未返回可下载文件")
        path = Path(candidate).resolve()
        if not path.is_relative_to(export_root) or not path.is_file():
            raise ValueError("导出文件不在本次授权目录内")
        if path.suffix.lower() not in {".md", ".json", ".txt"}:
            raise ValueError("导出文件格式不受支持")
        if path.stat().st_size > MAX_EXPORT_BYTES:
            raise ValueError("会话记录较大，请在桌面导出")
        content = path.read_text(encoding="utf-8")
        return {"ok": True, "session_id": request["session_id"],
                "filename": path.name, "content": content,
                "mime": "application/json" if path.suffix.lower() == ".json" else "text/markdown",
                "format": result.get("format", request.get("format", "markdown"))}


class _ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], dispatch: Callable):
        self.dispatch = dispatch
        self.token = secrets.token_urlsafe(32)
        self.accept_commands = True
        super().__init__(address, _Handler)

    def handle_error(self, request, client_address):
        # Never print request headers, bearer tokens, or dispatcher exceptions.
        pass


class _Handler(BaseHTTPRequestHandler):
    server: _ControlServer

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        pass

    def _send(self, status: int, payload: Any, content_type="application/json; charset=utf-8"):
        data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str):
        # Closing a POST with unread bytes can reset the TCP connection on
        # Windows before the JSON error reaches the caller. Drain only an
        # unambiguous, bounded body; never decode or dispatch rejected input.
        if self.command == 'POST' and not getattr(self, '_body_consumed', False):
            self._body_consumed = True
            lengths = self.headers.get_all('Content-Length', [])
            try:
                size = int(lengths[0]) if len(lengths) == 1 else 0
            except ValueError:
                size = 0
            if not self.headers.get('Transfer-Encoding') and 0 < size <= 1024 * 1024:
                remaining = size
                try:
                    while remaining:
                        chunk = self.rfile.read(min(remaining, 65536))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                except (OSError, TimeoutError):
                    pass
        self._send(status, {"ok": False, "error": message})

    def _authorized(self) -> bool:
        if not self.server.accept_commands:
            self._error(503, "远程控制已停止")
            return False
        authorization = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.token
        if not hmac.compare_digest(authorization.encode("utf-8"), expected.encode("utf-8")):
            self._error(401, "请输入桌面显示的访问令牌")
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                self._error(403, "不允许跨站访问")
                return False
            host = self.headers.get("Host", "")
            if (parsed.scheme != "http" or parsed.netloc.lower() != host.lower()
                    or parsed.path or parsed.query or parsed.fragment or parsed.username):
                self._error(403, "不允许跨站访问")
                return False
        return True

    def do_GET(self):
        if self.path == '/brand.svg':
            try:
                self._send(200, _PAGE.with_name('gezhi.svg').read_text(encoding='utf-8'), 'image/svg+xml')
            except OSError:
                self._error(404, '标识暂不可用')
            return
        if self.path == "/":
            try:
                self._send(200, _PAGE.read_text(encoding="utf-8"), "text/html; charset=utf-8")
            except OSError:
                self._error(503, "控制页面暂不可用")
            return
        if self.path != "/api/status":
            self._error(404, "页面不存在")
            return
        if not self._authorized():
            return
        try:
            result = self.server.dispatch({"cmd": "list"})
            self._send(200, {**result, "remote": server_status()})
        except Exception:
            self._error(500, "读取失败，请在桌面查看引擎状态")

    def do_POST(self):
        self._body_consumed = False
        if self.path != "/api/command":
            self._error(404, "接口不存在")
            return
        if not self._authorized():
            return
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            self._error(400, "请求长度无效")
            return
        try:
            size = int(self.headers["Content-Length"])
        except (TypeError, ValueError):
            self._error(400, "请求长度无效")
            return
        if size < 1 or size > MAX_BODY_BYTES:
            self._error(413 if size > MAX_BODY_BYTES else 400, "请求内容超过限制或为空")
            return
        try:
            data = self.rfile.read(size)
            self._body_consumed = True
            if len(data) != size:
                raise ValueError("请求内容不完整")
            request = _validated(json.loads(data))
            if request["cmd"] in _READ_ONLY or request["cmd"] in _CONCURRENT_CONTROLS:
                if not self.server.accept_commands:
                    self._error(503, "远程控制已停止")
                    return
                result = self.server.dispatch(request)
            else:
                with _dispatch_lock:
                    if not self.server.accept_commands:
                        self._error(503, "远程控制已停止")
                        return
                    result = (_export_session(self.server.dispatch, request)
                              if request["cmd"] == "session_export" else self.server.dispatch(request))
            self._send(200, result)
        except PermissionError as exc:
            self._error(403, str(exc))
        except (ValueError, UnicodeError) as exc:
            # Decoder messages may embed submitted data; do not echo them.
            self._error(400, "请求格式或导出结果无效" if isinstance(exc, (json.JSONDecodeError, UnicodeError)) else str(exc))
        except Exception:
            self._error(500, "操作失败，请在桌面查看详情")

    def do_OPTIONS(self):
        self._error(403, "不允许跨站访问")


def _urls(host: str, port: int) -> list[str]:
    if host not in {"0.0.0.0", ""}:
        return [f"http://{host}:{port}"]
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), port, socket.AF_INET):
            address = item[4][0]
            parsed = ipaddress.ip_address(address)
            if parsed.is_private and not parsed.is_loopback and not parsed.is_link_local:
                addresses.add(address)
    except OSError:
        pass
    return [f"http://{address}:{port}" for address in sorted(addresses)] + [f"http://127.0.0.1:{port}"]


def _status_unlocked() -> dict[str, Any]:
    if _server is None:
        return {"running": False, "host": "", "port": None, "urls": []}
    host, port = _server.server_address[:2]
    return {"running": _server.accept_commands, "host": host, "port": port,
            "urls": _urls(host, port), "scope": "lan" if host == "0.0.0.0" else "local"}


def start_server(dispatch: Callable[[dict], dict], host: str = "127.0.0.1", port: int = 0) -> dict[str, Any]:
    """Start once; only this local call returns the bearer token."""
    global _server, _thread
    if not callable(dispatch):
        raise TypeError("dispatch must be callable")
    with _state_lock:
        if _server is not None:
            return {**_status_unlocked(), "token": _server.token}
        server = _ControlServer((host, port), dispatch)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05},
                                  name="scizhi-phone-control", daemon=True)
        _server, _thread = server, thread
        thread.start()
        return {**_status_unlocked(), "token": server.token}


def stop_server() -> dict[str, Any]:
    """Reject queued commands and release the listener without stopping tasks."""
    global _server, _thread
    with _state_lock:
        if _server is not None:
            _server.accept_commands = False
            _server.shutdown()
            _server.server_close()
            if _thread is not None:
                _thread.join(timeout=1)
            _server, _thread = None, None
        return _status_unlocked()


def server_status() -> dict[str, Any]:
    """Public engine/UI status intentionally excludes credentials."""
    with _state_lock:
        return _status_unlocked()
