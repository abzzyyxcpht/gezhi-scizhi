# -*- coding: utf-8 -*-
"""MCP 客户端单元测试：与内置假服务器子进程走完整 JSON-RPC 握手。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import mcp
from src.orchestrator.engine import ACTIONS, TaskContext

_SERVER = Path(__file__).parent / "fake_mcp_server.py"


class McpTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        cfg = Path(self._tmp.name) / "mcp_servers.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "fake", "command": __import__("sys").executable,
             "args": [str(_SERVER)], "autoload": True}]}), encoding="utf-8")
        self._patch = mock.patch.object(mcp, "CONFIG_PATH", cfg)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_load_servers(self):
        servers = mcp.load_servers()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["name"], "fake")

    def test_status_all_online(self):
        st = mcp.status_all(timeout=15)
        self.assertEqual(len(st), 1)
        self.assertTrue(st[0]["ok"], st[0].get("error"))
        self.assertEqual(st[0]["tools"][0]["name"], "echo")

    def test_call_tool_roundtrip(self):
        cli = mcp.Client({"name": "fake", "command": __import__("sys").executable,
                          "args": [str(_SERVER)]}, timeout=15)
        try:
            cli.initialize()
            out = cli.call_tool("echo", {"text": "格致"})
            self.assertIn("echo:格致", out)
        finally:
            cli.close()

    def test_register_actions_and_invoke(self):
        n = mcp.register_actions()
        self.assertGreaterEqual(n, 1)
        self.assertIn("mcp_fake_echo", ACTIONS)
        with tempfile.TemporaryDirectory() as td, \
                mock.patch("src.orchestrator.engine.CHECKPOINT_DIR", Path(td)):
            ctx = TaskContext("mcpinvoke", "research_pipeline", {"topic": "t"})
            res = ACTIONS["mcp_fake_echo"](ctx, {"arguments": {"text": "你好"}})
            self.assertIn("echo:你好", res["text"])

    def test_missing_command_reported(self):
        cfg = Path(self._tmp.name) / "bad.json"
        cfg.write_text(json.dumps({"servers": [
            {"name": "ghost", "command": "definitely-not-a-command-xyz"}]}),
            encoding="utf-8")
        with mock.patch.object(mcp, "CONFIG_PATH", cfg):
            st = mcp.status_all()
            self.assertFalse(st[0]["ok"])
            self.assertIn("命令不存在", st[0]["error"])


if __name__ == "__main__":
    unittest.main()
