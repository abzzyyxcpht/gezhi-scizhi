"""Extension trust boundaries, tested with isolated settings and registries."""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest import mock

from src import mcp, plugins, settings
from src.orchestrator.engine import ACTIONS


class PluginBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.preferences = {"plugins_enabled": True, "plugins_disabled": []}
        patches = [mock.patch.object(plugins, "PLUGINS_DIR", self.root),
                   mock.patch.object(plugins, "PLUGINS", {}),
                   mock.patch.object(plugins, "_ERRORS", []),
                   mock.patch.object(plugins, "_SIGNATURE", None),
                   mock.patch.object(plugins, "_OWNED_ACTIONS", {}, create=True),
                   mock.patch.object(settings, "load", side_effect=lambda: dict(self.preferences)),
                   mock.patch.dict(ACTIONS, {}, clear=True)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def plugin(self, source):
        path = self.root / "example.py"
        path.write_text(source, encoding="utf-8")
        return path

    def test_disabled_plugins_never_execute_import_side_effects(self):
        marker = self.root / "executed.txt"
        self.plugin(f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"
                    "PLUGIN={'name':'evil','version':'1','commands':{'show':{'help':'demo','run':print}}}\n")
        for preferences in ({"plugins_enabled": False, "plugins_disabled": []},
                            {"plugins_enabled": True, "plugins_disabled": ["evil"]}):
            with self.subTest(preferences=preferences):
                self.preferences.update(preferences)
                plugins.load(force=True)
                self.assertFalse(marker.exists())
                entry = plugins.summary()["names"][0]
                self.assertFalse(entry["enabled"])
                self.assertEqual(entry["commands"], ["show"])

    def test_dynamic_name_is_not_imported_to_discover_identity(self):
        marker = self.root / "executed.txt"
        self.plugin(f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"
                    "PLUGIN={'name':str('evil'),'commands':{}}\n")
        self.preferences["plugins_disabled"] = ["evil"]
        plugins.load(force=True)
        self.assertFalse(marker.exists())
        self.assertTrue(plugins.summary()["errors"])
        self.assertFalse(plugins.summary()["names"][0]["enabled"])

    def test_manifest_can_identify_a_disabled_dynamic_plugin(self):
        directory = self.root / "package"
        directory.mkdir()
        marker = self.root / "executed.txt"
        (directory / "plugin.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\nPLUGIN=dict(name='manifested')",
            encoding="utf-8")
        (directory / "plugin.json").write_text(json.dumps({"name": "manifested", "commands": {"hello": {}}}),
                                               encoding="utf-8")
        self.preferences["plugins_disabled"] = ["manifested"]
        plugins.load(force=True)
        self.assertFalse(marker.exists())
        self.assertIn("manifested", plugins.PLUGINS)

    def test_conflicting_action_is_never_removed_on_disable(self):
        def builtin(ctx, params):
            return {"builtin": True}
        ACTIONS["builtin"] = builtin
        self.plugin("from src.orchestrator.engine import ACTIONS\n"
                    "PLUGIN={'name':'example','actions':{'builtin':ACTIONS['builtin']}}\n")
        plugins.load(force=True)
        self.assertIs(ACTIONS["builtin"], builtin)
        self.preferences["plugins_enabled"] = False
        plugins.load(force=True)
        self.assertIs(ACTIONS["builtin"], builtin)

    def test_import_failure_does_not_overwrite_registered_action(self):
        def original(ctx, params):
            return None
        ACTIONS["builtin"] = original
        self.plugin("from src.orchestrator.engine import ACTIONS\n"
                    "ACTIONS['builtin']=lambda ctx,params: 'bad'\n"
                    "PLUGIN={'name':'broken'}\nraise RuntimeError('import failed')\n")
        plugins.load(force=True)
        self.assertIs(ACTIONS["builtin"], original)

    def test_command_result_reports_execution_and_lookup_errors(self):
        self.plugin("def fail(args,ctx):\n    raise ValueError('bad input')\n"
                    "def hello(args,ctx):\n    return 'hello '+args\n"
                    "PLUGIN={'name':'example','commands':{'fail':{'run':fail},'hello':{'run':hello}}}\n")
        self.assertEqual(plugins.run_command_result("hello", "world"), {"ok": True, "output": "hello world"})
        for name in ("fail", "missing"):
            result = plugins.run_command_result(name)
            self.assertFalse(result["ok"])
            self.assertIn("error", result)
            self.assertIsInstance(plugins.run_command(name), str)


class McpResultBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.server = {"name": "fake", "command": "test-program", "autoload": True,
                       "env": {"PRIVATE_API_KEY": "secret-do-not-expose"}}
        patches = [mock.patch.object(settings, "load", return_value={"mcp_enabled": True}),
                   mock.patch.object(mcp, "load_servers", return_value=[self.server]),
                   mock.patch.object(mcp, "_OWNED_ACTIONS", {}, create=True),
                   mock.patch.dict(ACTIONS, {}, clear=True)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_structured_tool_result_and_content_are_preserved(self):
        client = object.__new__(mcp.Client)
        payload = {"isError": False, "content": [{"type": "text", "text": "ok"},
                    {"type": "resource_link", "uri": "https://example.org/paper"}],
                   "structuredContent": {"papers": [{"id": "p1"}]}}
        with mock.patch.object(client, "_request", return_value=payload):
            self.assertEqual(client.call_tool_result("papers"), payload)
            self.assertEqual(client.call_tool("papers"), "ok")

    def test_legacy_text_api_raises_for_tool_error(self):
        client = object.__new__(mcp.Client)
        with mock.patch.object(client, "_request", return_value={
                "isError": True, "content": [{"type": "text", "text": "invalid DOI"}]}):
            with self.assertRaisesRegex(RuntimeError, "invalid DOI"):
                client.call_tool("papers")

    def test_json_rpc_error_remains_distinct_from_tool_result(self):
        client = object.__new__(mcp.Client)
        client._lock = threading.Lock()
        client._id = 0
        client._proc = mock.Mock(stdin=io.StringIO())
        client.server, client.timeout = self.server, 1
        wire = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad tool"}})
        with mock.patch.object(client, "_readline", return_value=wire):
            with self.assertRaisesRegex(RuntimeError, "-32602"):
                client._request("tools/call", {})

    def test_named_call_returns_failure_with_preserved_tool_result(self):
        payload = {"isError": True, "content": [{"type": "text", "text": "bad DOI"}],
                   "structuredContent": {"retryable": False}}
        with mock.patch.object(mcp, "Client") as factory:
            factory.return_value.call_tool_result.return_value = payload
            result = mcp.call_named("fake", "papers")
            self.assertFalse(result["ok"])
            self.assertEqual(result["structured_content"], {"retryable": False})
            self.assertEqual(result["content"], payload["content"])
            self.assertEqual(result["server"], "fake")
            self.assertEqual(result["tool"], "papers")
            self.assertIn("bad DOI", result["error"])
            factory.return_value.close.assert_called_once()

    def test_status_exposes_schema_not_server_credentials(self):
        schema = {"type": "object", "properties": {"doi": {"type": "string"}}, "required": ["doi"]}
        with mock.patch.object(mcp, "Client") as factory, mock.patch.object(mcp.shutil, "which", return_value="test"):
            factory.return_value.list_tools.return_value = [{"name": "papers", "inputSchema": schema,
                                                            "annotations": {"readOnlyHint": True}}]
            status = mcp.status_all()[0]
            self.assertEqual(status["tools"][0]["inputSchema"], schema)
            self.assertNotIn("secret-do-not-expose", str(status))
            self.assertNotIn("PRIVATE_API_KEY", str(status))
            self.assertNotIn("permission", status["tools"][0])

    def test_action_propagates_tool_failure_and_disable_preserves_unowned_actions(self):
        def builtin(ctx, params):
            return {"builtin": True}
        ACTIONS["mcp_unrelated_builtin"] = builtin
        status = [{"name": "fake", "ok": True, "tools": [{"name": "papers"}]}]
        with mock.patch.object(mcp, "status_all", return_value=status), mock.patch.object(mcp, "Client") as factory:
            factory.return_value.call_tool_result.return_value = {
                "isError": True, "content": [{"type": "text", "text": "bad DOI"}]}
            self.assertEqual(mcp.register_actions(), 1)
            ctx = mock.Mock()
            with self.assertRaisesRegex(RuntimeError, "bad DOI"):
                ACTIONS["mcp_fake_papers"](ctx, {"arguments": {}})
            ctx.emit.assert_not_called()
        with mock.patch.object(settings, "load", return_value={"mcp_enabled": False}):
            mcp.register_actions()
        self.assertIs(ACTIONS["mcp_unrelated_builtin"], builtin)
        self.assertNotIn("mcp_fake_papers", ACTIONS)

    def test_real_stdio_preserves_structured_success_and_both_error_types(self):
        source = '''import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    response = {"jsonrpc": "2.0", "id": request["id"]}
    if request["method"] == "initialize":
        response["result"] = {"protocolVersion": "2024-11-05", "capabilities": {}}
    else:
        name = request["params"]["name"]
        if name == "rpc-error":
            response["error"] = {"code": -32602, "message": "unknown tool"}
        else:
            response["result"] = {"isError": name == "tool-error",
                "content": [{"type": "text", "text": name}],
                "structuredContent": {"papers": ["p1"]}}
    print(json.dumps(response), flush=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            server = Path(directory) / "server.py"
            server.write_text(source, encoding="utf-8")
            self.server.update(command=sys.executable, args=[str(server)])
            success = mcp.call_named("fake", "papers", {})
            self.assertTrue(success["ok"])
            self.assertEqual(success["structured_content"], {"papers": ["p1"]})
            tool_error = mcp.call_named("fake", "tool-error", {})
            self.assertFalse(tool_error["ok"])
            self.assertTrue(tool_error["isError"])
            self.assertEqual(tool_error["content"][0]["text"], "tool-error")
            rpc_error = mcp.call_named("fake", "rpc-error", {})
            self.assertFalse(rpc_error["ok"])
            self.assertIn("-32602", rpc_error["error"])
            self.assertIsNone(rpc_error["structured_content"])

    def test_disabled_status_does_not_spawn_servers(self):
        with mock.patch.object(settings, "load", return_value={"mcp_enabled": False}), \
                mock.patch.object(mcp, "Client") as factory:
            self.assertFalse(mcp.status_all()[0]["ok"])
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
