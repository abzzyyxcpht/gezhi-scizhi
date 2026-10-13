import unittest

from src.bridge import handle


class AgentBridgeTest(unittest.TestCase):
    def test_agent_plan_bridge_returns_structured_steps(self):
        result = handle({"cmd": "agent_plan", "request": "调研大模型智能体"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["plan"]["steps"])
        self.assertEqual(result["plan"]["steps"][0]["tool"], "research.create")

    def test_sessions_bridge_returns_json_list(self):
        result = handle({"cmd": "sessions"})
        self.assertTrue(result["ok"])
        self.assertIsInstance(result["sessions"], list)
