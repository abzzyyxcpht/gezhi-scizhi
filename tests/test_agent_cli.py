import tempfile
import unittest
from pathlib import Path

from scripts.gezhi import parse_args
from src.agent_runtime import AgentRuntime


class AgentCliTest(unittest.TestCase):
    def test_agent_parser_supports_mode_and_request(self):
        args = parse_args(["agent", "调研大模型智能体", "--mode", "plan"])
        self.assertEqual(args.command, "agent")
        self.assertEqual(args.mode, "plan")
        self.assertEqual(args.request, "调研大模型智能体")

    def test_offline_agent_invocation_returns_plan(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(session_root=Path(d))
            result = runtime.start("调研大模型智能体", mode="plan")
            self.assertEqual(result["status"], "planned")
            self.assertTrue(result["plan"]["steps"])
