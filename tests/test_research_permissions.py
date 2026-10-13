# -*- coding: utf-8 -*-
"""科研权限三档门：归类、档位判定、运行期拦截与 /policy 路由。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import feature_commands
from src.agent_runtime import AgentRuntime
from src.bridge import handle
from src.research_permissions import (DEFAULT_TIER, TIERS, classify, exceeds,
                                      gate, matrix, normalize_tier, render_text)


def _settings(**overrides):
    data = {"agent_permission_tier": DEFAULT_TIER}
    data.update(overrides)
    return data


class ClassifyTest(unittest.TestCase):
    def test_shell_and_control_are_code_risk_full_access(self):
        for tool in ("shell.run", "control.call", "mcp.call"):
            action = classify(tool, "write", {"command": "ls", "path": "/x"})
            self.assertEqual(action["tier"], "full-access", tool)
            self.assertEqual(action["risk"], "code", tool)
            self.assertFalse(action["reversible"], tool)

    def test_workspace_writes_are_data_risk_with_target_detail(self):
        action = classify("file.write", "write", {"path": "notes/实验.md"})
        self.assertEqual((action["tier"], action["risk"]), ("workspace-write", "data"))
        self.assertIn("notes/实验.md", action["target"])
        self.assertTrue(action["reversible"])

    def test_retrieval_is_citation_risk_read_only(self):
        for tool in ("research.run", "subagent.fanout", "research.status"):
            action = classify(tool, "network", {})
            self.assertEqual((action["tier"], action["risk"]), ("read-only", "citation"), tool)

    def test_unknown_tool_falls_back_conservatively(self):
        action = classify("mystery.tool", "external", {})
        self.assertEqual(action["tier"], "workspace-write")
        self.assertFalse(action["reversible"])


class TierMathTest(unittest.TestCase):
    def test_matrix_blocks_match_tier_order(self):
        rows = {row["tier"]: row["risks"] for row in matrix()}
        self.assertEqual(rows["read-only"], {"citation": True, "data": False, "code": False})
        self.assertEqual(rows["workspace-write"],
                         {"citation": True, "data": True, "code": False})
        self.assertEqual(rows["full-access"], {"citation": True, "data": True, "code": True})

    def test_exceeds_is_strict(self):
        self.assertTrue(exceeds("full-access", "workspace-write"))
        self.assertTrue(exceeds("workspace-write", "read-only"))
        self.assertFalse(exceeds("workspace-write", "workspace-write"))
        self.assertFalse(exceeds("read-only", "full-access"))

    def test_normalize_rejects_unknown_tier(self):
        self.assertEqual(normalize_tier("full-access"), "full-access")
        self.assertEqual(normalize_tier(" nonsense "), DEFAULT_TIER)
        self.assertEqual(normalize_tier(None), DEFAULT_TIER)

    def test_gate_asks_when_tier_covers_action(self):
        verdict, action = gate("file.write", "write", {"path": "a.md"}, "workspace-write")
        self.assertEqual(verdict, "ask")
        self.assertNotIn("denied_because", action)

    def test_gate_denies_with_actionable_reason(self):
        verdict, action = gate("shell.run", "write", {"command": "rm -rf x"},
                               "workspace-write")
        self.assertEqual(verdict, "deny")
        self.assertIn("完全访问", action["denied_because"])
        self.assertIn("代码", action["denied_because"])

    def test_render_text_lists_matrix_and_boundary(self):
        text = render_text("read-only")
        self.assertIn("只读", text)
        self.assertIn("引用:放行", text.replace("：", ":"))
        self.assertIn("代码:拦截", text.replace("：", ":"))


class RuntimeGateTest(unittest.TestCase):
    def test_read_only_tier_blocks_first_write_step_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            with mock.patch("src.settings.load",
                            return_value=_settings(agent_permission_tier="read-only")):
                result = runtime.start("导出研究产物", mode="agent")
            self.assertEqual(result["status"], "blocked")
            self.assertIn("只读", result["reason"])
            self.assertIn("数据", result["reason"])

    def test_default_tier_asks_and_pending_carries_action_preview(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            result = runtime.start("生成一份科研综述", mode="agent")
            self.assertEqual(result["status"], "waiting_approval")
            action = result["pending"]["action"]
            self.assertEqual(action["risk"], "data")
            self.assertTrue(action["target"])
            self.assertIn(action["tier"], TIERS)

    def test_shell_denied_in_workspace_write_tier_records_reason(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            with mock.patch("src.settings.load",
                            return_value=_settings(agent_permission_tier="workspace-write")):
                decision = runtime._agent_gate("sid-x", "shell.run", "write",
                                               {"command": "echo hi"})
            self.assertEqual(decision, "deny")
            self.assertIn("完全访问", runtime._denial_reasons["sid-x"])

    def test_full_access_tier_lets_shell_reach_policy(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            with mock.patch("src.settings.load",
                            return_value=_settings(agent_permission_tier="full-access")):
                decision = runtime._agent_gate("sid-y", "shell.run", "write",
                                               {"command": "echo hi"})
            self.assertEqual(decision, "ask")  # 档位放行后仍按策略每次询问


class BridgeAndCommandTest(unittest.TestCase):
    def test_bridge_get_returns_matrix_and_text(self):
        result = handle({"cmd": "agent_policy"})
        self.assertTrue(result["ok"])
        self.assertIn(result["tier"], TIERS)
        self.assertEqual(len(result["matrix"]), 3)
        self.assertIn("科研权限档位", result["text"])

    def test_bridge_set_tier_persists_and_rejects_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("src.settings._PATH", Path(d) / "settings.json"):
                saved = handle({"cmd": "agent_policy", "tier": "read-only"})
                self.assertTrue(saved["ok"])
                self.assertEqual(saved["tier"], "read-only")
                again = handle({"cmd": "agent_policy"})
                self.assertEqual(again["tier"], "read-only")
            bad = handle({"cmd": "agent_policy", "tier": "root"})
            self.assertFalse(bad["ok"])

    def test_policy_slash_command_routes(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("src.settings._PATH", Path(d) / "settings.json"):
                result = feature_commands.execute("policy", "read-only")
                self.assertTrue(result["ok"], result.get("error"))
                self.assertEqual(result["tier"], "read-only")
                self.assertIn("只读", result["text"])

    def test_policy_listed_in_catalog(self):
        self.assertIn("policy", {item[0] for item in feature_commands.CATALOG})


if __name__ == "__main__":
    unittest.main()
