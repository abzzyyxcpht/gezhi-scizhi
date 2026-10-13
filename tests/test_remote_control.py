"""Exercise the phone control boundary through real HTTP requests."""
from __future__ import annotations

import concurrent.futures
import http.client
import importlib
import importlib.util
import json
import io
from email.message import Message
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest


class RemoteControlTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("src.remote_control"),
                             "remote control service has not been implemented")
        self.remote = importlib.import_module("src.remote_control")
        self.remote.stop_server()
        self.calls = []

        def dispatch(request):
            self.calls.append(request)
            if request["cmd"] == "list":
                return {"ok": True, "tasks": [{"task_id": "task-1", "topic": "真实任务"}]}
            return {"ok": True, "received": request}

        self.info = self.remote.start_server(dispatch)
        self.port = self.info["port"]
        self.token = self.info["token"]
        self.addCleanup(self.remote.stop_server)

    def request(self, path="/api/command", body=None, *, auth=True, origin=None,
                method=None, extra_headers=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self.token
        if origin:
            headers["Origin"] = origin
        headers.update(extra_headers or {})
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method or ("POST" if body is not None else "GET"),
                               path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            result = json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw.decode()
            return response.status, result, dict(response.getheaders())
        finally:
            connection.close()

    def test_anonymous_status_and_command_are_unauthorized(self):
        for path, body in [("/api/status", None), ("/api/command", {"cmd": "list"})]:
            self.assertEqual(self.request(path, body, auth=False)[0], 401)
        self.assertEqual(self.calls, [])

    def test_public_root_contains_login_but_no_server_token(self):
        status, html, headers = self.request("/", auth=False)
        self.assertEqual(status, 200)
        self.assertIn("访问令牌", html)
        self.assertNotIn(self.token, html)
        self.assertIn("Content-Security-Policy", headers)

    def test_cancel_can_arrive_while_continue_is_running(self):
        entered, release = threading.Event(), threading.Event()
        def dispatch(req):
            if req['cmd'] == 'agent_continue':
                entered.set()
                release.wait(5)
            return {'ok': True}
        self.remote._server.dispatch = dispatch
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(self.request, body={'cmd': 'agent_continue', 'session_id': 's1'})
            self.assertTrue(entered.wait(2))
            cancelled = pool.submit(self.request, body={'cmd': 'agent_cancel', 'session_id': 's1'})
            try:
                self.assertEqual(200, cancelled.result(timeout=1)[0])
            finally:
                release.set()
            self.assertEqual(200, running.result(timeout=3)[0])
    def test_status_uses_same_dispatcher_without_exposing_token(self):
        status, result, _ = self.request("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(result["tasks"][0]["task_id"], "task-1")
        self.assertEqual(self.calls, [{"cmd": "list"}])
        self.assertNotIn(self.token, json.dumps(result))
        self.assertNotIn("token", self.remote.server_status())

    def test_queue_controls_remain_available_during_running_turn(self):
        entered, release = threading.Event(), threading.Event()

        def dispatch(req):
            if req['cmd'] == 'agent_continue':
                entered.set()
                release.wait(4)
            return {'ok': True, 'received': req}

        self.remote._server.dispatch = dispatch
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(self.request, body={'cmd': 'agent_continue', 'session_id': 's1'})
            try:
                self.assertTrue(entered.wait(2))
                for body in [
                    {'cmd': 'queue_add', 'session_id': 's1', 'request': '下一问', 'queue_request_id': 'retry-1'},
                    {'cmd': 'queue_pause', 'session_id': 's1'},
                    {'cmd': 'queue_list', 'session_id': 's1'},
                    {'cmd': 'evidence_brief', 'task_id': 'task-1'},
                    {'cmd': 'steer_status', 'session_id': 's1'},
                    {'cmd': 'review_list'},
                    {'cmd': 'session_history', 'session_id': 's1', 'query': 'needle', 'page': 1},
                    {'cmd': 'review_cancel', 'job_id': 'rv_one'},
                    {'cmd': 'steer_add', 'session_id': 's1', 'expected_turn_id': 't1', 'steer_request_id': 'r1', 'request': '补充'},
                ]:
                    with self.subTest(cmd=body['cmd']):
                        response = pool.submit(self.request, body=body).result(timeout=1)
                        self.assertEqual(response[0], 200)
                        self.assertEqual(response[1]['received'], body)
            finally:
                release.set()
                self.assertEqual(running.result(timeout=3)[0], 200)

    def test_queue_and_evidence_reject_invalid_identity_and_execution_overrides(self):
        requests = [
            {'cmd': 'session_history', 'session_id': 's1', 'page_size': 101},
            {'cmd': 'session_history_message', 'session_id': 's1', 'message_id': -1, 'generation': 'x'},
            {'cmd': 'review_start', 'record_id': 'r', 'expected_version': 1, 'mode': [], 'review_request_id': 'key'},
            {'cmd': 'review_accept', 'job_id': '../job'},
            {'cmd': 'steer_add', 'session_id': 's1', 'request': 'missing identity'},
            {'cmd': 'steer_add', 'session_id': 's1', 'request': 'x', 'expected_turn_id': '../turn', 'steer_request_id': 'r'},
            {'cmd': 'queue_add', 'session_id': 's1', 'request': 'missing key'},
            {'cmd': 'queue_add', 'session_id': 's1', 'request': 'x', 'queue_request_id': '../bad'},
            {'cmd': 'queue_add', 'session_id': 's1', 'request': 'x', 'queue_request_id': 'k', 'online': True},
            {'cmd': 'queue_add', 'session_id': 's1', 'request': 'x', 'queue_request_id': 'k', 'queue_ticket': 't'},
            {'cmd': 'queue_add', 'session_id': 's1', 'request': 'x' * 32001, 'queue_request_id': 'k'},
            {'cmd': 'queue_reorder', 'session_id': 's1', 'ordered_ids': ['a', 'a']},
            {'cmd': 'queue_reorder', 'session_id': 's1', 'ordered_ids': ['../a']},
            {'cmd': 'queue_discard', 'session_id': 's1'},
            {'cmd': 'evidence_brief', 'task_id': '../task'},
            {'cmd': 'evidence_brief', 'execute': True},
        ]
        for body in requests:
            with self.subTest(body=body):
                self.assertEqual(self.request(body=body)[0], 400)
        self.assertEqual(self.calls, [])

    def test_foreign_origin_rejected_and_same_origin_accepted(self):
        self.assertEqual(self.request(body={"cmd": "list"}, origin="https://evil.example")[0], 403)
        self.assertEqual(self.request(body={"cmd": "list"}, origin="null")[0], 403)
        self.assertEqual(self.request(body={"cmd": "list"}, origin=f"http://127.0.0.1:{self.port}")[0], 200)
        self.assertEqual(len(self.calls), 1)

    def test_malformed_origin_is_rejected_with_json_error(self):
        self.assertEqual(self.request(body={"cmd": "list"}, origin="http://[")[0], 403)
        self.assertEqual(self.calls, [])

    def test_rejection_consumes_declared_post_body_before_sending_json(self):
        handler = object.__new__(self.remote._Handler)
        handler.command = 'POST'
        handler.headers = Message()
        handler.headers['Content-Length'] = '12'
        handler.rfile = io.BytesIO(b'pending-body')
        sent = []
        handler._send = lambda status, payload: sent.append((status, handler.rfile.tell(), payload))
        handler._error(403, 'Rejected')
        self.assertEqual(sent[0][:2], (403, 12))
        # JSON validation can also reject after do_POST has already read it.
        handler.rfile = io.BytesIO(b'do-not-read')
        handler._error(400, 'Again')
        self.assertEqual(handler.rfile.tell(), 0)

    def test_invalid_enum_values_are_client_errors(self):
        for body in [{"cmd": "session_export", "session_id": "session-1", "format": []},
                     {"cmd": "agent_approve", "session_id": "session-1", "tool_call_id": "call-1", "scope": []}]:
            self.assertEqual(self.request(body=body)[0], 400)
        self.assertEqual(self.calls, [])

    def test_denial_cannot_be_changed_to_session_authorization(self):
        self.assertEqual(self.request(body={"cmd": "agent_deny", "session_id": "session-1", "tool_call_id": "call-1", "scope": "session"})[0], 400)
        self.assertEqual(self.calls, [])

    def test_unsupported_commands_cannot_reach_dispatcher(self):
        for cmd in ["settings_save", "terminal_open", "shell", "stats", "plugin_call", "export"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.request(body={"cmd": cmd})[0], 403)
        self.assertEqual(self.calls, [])

    def test_arbitrary_fields_and_nested_paths_are_rejected(self):
        for request in [{"cmd": "list", "path": "C:/secret"},
                        {"cmd": "workflow_run", "workflow_id": "research_pipeline", "topic": "AI", "params": {"path": "C:/secret"}},
                        {"cmd": "session_export", "session_id": "safe-id", "dir": "C:/secret"},
                        {"cmd": "poll", "task_id": "../../secret"}]:
            with self.subTest(request=request):
                self.assertEqual(self.request(body=request)[0], 400)
        self.assertEqual(self.calls, [])

    def test_oversized_request_rejected_before_dispatch(self):
        self.assertEqual(self.request(body=b"x" * (65536 + 1))[0], 413)
        self.assertEqual(self.calls, [])

    def test_invalid_json_or_non_object_rejected(self):
        for body in [b"{", b"[]", b"null"]:
            self.assertEqual(self.request(body=body)[0], 400)
        self.assertEqual(self.calls, [])

    def test_workflow_and_loop_commands_are_forwarded(self):
        requests = [{"cmd": "workflows"},
                    {"cmd": "workflow_run", "workflow_id": "research_pipeline", "topic": "AI", "offline": True},
                    {"cmd": "loop_start", "workflow_id": "research_pipeline", "topic": "AI", "interval_seconds": 60, "max_iterations": 2},
                    {"cmd": "loop_list"}, {"cmd": "loop_stop", "loop_id": "loop-1"},
                    {"cmd": "loop_resume", "loop_id": "loop-1"}]
        for request in requests:
            self.assertEqual(self.request(body=request)[0], 200)
        self.assertEqual(self.calls, requests)

    def test_approval_and_task_controls_are_forwarded(self):
        requests = [{"cmd": "pause", "task_id": "task-1"},
                    {"cmd": "resume", "task_id": "task-1"},
                    {"cmd": "cancel", "task_id": "task-1"},
                    {"cmd": "agent_approve", "session_id": "session-1", "tool_call_id": "call-1", "scope": "once"}]
        for request in requests:
            self.assertEqual(self.request(body=request)[0], 200)
        self.assertEqual(self.calls, requests)

    def test_session_export_returns_only_generated_content(self):
        self.remote.stop_server()

        def dispatch(request):
            self.assertEqual(request["cmd"], "session_export")
            output = Path(request["dir"]) / "safe-session.md"
            output.write_text("# 会话记录\n完成", encoding="utf-8")
            return {"ok": True, "path": str(output)}

        self.info = self.remote.start_server(dispatch)
        self.port, self.token = self.info["port"], self.info["token"]
        status, result, _ = self.request(body={"cmd": "session_export", "session_id": "session-1", "format": "markdown"})
        self.assertEqual(status, 200)
        self.assertEqual(result["content"], "# 会话记录\n完成")
        self.assertEqual(result["filename"], "safe-session.md")
        self.assertNotIn("path", result)

    def test_session_export_rejects_dispatcher_file_outside_own_directory(self):
        self.remote.stop_server()
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory) / "secret.md"
            other.write_text("private", encoding="utf-8")
            self.info = self.remote.start_server(lambda _: {"ok": True, "path": str(other)})
            self.port, self.token = self.info["port"], self.info["token"]
            status, result, _ = self.request(body={"cmd": "session_export", "session_id": "session-1"})
            self.assertEqual(status, 400)
            self.assertNotIn("private", json.dumps(result))

    def test_real_workbench_workflow_catalog_and_redacted_session_export(self):
        from src.agent_runtime import SessionStore
        from src.workbench import Workbench
        self.remote.stop_server()
        with tempfile.TemporaryDirectory() as directory:
            workbench = Workbench(directory)
            store = SessionStore(Path(directory) / "sessions")
            store.append("phone-contract", "user_message", {"text": "AI research", "api_key": "sensitive-test-value"})
            try:
                self.info = self.remote.start_server(workbench.handle)
                self.port, self.token = self.info["port"], self.info["token"]
                code, catalog, _ = self.request(body={"cmd": "workflows"})
                self.assertEqual(code, 200)
                self.assertIn("research_pipeline", [row["id"] for row in catalog["workflows"]])
                code, exported, _ = self.request(body={"cmd": "session_export", "session_id": "phone-contract", "format": "json"})
                self.assertEqual(code, 200)
                document = json.loads(exported["content"])
                self.assertEqual(document["metadata"]["source_session_id"], "phone-contract")
                self.assertNotIn("sensitive-test-value", exported["content"])
            finally:
                self.remote.stop_server()
                workbench.close()

    def test_dispatcher_mutations_are_serialized(self):
        self.remote.stop_server()
        active = 0
        maximum = 0
        state_lock = threading.Lock()

        def dispatch(request):
            nonlocal active, maximum
            with state_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with state_lock:
                active -= 1
            return {"ok": True}

        self.info = self.remote.start_server(dispatch)
        self.port, self.token = self.info["port"], self.info["token"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.request(body={"cmd": "pause", "task_id": "task-1"})[0], range(4)))
        self.assertEqual(results, [200] * 4)
        self.assertEqual(maximum, 1)

    def test_readonly_commands_and_status_remain_available_during_approval(self):
        self.remote.stop_server()
        entered = threading.Event()
        release = threading.Event()

        def dispatch(request):
            if request["cmd"] == "agent_approve":
                entered.set()
                release.wait(3)
            return {"ok": True, "tasks": []}

        self.info = self.remote.start_server(dispatch)
        self.port, self.token = self.info["port"], self.info["token"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            approval = pool.submit(self.request, body={"cmd": "agent_approve", "session_id": "session-1", "tool_call_id": "call-1"})
            try:
                self.assertTrue(entered.wait(1))
                start = time.perf_counter()
                self.assertEqual(self.request("/api/status")[0], 200)
                for cmd in ["nodes", "usage", "plugin_commands", "sessions", "loop_list"]:
                    self.assertEqual(self.request(body={"cmd": cmd})[0], 200)
                self.assertLess(time.perf_counter() - start, 1)
                self.assertFalse(approval.done())
            finally:
                release.set()
                self.assertEqual(approval.result()[0], 200)

    def test_workspace_readonly_commands_are_forwarded(self):
        requests = [{"cmd": "project_list"},
                    {"cmd": "project_files", "project_id": "proj-1", "path": "notes"},
                    {"cmd": "chat_sessions", "query": "文献"},
                    {"cmd": "chat_sessions", "project_id": "proj-1", "include_archived": True},
                    {"cmd": "notebook_list", "kind": "hypothesis"},
                    {"cmd": "todo_list", "project_id": "proj-1"},
                    {"cmd": "schedule_list"},
                    {"cmd": "hook_list"},
                    {"cmd": "custom_commands", "query": "综述"}]
        for request in requests:
            self.assertEqual(self.request(body=request)[0], 200)
        self.assertEqual(self.calls, requests)

    def test_workspace_mutations_and_content_reads_stay_denied(self):
        for cmd in ["chat_turn", "notebook_save", "notebook_get", "project_preview",
                    "project_attachment", "custom_command_save", "hook_save", "todo_save"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.request(body={"cmd": cmd})[0], 403)
        self.assertEqual(self.calls, [])

    def test_workspace_field_allowlist_is_enforced(self):
        for request in [{"cmd": "todo_list", "path": "C:/secret"},
                        {"cmd": "project_files"},
                        {"cmd": "project_files", "project_id": "proj-1", "path": "sub", "extra": 1},
                        {"cmd": "schedule_list", "session_id": "s 1"},
                        {"cmd": "notebook_list", "task_id": "../../secret"}]:
            with self.subTest(request=request):
                self.assertEqual(self.request(body=request)[0], 400)
        self.assertEqual(self.calls, [])

    def test_stop_closes_listener_and_can_restart_same_port(self):
        self.remote.stop_server()
        self.assertFalse(self.remote.server_status()["running"])
        with socket.socket() as connection:
            self.assertNotEqual(connection.connect_ex(("127.0.0.1", self.port)), 0)
        info = self.remote.start_server(lambda _: {"ok": True}, port=self.port)
        self.assertEqual(info["port"], self.port)


if __name__ == "__main__":
    unittest.main()
