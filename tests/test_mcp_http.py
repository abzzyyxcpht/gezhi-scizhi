"""2025 Streamable HTTP lifecycle, framing and credential boundaries."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from src import mcp, settings


class Chunks(httpx.SyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    def __iter__(self):
        # Split UTF-8 and CRLF boundaries as a real network stream may do.
        for index in range(0, len(self.data), 7):
            yield self.data[index:index + 7]


class HttpMcpTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.preferences = {"mcp_enabled": True}
        for patch in (mock.patch.object(mcp, "CONFIG_PATH", self.root / "mcp.json"),
                      mock.patch.object(settings, "load", side_effect=lambda: dict(self.preferences))):
            patch.start()
            self.addCleanup(patch.stop)
        self.requests = []

    def save(self, **extra):
        response = mcp.handle({"cmd": "mcp_save", "server": {
            "name": "remote", "transport": "streamable_http", "url": "https://mcp.test/mcp",
            "headers": {"Authorization": "Bearer top-secret", "X-API-Key": "key-secret"}, **extra}})
        self.assertTrue(response["ok"], response)
        return response

    def connect(self, handler):
        real_client = httpx.Client
        patch = mock.patch.object(httpx, "Client", side_effect=lambda **kwargs:
                                  real_client(transport=httpx.MockTransport(handler), **kwargs))
        patch.start()
        self.addCleanup(patch.stop)
        return patch

    def handler(self, request):
        self.requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(405)
        body = json.loads(request.content)
        method = body.get("method")
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            return httpx.Response(200, headers={"Mcp-Session-Id": "session-123"}, json={
                "jsonrpc": "2.0", "id": body["id"], "result": {
                    "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1"}}})
        result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]} if method == "tools/list" else {
            "content": [{"type": "text", "text": "你好 " + body["params"]["arguments"].get("text", "")}],
            "structuredContent": {"executed": True}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def test_configuration_redacts_headers_and_preserves_omitted_or_null_secrets_without_network(self):
        with mock.patch.object(httpx, "Client") as client, mock.patch.object(mcp, "spawn_owned_argv") as process:
            response = self.save()
            public = response["servers"][0]
            self.assertIsNone(public["headers"]["Authorization"])
            self.assertNotIn("top-secret", str(response))
            self.assertNotIn("key-secret", str(response))
            self.assertEqual(public["secret_header_keys"], ["Authorization", "X-API-Key"])
            self.assertTrue(mcp.handle({"cmd": "mcp_save", "server": public})["ok"])
            self.assertTrue(mcp.handle({"cmd": "mcp_save", "server": {"name": "remote", "enabled": False}})["ok"])
            self.assertEqual(mcp.load_servers()[0]["headers"]["Authorization"], "Bearer top-secret")
            mcp.handle({"cmd": "mcp_config"})
            client.assert_not_called()
            process.assert_not_called()

    def test_invalid_url_credentials_and_protocol_headers_rejected_without_echoing_secrets(self):
        for fields in ({"url": "https://user:password-secret@mcp.test/mcp"},
                       {"url": "https://mcp.test/mcp?token=query-secret"},
                       {"url": "https://mcp.test/mcp#fragment-secret"},
                       {"url": "file:///secret"}, {"url": "https://mcp.test:bad/mcp"},
                       {"headers": {"Authorization": "secret\r\nX-Leak: secret"}},
                       {"headers": {"Host": "evil.test"}},
                       {"headers": {"Mcp-Session-Id": "session-secret"}},
                       {"headers": {"Content-Length": "0"}},
                       {"headers": {"a": "one", "A": "two"}}):
            with self.subTest(fields=fields):
                result = mcp.handle({"cmd": "mcp_save", "server": {
                    "name": "bad", "transport": "streamable_http", "url": "https://mcp.test/mcp", **fields}})
                self.assertFalse(result["ok"])
                self.assertNotIn("secret", result["error"])
        self.assertEqual(mcp.load_servers(), [])

    def test_json_roundtrip_negotiates_version_and_terminates_session(self):
        self.save()
        self.connect(self.handler)
        client = mcp.Client(mcp.load_servers()[0])
        client.initialize()
        self.assertEqual(client.list_tools()[0]["name"], "echo")
        result = client.call_tool_result("echo", {"text": "result"})
        self.assertEqual(result["structuredContent"], {"executed": True})
        self.assertEqual(result["content"][0]["text"], "你好 result")
        client.close()
        client.close()
        self.assertEqual([r.method for r in self.requests], ["POST"] * 4 + ["DELETE"])
        self.assertEqual(json.loads(self.requests[0].content)["params"]["protocolVersion"], "2025-11-25")
        for request in self.requests:
            self.assertEqual(request.headers["Authorization"], "Bearer top-secret")
            self.assertEqual(request.url, "https://mcp.test/mcp")
            self.assertIn("application/json", request.headers["Accept"])
            self.assertIn("text/event-stream", request.headers["Accept"])
        self.assertNotIn("Mcp-Session-Id", self.requests[0].headers)
        for request in self.requests[1:]:
            self.assertEqual(request.headers["Mcp-Session-Id"], "session-123")
            self.assertEqual(request.headers["Mcp-Protocol-Version"], "2025-06-18")

    def test_probe_returns_tools_count_and_actual_protocol_version(self):
        self.save()
        self.connect(self.handler)
        result = mcp.probe_named("remote")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool_count"], 1)
        self.assertEqual(result["protocol_version"], "2025-06-18")
        self.assertEqual(result["transport"], "streamable_http")
        self.assertEqual(self.requests[-1].method, "DELETE")
        self.assertNotIn("top-secret", str(result))

    def test_sse_response_handles_comments_notifications_multiline_and_split_utf8(self):
        self.save()
        def handler(request):
            response = self.handler(request)
            if request.method != "POST" or json.loads(request.content).get("method") in {"initialize", "notifications/initialized"}:
                return response
            result = response.json()
            wire = (": heartbeat\r\nid: warmup\r\ndata:\r\n\r\n"
                    "data: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\",\"params\":{}}\r\n\r\n"
                    "event: message\r\ndata: " + json.dumps(result, ensure_ascii=False, indent=1).replace("\n", "\r\ndata: ") + "\r\n\r\n")
            return httpx.Response(200, headers={"Content-Type": "text/event-stream; charset=utf-8"},
                                  stream=Chunks(wire.encode("utf-8")))
        self.connect(handler)
        result = mcp.call_named("remote", "echo", {"text": "世界"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "你好 世界")
        self.assertEqual(self.requests[-1].method, "DELETE")

    def test_redirects_are_not_followed_and_errors_do_not_include_body_location_or_credentials(self):
        self.save()
        def handler(request):
            self.requests.append(request)
            return httpx.Response(307, headers={"Location": "https://evil.test/?token=query-secret"},
                                  text="Bearer top-secret key-secret query-secret")
        self.connect(handler)
        result = mcp.call_named("remote", "echo")
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.requests), 1)
        self.assertIn("307", result["error"])
        self.assertNotIn("secret", result["error"])
        self.assertNotIn("evil.test", result["error"])

    def test_network_errors_and_remote_rpc_errors_never_echo_credentials(self):
        self.save()
        for mode in ("network", "rpc", "http"):
            with self.subTest(mode=mode):
                def handler(request):
                    if mode == "network":
                        raise httpx.ConnectError("Bearer top-secret https://evil.test/?token=query-secret", request=request)
                    if mode == "http":
                        return httpx.Response(401, text="Bearer top-secret query-secret")
                    body = json.loads(request.content)
                    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                        "error": {"code": -32603, "message": "Bearer top-secret key-secret query-secret"}})
                real_client = httpx.Client
                with mock.patch.object(httpx, "Client", side_effect=lambda **kwargs:
                        real_client(transport=httpx.MockTransport(handler), **kwargs)):
                    result = mcp.call_named("remote", "echo")
                self.assertFalse(result["ok"])
                self.assertNotIn("secret", result["error"])

    def test_disabled_http_servers_cannot_create_connections_or_processes(self):
        self.save(enabled=False, autoload=True)
        with mock.patch.object(httpx, "Client") as client, mock.patch.object(mcp, "spawn_owned_argv") as process:
            self.assertFalse(mcp.probe_named("remote")["ok"])
            self.assertFalse(mcp.call_named("remote", "echo")["ok"])
            self.assertFalse(mcp.status_all()[0]["ok"])
            with self.assertRaises(ValueError):
                mcp.Client(mcp.load_servers()[0])
            client.assert_not_called()
            process.assert_not_called()

    def test_unknown_negotiated_version_is_rejected_and_no_tool_runs(self):
        self.save()
        def handler(request):
            response = self.handler(request)
            if request.method == "POST" and json.loads(request.content).get("method") == "initialize":
                data = response.json()
                data["result"]["protocolVersion"] = "2026-07-28"
                return httpx.Response(200, headers={"Mcp-Session-Id": "session-123"}, json=data)
            return response
        self.connect(handler)
        result = mcp.call_named("remote", "echo")
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.requests), 2)  # initialize and cleanup DELETE
        self.assertIn("版本", result["error"])

    def test_stream_ending_without_response_is_not_replayed(self):
        self.save()
        def handler(request):
            response = self.handler(request)
            if request.method == "POST" and json.loads(request.content).get("method") == "tools/call":
                return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                      content=b"id: 1\ndata:\nretry: 1000\n\n")
            return response
        self.connect(handler)
        result = mcp.call_named("remote", "echo")
        self.assertFalse(result["ok"])
        self.assertEqual(sum(r.method == "POST" and json.loads(r.content).get("method") == "tools/call"
                             for r in self.requests), 1)

    def test_expired_session_reinitializes_without_replaying_tool(self):
        self.save()
        def handler(request):
            response = self.handler(request)
            if request.method == "POST" and json.loads(request.content).get("method") == "tools/call":
                return httpx.Response(404)
            return response
        self.connect(handler)
        result = mcp.call_named("remote", "echo")
        self.assertFalse(result["ok"])
        methods = [json.loads(r.content)["method"] for r in self.requests if r.method == "POST"]
        self.assertEqual(methods.count("initialize"), 2)
        self.assertEqual(methods.count("tools/call"), 1)
        for request in self.requests:
            if request.method == "POST" and json.loads(request.content)["method"] == "initialize":
                self.assertNotIn("Mcp-Session-Id", request.headers)

    def test_stateless_server_sse_initialization_and_carriage_return_framing(self):
        self.save()
        def handler(request):
            response = self.handler(request)
            if request.method != "POST" or json.loads(request.content).get("method") == "notifications/initialized":
                return response
            wire = "data: " + json.dumps(response.json(), ensure_ascii=False) + "\r\r"
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  stream=Chunks(wire.encode("utf-8")))
        self.connect(handler)
        result = mcp.call_named("remote", "echo", {"text": "无状态"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "你好 无状态")
        self.assertTrue(all(request.method == "POST" for request in self.requests))
        self.assertTrue(all("Mcp-Session-Id" not in request.headers for request in self.requests))

    def test_malformed_responses_and_oversized_payload_fail_without_echoing_body(self):
        self.save()
        for mode in ("id", "shape", "media", "size", "json"):
            with self.subTest(mode=mode):
                def handler(request):
                    body = json.loads(request.content)
                    if mode == "id":
                        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"] + 1,
                            "result": {"secret": "top-secret"}})
                    if mode == "shape":
                        return httpx.Response(200, json=[{"secret": "top-secret"}])
                    if mode == "media":
                        return httpx.Response(200, text="top-secret")
                    if mode == "json":
                        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"top-secret{")
                    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                        "result": {"message": "top-secret" * 100}})
                real_client = httpx.Client
                with mock.patch.object(httpx, "Client", side_effect=lambda **kwargs:
                        real_client(transport=httpx.MockTransport(handler), **kwargs)), \
                        mock.patch.object(mcp, "HTTP_MAX_RESPONSE_BYTES", 512):
                    result = mcp.call_named("remote", "echo")
                self.assertFalse(result["ok"])
                self.assertNotIn("top-secret", str(result))

    def test_failed_tool_result_redacts_configured_secrets(self):
        self.save()
        def handler(request):
            response = self.handler(request)
            if request.method == "POST" and json.loads(request.content).get("method") == "tools/call":
                body = json.loads(request.content)
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
                    "isError": True, "content": [{"type": "text", "text": "top-secret key-secret https://mcp.test/mcp"}],
                    "structuredContent": {"Authorization": "Bearer top-secret"}}})
            return response
        self.connect(handler)
        result = mcp.call_named("remote", "echo")
        self.assertFalse(result["ok"])
        self.assertNotIn("top-secret", str(result))
        self.assertNotIn("key-secret", str(result))
        self.assertNotIn("https://mcp.test/mcp", str(result))

    def test_server_ping_is_answered_without_granting_other_client_capabilities(self):
        self.save()
        def handler(request):
            if request.method == "POST":
                body = json.loads(request.content)
                if "method" not in body:
                    self.requests.append(request)
                    return httpx.Response(202)
            response = self.handler(request)
            if request.method == "POST" and json.loads(request.content).get("method") == "tools/call":
                messages = [{"jsonrpc": "2.0", "id": "ping1", "method": "ping"},
                            {"jsonrpc": "2.0", "id": "sample1", "method": "sampling/createMessage", "params": {}},
                            response.json()]
                wire = "".join("data: " + json.dumps(message) + "\n\n" for message in messages)
                return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=wire)
            return response
        self.connect(handler)
        result = mcp.call_named("remote", "echo")
        self.assertTrue(result["ok"], result)
        replies = [json.loads(r.content) for r in self.requests if r.method == "POST"
                   and "method" not in json.loads(r.content)]
        self.assertEqual(replies[0], {"jsonrpc": "2.0", "id": "ping1", "result": {}})
        self.assertEqual(replies[1]["error"]["code"], -32601)

    def test_closed_client_cannot_reinitialize(self):
        self.save()
        self.connect(self.handler)
        client = mcp.Client(mcp.load_servers()[0])
        client.initialize()
        client.close()
        with self.assertRaises(RuntimeError):
            client.initialize()

    def test_header_case_roundtrip_and_transport_switch_drop_inactive_credentials(self):
        self.save()
        result = mcp.handle({"cmd": "mcp_save", "server": {"name": "remote", "headers": {"authorization": None}}})
        self.assertTrue(result["ok"], result)
        self.assertEqual(mcp.load_servers()[0]["headers"], {"authorization": "Bearer top-secret"})
        result = mcp.handle({"cmd": "mcp_save", "server": {"name": "remote", "transport": "stdio",
            "command": "python", "url": "", "headers": {}}})
        self.assertTrue(result["ok"], result)
        self.assertNotIn("headers", mcp.load_servers()[0])
        self.assertNotIn("url", mcp.load_servers()[0])


if __name__ == "__main__":
    unittest.main()
