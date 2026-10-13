"""Workbench integrations enforce configuration and actual-target boundaries."""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src import mcp, settings
from src.agent_runtime import AgentRuntime, ToolCall


class McpConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.preferences = {"mcp_enabled": True, "hooks_enabled": True,
                            "browser_control_enabled": True,
                            "computer_control_enabled": False,
                            "subagents_enabled": True, "subagent_max_parallel": 2}
        for patch in (mock.patch.object(mcp, "CONFIG_PATH", self.root / "mcp.json"),
                      mock.patch.object(settings, "load", side_effect=lambda: dict(self.preferences))):
            patch.start()
            self.addCleanup(patch.stop)

    def command(self, cmd, **fields):
        self.assertTrue(callable(getattr(mcp, "handle", None)), "MCP config commands are missing")
        return mcp.handle({"cmd": cmd, **fields})

    def create_server(self, **extra):
        return self.command("mcp_save", server={"name": "browser", "command": sys.executable,
                            "args": [str(Path(__file__).with_name("fake_mcp_server.py"))],
                            **extra})

    def test_read_and_secret_roundtrip_never_spawn(self):
        with mock.patch.object(mcp, "Client") as client:
            self.assertTrue(self.create_server(env={"API_TOKEN": "secret-123"})["ok"])
            public = self.command("mcp_config")
            self.assertNotIn("secret-123", json.dumps(public))
            self.assertIsNone(public["servers"][0]["env"]["API_TOKEN"])
            row = public["servers"][0]
            row["enabled"] = False
            self.assertTrue(self.command("mcp_save", server=row)["ok"])
            self.assertEqual(mcp.load_servers()[0]["env"]["API_TOKEN"], "secret-123")
            client.assert_not_called()

    def test_disabled_server_never_spawns_for_call_probe_or_registration(self):
        self.create_server(enabled=False, autoload=True)
        with mock.patch.object(mcp, "Client") as client:
            self.assertFalse(mcp.call_named("browser", "echo")["ok"])
            self.assertFalse(self.command("mcp_probe", name="browser")["ok"])
            self.assertFalse(mcp.status_all()[0]["ok"])
            self.assertEqual(mcp.register_actions(), 0)
            client.assert_not_called()

    def test_explicit_probe_and_enabled_toggle_execute_real_stdio(self):
        self.create_server(enabled=False)
        self.assertTrue(self.command("mcp_save", server={"name": "browser", "enabled": True})["ok"])
        self.assertTrue(self.command("mcp_probe", name="browser")["ok"])
        result = mcp.call_named("browser", "echo", {"text": "roundtrip"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "echo:roundtrip")
        self.assertTrue(self.command("mcp_delete", name="browser")["ok"])
        self.assertEqual(mcp.load_servers(), [])

    def test_invalid_shapes_do_not_replace_valid_configuration(self):
        self.create_server()
        before = mcp.CONFIG_PATH.read_text(encoding="utf-8")
        for fields in ({"args": "shell command"}, {"enabled": "false"},
                       {"transport": "http"}, {"env": {"TOKEN": ["bad"]}},
                       {"command": "python\nmalicious"}):
            with self.subTest(fields=fields):
                self.assertFalse(self.command("mcp_save", server={"name": "browser", **fields})["ok"])
                self.assertEqual(mcp.CONFIG_PATH.read_text(encoding="utf-8"), before)

    def test_command_line_secrets_and_probe_errors_are_redacted(self):
        self.create_server(args=["--api-key", "argument-secret", "--token=second-secret"],
                           env={"TOKEN": "environment-secret"})
        public = self.command("mcp_config")
        text = json.dumps(public)
        for secret in ("argument-secret", "second-secret", "environment-secret"):
            self.assertNotIn(secret, text)
        self.assertTrue(self.command("mcp_save", server=public["servers"][0])["ok"])
        self.assertEqual(mcp.load_servers()[0]["args"][1], "argument-secret")
        with mock.patch.object(mcp, "Client", side_effect=OSError("environment-secret argument-secret second-secret")):
            result = self.command("mcp_probe", name="browser")
        for secret in ("argument-secret", "second-secret", "environment-secret"):
            self.assertNotIn(secret, str(result))

    def test_deleted_or_disabled_server_revokes_previously_registered_action(self):
        from src.orchestrator.engine import ACTIONS
        self.create_server(autoload=True)
        with mock.patch.dict(ACTIONS, {}, clear=True):
            mcp.register_actions()
            action = ACTIONS["mcp_browser_echo"]
            self.command("mcp_save", server={"name": "browser", "enabled": False})
            with mock.patch.object(mcp, "Client") as client:
                with self.assertRaises(RuntimeError):
                    action(mock.Mock(), {"arguments": {}})
                client.assert_not_called()
            self.assertTrue(self.command("mcp_save", server={"name": "browser", "enabled": True})["ok"])
            self.assertTrue(self.command("mcp_delete", name="browser")["ok"])
            with mock.patch.object(mcp, "Client") as client:
                with self.assertRaises(RuntimeError):
                    action(mock.Mock(), {"arguments": {}})
                client.assert_not_called()

    def test_external_config_deletion_revokes_previously_registered_action(self):
        from src.orchestrator.engine import ACTIONS
        self.create_server(autoload=True)
        with mock.patch.dict(ACTIONS, {}, clear=True):
            mcp.register_actions()
            action = ACTIONS["mcp_browser_echo"]
            mcp.CONFIG_PATH.write_text('{"servers": []}', encoding="utf-8")
            with mock.patch.object(mcp, "Client") as client:
                with self.assertRaises(RuntimeError):
                    action(mock.Mock(), {"arguments": {}})
                client.assert_not_called()


class ControlsAndHooksTest(McpConfigurationTest):
    def setUp(self):
        super().setUp()
        self.assertIsNotNone(importlib.util.find_spec("src.control_integrations"), "control module is missing")
        self.assertIsNotNone(importlib.util.find_spec("src.lifecycle_hooks"), "lifecycle hooks are missing")
        self.controls = importlib.import_module("src.control_integrations")
        self.hooks = importlib.import_module("src.lifecycle_hooks")
        for module, attribute, path in ((self.controls, "CONFIG_PATH", "controls.json"),
                                         (self.hooks, "CONFIG_PATH", "hooks.json"),
                                         (self.hooks, "LOG_PATH", "hooks.jsonl")):
            patch = mock.patch.object(module, attribute, self.root / path)
            patch.start()
            self.addCleanup(patch.stop)

    def bind_browser(self):
        self.create_server()
        return self.controls.handle({"cmd": "control_save", "kind": "browser",
                                     "binding": {"server": "browser", "enabled": True}})

    def test_grouped_bridge_route_forwards_mcp_crud_without_probe(self):
        from src import bridge
        self.create_server()
        with mock.patch.object(mcp, "Client") as client:
            response = bridge.handle({"cmd": "mcp_config"})
            self.assertTrue(response["ok"], response)
            self.assertEqual(response["servers"][0]["name"], "browser")
            client.assert_not_called()

    def test_control_unconfigured_and_disabled_do_not_call(self):
        with mock.patch.object(mcp, "Client") as client:
            status = self.controls.handle({"cmd": "control_status"})
            self.assertFalse(status["controls"]["browser"]["configured"])
            self.assertFalse(self.controls.handle({"cmd": "control_call", "kind": "browser", "tool": "echo"})["ok"])
            self.bind_browser()
            self.preferences["browser_control_enabled"] = False
            self.assertFalse(self.controls.handle({"cmd": "control_call", "kind": "browser", "tool": "echo"})["ok"])
            client.assert_not_called()

    def test_control_host_and_runtime_execute_bound_server(self):
        self.assertTrue(self.bind_browser()["ok"])
        host = self.controls.handle({"cmd": "control_call", "kind": "browser",
                                     "tool": "echo", "arguments": {"text": "host"}})
        self.assertEqual(host["text"], "echo:host")
        runtime = AgentRuntime(session_root=self.root / "sessions")
        result = runtime._execute_call("s", ToolCall("c", "control.call",
                       {"kind": "browser", "server": "browser", "tool": "echo",
                        "arguments": {"text": "agent"}}, "external"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "echo:agent")

    def test_control_permission_uses_target_and_does_not_bypass_mcp_deny(self):
        # C2 三档门后：control.call 需 full-access 档位才进入策略层（档位拒绝有专测）
        self.preferences["agent_permission_tier"] = "full-access"
        self.bind_browser()
        runtime = AgentRuntime(session_root=self.root / "sessions")
        args = {"kind": "browser", "server": "browser", "tool": "echo"}
        self.assertEqual(runtime._permission_key("control.call", args), "mcp/browser/echo")
        runtime.policy.allow_for_session("control.call", "s")
        self.assertEqual(runtime._agent_gate("s", "control.call", "external", args), "ask")
        runtime.policy.allow_for_session("mcp/browser/echo", "s")
        self.assertEqual(runtime._agent_gate("s", "control.call", "external", args), "allow")
        runtime.policy.decisions["mcp.call"] = "deny"
        self.assertEqual(runtime._agent_gate("s", "control.call", "external", args), "deny")
        with mock.patch.object(mcp, "Client") as client:
            result = runtime._execute_call("s", ToolCall("x", "control.call", {**args, "server": "other"}, "external"))
            self.assertFalse(result["ok"])
            client.assert_not_called()

    def test_invalid_control_kind_cannot_crash_permission_gate(self):
        self.bind_browser()
        runtime = AgentRuntime(session_root=self.root / "sessions")
        self.assertEqual(runtime._agent_gate("s", "control.call", "external",
                         {"kind": ["browser"], "server": "browser", "tool": "echo"}), "deny")

    def test_binding_changed_after_permission_prompt_does_not_redirect_call(self):
        self.bind_browser()
        self.command("mcp_save", server={"name": "second", "command": sys.executable})
        self.controls.handle({"cmd": "control_save", "kind": "browser",
                              "binding": {"server": "second", "enabled": True}})
        runtime = AgentRuntime(session_root=self.root / "sessions")
        with mock.patch.object(mcp, "Client") as client:
            result = runtime._execute_call("s", ToolCall("x", "control.call",
                    {"kind": "browser", "server": "browser", "tool": "echo"}, "external"))
            self.assertFalse(result["ok"])
            client.assert_not_called()

    def save_hook(self, hook_id, event, action="record", **extra):
        return self.hooks.handle({"cmd": "hook_save", "hook": {
            "id": hook_id, "event": event, "action": action, "tool": "*", "enabled": True, **extra}})

    def test_before_hook_blocks_even_authorized_tool_and_logs(self):
        self.assertTrue(self.save_hook("block", "before_tool", "block_tool", tool="research.create")["ok"])
        runtime = AgentRuntime(session_root=self.root / "sessions", permissions={"*": "allow"})
        with mock.patch.object(runtime, "_research_create") as handler:
            runtime._register_tools()
            result = runtime._execute_call("s", ToolCall("x", "research.create", {"topic": "test"}, "write"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "hook_blocked")
            handler.assert_not_called()
        self.assertTrue(self.hooks.handle({"cmd": "hook_list"})["logs"])
        self.preferences["hooks_enabled"] = False
        result = runtime._execute_call("s", ToolCall("y", "research.create", {"topic": "test"}, "write"))
        self.assertNotEqual(result.get("error"), "hook_blocked")

    def test_after_and_completed_hooks_record_actual_runtime_events(self):
        self.save_hook("after", "after_tool")
        self.save_hook("finish", "completed")
        runtime = AgentRuntime(session_root=self.root / "sessions")
        runtime._continue("s", [{"tool": "workspace.read", "arguments": {}}], 0)
        logs = self.hooks.handle({"cmd": "hook_list"})["logs"]
        self.assertEqual({row["event"] for row in logs}, {"after_tool", "completed"})
        self.assertTrue(next(row for row in logs if row["event"] == "after_tool")["tool_ok"])

    def test_after_hook_failure_preserves_successful_executed_result(self):
        runtime = AgentRuntime(session_root=self.root / "sessions")
        original = self.hooks.run
        def failing(event, context):
            if event == "after_tool":
                raise OSError("hook log unavailable")
            return original(event, context)
        with mock.patch.object(self.hooks, "run", side_effect=failing):
            result = runtime._execute_call("s", ToolCall("x", "research.create", {"topic": "kept"}, "write"))
        self.assertTrue(result["ok"])
        self.assertEqual(runtime.sessions.read("s")[-1]["payload"]["result"]["task_id"], result["task_id"])
        self.assertTrue(result["hook_warnings"])

    def test_hook_crud_rejects_executable_actions_and_persists_toggle(self):
        self.assertFalse(self.save_hook("bad", "after_tool", "shell", command="echo no")["ok"])
        self.assertFalse(self.save_hook("bad", "after_tool", "block_tool")["ok"])
        self.assertTrue(self.save_hook("record", "before_tool")["ok"])
        self.assertTrue(self.hooks.handle({"cmd": "hook_save", "hook": {"id": "record", "enabled": False}})["ok"])
        self.assertFalse(self.hooks.handle({"cmd": "hook_list"})["hooks"][0]["enabled"])
        self.assertTrue(self.hooks.handle({"cmd": "hook_delete", "id": "record"})["ok"])
        self.assertEqual(self.hooks.handle({"cmd": "hook_list"})["hooks"], [])

    def test_disabled_subagents_never_run_handlers(self):
        runtime = AgentRuntime(session_root=self.root / "sessions")
        self.preferences["subagents_enabled"] = False
        with mock.patch("src.agents.specialists.run") as run:
            self.assertFalse(runtime._subagent_spawn({"agent": "literature", "request": "test"})["ok"])
            self.assertFalse(runtime._subagent_fanout({"agents": ["literature"], "requests": ["test"]})["ok"])
            run.assert_not_called()

    def test_fanout_parallel_setting_keeps_all_requests_with_one_worker(self):
        runtime = AgentRuntime(session_root=self.root / "sessions")
        self.preferences["subagent_max_parallel"] = 1
        calls = []
        def run(name, request, **kwargs):
            calls.append((threading.get_ident(), request))
            return {"ok": True}
        with mock.patch("src.agents.specialists.run", side_effect=run):
            result = runtime._subagent_fanout({"__session_id": "s",
                                               "agents": ["literature"] * 3,
                                               "requests": ["one", "two", "three"]})
        self.assertTrue(result["ok"])
        self.assertEqual({request for _, request in calls}, {"one", "two", "three"})
        self.assertEqual(len({worker for worker, _ in calls}), 1)


if __name__ == "__main__":
    unittest.main()
