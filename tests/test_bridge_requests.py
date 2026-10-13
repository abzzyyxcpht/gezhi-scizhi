from __future__ import annotations
import io
import json
import time
import unittest
from unittest import mock
from src import bridge


class BridgeRequestsTest(unittest.TestCase):
    def test_deny_cannot_be_promoted_by_a_session_scope_field(self):
        runtime = mock.Mock()
        runtime.approve.return_value = {"status": "blocked"}
        with mock.patch.object(bridge, "_session_runtime", return_value=runtime):
            response = bridge.handle({"cmd": "agent_deny", "session_id": "session-a",
                                      "tool_call_id": "call-a", "scope": "session"})
        self.assertTrue(response["ok"])
        runtime.approve.assert_called_once_with("session-a", "call-a", "deny")

    def test_shell_exec_runs_in_project_root_with_output_cap(self):
        result = bridge.handle({"cmd": "shell_exec", "command": "echo scizhi-shell"})
        self.assertTrue(result["ok"], result.get("error"))
        self.assertIn("scizhi-shell", result.get("text", ""))
        self.assertEqual(result.get("code"), 0)

    def test_shell_exec_rejects_dangerous_commands(self):
        result = bridge.handle({"cmd": "shell_exec", "command": "rm -rf /"})
        self.assertFalse(result["ok"])
        self.assertIn("禁区", result.get("error", ""))

    def test_response_preserves_request_id(self):
        result = bridge.handle({"cmd": "nodes", "request_id": "client-7"})
        self.assertEqual(result.get("request_id"), "client-7")

    def test_slow_request_does_not_block_status(self):
        def dispatch(req):
            if req["cmd"] == "slow":
                time.sleep(.08)
            return {"ok": True, "cmd": req["cmd"]}
        input_stream = io.StringIO('\n'.join(json.dumps(r) for r in [
            {"cmd": "slow", "request_id": 1}, {"cmd": "list", "request_id": 2}]))
        output = io.StringIO()
        bridge.process_requests(input_stream, output, dispatch)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r["request_id"] for r in rows], [2, 1])

    def test_legacy_requests_remain_ordered(self):
        output = io.StringIO()
        bridge.process_requests(io.StringIO('{"cmd":"a"}\n{"cmd":"b"}\n'), output,
                                lambda req: {"ok": True, "cmd": req["cmd"]})
        self.assertEqual([json.loads(line)["cmd"] for line in output.getvalue().splitlines()], ["a", "b"])
