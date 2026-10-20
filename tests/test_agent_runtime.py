import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.agent_runtime import AgentRuntime, ToolCall


class _GateRuntime(AgentRuntime):
    """固定计划的离线 runtime：用于验证工具执行入口的权限兜底与审批语义。"""

    def __init__(self, root, steps, *, permissions=None):
        super().__init__(llm_enabled=False, session_root=root, permissions=permissions)
        self.steps = steps
        self.calls = []

    def plan(self, request, mode="plan", agent="research"):
        return self._plan_payload(mode, request, self.steps, agent)

    def fixture_handler(self):
        def execute(args):
            self.calls.append(dict(args))
            return {"ok": True}
        return execute


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


class ExecuteCallPermissionTest(unittest.TestCase):
    """A/B：执行入口权限兜底与拒绝原因不被工具清单污染。"""

    def test_execute_call_denies_before_handler_and_logs_permission_denied(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d),
                                   permissions={"fixture.write": "deny"})
            handler = mock.Mock(return_value={"ok": True})
            runtime._register("fixture.write", "write", "fixture", handler)
            result = runtime._execute_call(
                "gate-denied", ToolCall("call-denied", "fixture.write", {}, "write"))
            self.assertEqual(result, {"ok": False, "error": "permission_denied",
                                      "executed": False})
            handler.assert_not_called()
            events = runtime.sessions.read("gate-denied")
            self.assertEqual([event["type"] for event in events], ["permission_denied"])
            self.assertEqual(events[0]["payload"]["tool_call_id"], "call-denied")
            self.assertEqual(events[0]["payload"]["tool"], "fixture.write")

    def test_execute_call_tier_denial_blocks_handler_and_names_required_tier(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            handler = mock.Mock(return_value={"ok": True})
            runtime._register("fixture.write", "write", "fixture", handler)
            with mock.patch("src.settings.load",
                            return_value={"agent_permission_tier": "read-only"}):
                result = runtime._execute_call(
                    "gate-tier", ToolCall("call-tier", "fixture.write", {}, "write"))
            self.assertEqual(result, {"ok": False, "error": "permission_denied",
                                      "executed": False})
            handler.assert_not_called()
            self.assertIn("只读", runtime._denial_reasons["gate-tier"])

    def test_approved_execution_does_not_prompt_again(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = _GateRuntime(
                Path(d), [{"tool": "fixture.write", "arguments": {"value": 1}}])
            runtime._register("fixture.write", "write", "fixture",
                              runtime.fixture_handler())
            waiting = runtime.start("需要审批的写入动作")
            self.assertEqual(waiting["status"], "waiting_approval")
            self.assertEqual(runtime.calls, [])
            done = runtime.approve(waiting["session_id"], waiting["pending"]["id"], "allow")
            self.assertEqual(done["status"], "done")
            self.assertEqual(len(runtime.calls), 1)
            events = runtime.sessions.read(waiting["session_id"])
            self.assertEqual(sum(event["type"] == "permission_requested"
                                 for event in events), 1)
            self.assertEqual(sum(event["type"] == "permission_granted"
                                 for event in events), 1)
            self.assertEqual(sum(event["type"] == "tool_call" for event in events), 1)

    def test_tools_schema_enumeration_does_not_pollute_denial_reason(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            runtime.policy.decisions["doctor.run"] = "deny"
            with mock.patch("src.settings.load",
                            return_value={"agent_permission_tier": "read-only"}):
                self.assertGreater(len(runtime.tools_schema("polluted")), 5)
                self.assertNotIn("polluted", runtime._denial_reasons)
                blocked = runtime._continue(
                    "polluted", [{"tool": "doctor.run", "arguments": {}}], 0)
            self.assertEqual(blocked["status"], "blocked")
            # 真实拒绝原因应来自当前策略，而不是 tools_schema 枚举时暂存的旧理由
            self.assertIn("已被策略禁用", blocked["reason"])
            self.assertNotIn("完全访问", blocked["reason"])

    def test_only_real_tier_denial_records_reason(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(llm_enabled=False, session_root=Path(d))
            with mock.patch("src.settings.load",
                            return_value={"agent_permission_tier": "read-only"}):
                runtime.tools_schema("reason-session")
                self.assertNotIn("reason-session", runtime._denial_reasons)
                decision = runtime._agent_gate(
                    "reason-session", "shell.run", "write", {"command": "echo hi"})
                self.assertEqual(decision, "deny")
                self.assertIn("完全访问", runtime._denial_reasons["reason-session"])
