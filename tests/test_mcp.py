# -*- coding: utf-8 -*-
"""MCP 客户端单元测试：与内置假服务器子进程走完整 JSON-RPC 握手。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from src import mcp
from src.orchestrator.engine import ACTIONS, TaskContext

_SERVER = Path(__file__).parent / "fake_mcp_server.py"

# 子进程把看到的 GEZHI_* 环境与 server.env 回显给测试断言（stdio 最小环境白名单）。
_ENV_SERVER = r'''
import json, os, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if "id" not in req:
        continue
    method = req.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "env", "version": "1"}}
    elif method == "tools/call":
        payload = {
            "GEZHI_LLM_API_KEY": os.environ.get("GEZHI_LLM_API_KEY", "<missing>"),
            "PARENT_ONLY_SECRET": os.environ.get("PARENT_ONLY_SECRET", "<missing>"),
            "MCP_EXPLICIT_TOKEN": os.environ.get("MCP_EXPLICIT_TOKEN", "<missing>"),
            "PATH": os.environ.get("PATH", ""),
        }
        result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\n")
    sys.stdout.flush()
'''

# 服务器启动时派生一个长寿孙进程；close 后它必须被拥有的 Job/进程组回收。
_TREE_SERVER = r'''
import json, os, subprocess, sys
from pathlib import Path
work = Path(os.environ["MCP_TEST_DIR"])
child = subprocess.Popen([sys.executable, "-c",
    "import os, time; from pathlib import Path; time.sleep(8); "
    "Path(os.environ['MCP_TEST_DIR'], 'grandchild-finished').write_text('finished')"])
(work / "grandchild-started").write_text(str(child.pid))
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if "id" not in req:
        continue
    method = req.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "tree", "version": "1"}}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "tree-ok"}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\n")
    sys.stdout.flush()
'''


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

    def test_stdio_child_gets_minimal_env_and_explicit_server_env(self):
        """父进程 GEZHI_* 秘密不进入 MCP 子进程；server.env 显式变量仍然注入。"""
        script = Path(self._tmp.name) / "env_server.py"
        script.write_text(_ENV_SERVER, encoding="utf-8")
        server = {"name": "env", "command": sys.executable, "args": [str(script)],
                  "env": {"MCP_EXPLICIT_TOKEN": "explicit-value"}}
        with mock.patch.dict(os.environ, {
                "GEZHI_LLM_API_KEY": "sk-parent-secret",
                "PARENT_ONLY_SECRET": "parent-secret"}):
            cli = mcp.Client(server, timeout=15)
            try:
                cli.initialize()
                payload = json.loads(cli.call_tool("env", {}))
            finally:
                cli.close()
        self.assertEqual(payload["GEZHI_LLM_API_KEY"], "<missing>")
        self.assertEqual(payload["PARENT_ONLY_SECRET"], "<missing>")
        self.assertEqual(payload["MCP_EXPLICIT_TOKEN"], "explicit-value")
        self.assertTrue(payload["PATH"])

    def test_close_terminates_owned_descendant_tree(self):
        """stdio 子进程归属 Job/进程组：close 后 npx→node 一类后代不残留。"""
        import psutil  # 与 tests/test_process_owner.py 一样复用开发依赖

        work = Path(self._tmp.name) / "tree"
        work.mkdir()
        script = work / "tree_server.py"
        script.write_text(_TREE_SERVER, encoding="utf-8")
        server = {"name": "tree", "command": sys.executable, "args": [str(script)],
                  "env": {"MCP_TEST_DIR": str(work)}}
        cli = mcp.Client(server, timeout=15)
        try:
            cli.initialize()
            self.assertEqual(cli.call_tool("tree"), "tree-ok")
        finally:
            cli.close()
        started = work / "grandchild-started"
        self.assertTrue(started.exists())
        pid = int(started.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and psutil.pid_exists(pid):
            time.sleep(0.02)
        self.assertFalse(psutil.pid_exists(pid), "MCP close 后仍残留后代进程")
        self.assertFalse((work / "grandchild-finished").exists())


if __name__ == "__main__":
    unittest.main()
