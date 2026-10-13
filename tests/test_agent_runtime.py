import tempfile
import unittest
from pathlib import Path

from src.agent_runtime import AgentRuntime


class AgentRuntimeTest(unittest.TestCase):
    def test_offline_planner_creates_research_plan(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            plan = runtime.plan("调研大模型智能体并生成综述", "plan")
            self.assertEqual(
                [step["tool"] for step in plan["steps"]],
                ["research.create", "research.run", "research.export"],
            )

    def test_write_tool_waits_for_approval_and_approval_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            result = runtime.start("生成一份科研综述", mode="agent")
            self.assertEqual(result["status"], "waiting_approval")
            self.assertEqual(result["pending"]["capability"], "write")
            resumed = runtime.approve(result["session_id"],
                                      result["pending"]["id"], "allow")
            self.assertIn(resumed["status"], {"waiting_approval", "running", "done"})

    def test_permission_deny_is_logged_and_tool_not_called(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            result = runtime.start("导出研究产物", mode="agent")
            denied = runtime.approve(result["session_id"],
                                     result["pending"]["id"], "deny")
            events = runtime.sessions.read(result["session_id"])
            self.assertEqual(denied["status"], "blocked")
            self.assertTrue(any(e["type"] == "permission_denied" for e in events))

    def test_pending_approval_recovers_in_new_runtime_process(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first = AgentRuntime(session_root=root)
            result = first.start("调研大模型智能体", mode="agent")
            second = AgentRuntime(session_root=root)
            resumed = second.approve(result["session_id"],
                                     result["pending"]["id"], "deny")
            self.assertEqual(resumed["status"], "blocked")

    def test_offline_agent_executes_research_pipeline_after_approvals(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(session_root=Path(d))
            result = runtime.start("调研大模型智能体", mode="agent")
            for _ in range(3):
                if result["status"] != "waiting_approval":
                    break
                result = runtime.approve(result["session_id"],
                                          result["pending"]["id"],
                                          "allow_session")
            self.assertEqual(result["status"], "done")
            self.assertGreater(result["events"][-1]["payload"].get("status", ""), "")
