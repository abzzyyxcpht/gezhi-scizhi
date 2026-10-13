from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import llm, settings
from src.agent_runtime import AgentRuntime
from src.agent_driver import AdaptiveDriver
from tests.test_driver_resilience import driver_state


def response(text="", tool=None, call_id="call-1", args=None):
    calls = [{"id": call_id, "name": tool, "arguments": args or {}}] if tool else []
    return {"content": text, "tool_calls": calls, "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "assistant_message": {"role": "assistant", "content": text,
                                  "tool_calls": [{"id": c["id"], "type": "function", "function": {
                                      "name": c["name"], "arguments": "{}"}} for c in calls]}}


class AdaptiveDriverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.object(settings, "_PATH", self.root / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(llm, "available", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def runtime(self):
        return AgentRuntime(offline=False, llm_enabled=True, session_root=self.root)

    def test_tool_observation_is_sent_into_the_next_model_request(self):
        runtime = self.runtime()
        with mock.patch.object(llm, "chat", side_effect=[response(tool="workspace.read"), response("已读取工作区")]) as chat:
            result = runtime.start("先读工作区，再汇报")
        self.assertEqual(result["status"], "done")
        self.assertEqual(chat.call_count, 2)
        messages = chat.call_args_list[1].args[0]
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["tool_call_id"], "call-1")
        self.assertIn("pool_count", messages[-1]["content"])
        self.assertEqual(result["content"], "已读取工作区")

    def test_pending_tool_restores_workspace_in_a_new_runtime_without_replay(self):
        first = self.runtime()
        def seed(args):
            first._workspaces[args["__session_id"]]["evidence_marker"] = "retained"
            return {"ok": True}
        first._register("test.seed", "read", "Seed research evidence", seed, first._obj_schema({}))
        first._register("test.write", "write", "Write result", lambda args: {"ok": True}, first._obj_schema({}))
        with mock.patch.object(llm, "chat", side_effect=[response(tool="test.seed"), response(tool="test.write", call_id="write-2")]):
            pending = first.start("读取后写入")
        self.assertEqual(pending["status"], "waiting_approval")
        second = self.runtime()
        writer = mock.Mock(return_value={"ok": True})
        second._register("test.write", "write", "Write result", writer, second._obj_schema({}))
        with mock.patch.object(llm, "chat", return_value=response("已完成")):
            result = second.approve(pending["session_id"], "write-2", "allow")
        self.assertEqual(result["status"], "done")
        self.assertEqual(second._workspaces[pending["session_id"]]["evidence_marker"], "retained")
        writer.assert_called_once()
        with self.assertRaises(ValueError):
            second.approve(pending["session_id"], "write-2", "allow")

    def test_failed_tool_receives_feedback_but_prose_does_not_prove_completion(self):
        runtime = self.runtime()
        runtime._register("test.fail", "read", "Failure", lambda _: {"ok": False, "error": "missing evidence"}, runtime._obj_schema({}))
        with mock.patch.object(llm, "chat", side_effect=[response(tool="test.fail"), response("已根据反馈说明情况")]) as chat:
            result = runtime.start("核对证据")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "unresolved_tool_errors")
        feedback = chat.call_args_list[1].args[0]
        self.assertTrue(any(m["role"] == "user" and "工具执行反馈" in str(m.get("content"))
                            and "missing evidence" in str(m.get("content")) for m in feedback))

    def test_model_step_budget_stops_repeated_tool_requests(self):
        settings.save({"agent_max_steps": 1})
        with mock.patch.object(llm, "chat", return_value=response(tool="workspace.read")) as chat:
            result = self.runtime().start("重复读取")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "step_budget")
        self.assertEqual(chat.call_count, 1)

    def test_only_role_permitted_tools_are_exposed_to_model(self):
        with mock.patch.object(llm, "chat", return_value=response("目前没有证据")) as chat:
            result = self.runtime().start("核对证据", agent="critic")
        exposed = {t["function"]["name"] for t in chat.call_args.kwargs["tools"]}
        self.assertIn("critic.run", exposed)
        self.assertNotIn("research.create", exposed)
        self.assertEqual(result["status"], "done")

    def test_cancel_during_model_response_prevents_success_and_tools(self):
        runtime = self.runtime()
        current = {}
        def answer(*args, **kwargs):
            runtime.cancel_session(current["sid"])
            return response("迟到的最终输出")
        with mock.patch.object(llm, "chat", side_effect=answer):
            result = runtime.start("读取", on_started=lambda sid: current.update(sid=sid))
        self.assertEqual(result["status"], "cancelled")

    def test_cancel_between_seal_and_completion_cannot_be_overwritten(self):
        """A late completion must not turn a persisted cancellation into done."""
        runtime = self.runtime()

        def seal_then_cancel(driver, state, status):
            self.assertEqual(status, "done")
            runtime.cancel_session(state["session_id"])
            return True

        with mock.patch("src.session_steering.seal", side_effect=seal_then_cancel):
            with mock.patch.object(llm, "chat", return_value=response("迟到的最终输出")):
                result = runtime.start("读取")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(runtime.sessions.snapshot(result["session_id"])["status"], "cancelled")
        events = runtime.sessions.read(result["session_id"])
        self.assertTrue(any(event["type"] == "cancel_requested" for event in events))
        self.assertEqual(events[-1]["payload"]["status"], "cancelled")

    def test_committed_terminal_cannot_be_replaced_by_late_driver_finish(self):
        for committed, requested in (("partial", "done"), ("cancelled", "done"),
                                     ("done", "cancelled"), ("done", "error"), ("error", "done")):
            with self.subTest(committed=committed, requested=requested):
                runtime = self.runtime()
                sid = f"canonical-{committed}-{requested}"
                runtime.sessions.append(sid, "user_message", {"text": "Fixture", "mode": "agent"})
                payload = {"reason": "first_reason", "message": "first_message"}
                runtime._publish_terminal(sid, committed, payload)
                before = runtime.sessions.read(sid)
                state = driver_state(runtime, sid, [{"role": "system", "content": "Fixture"},
                    {"role": "user", "content": "Fixture"}])
                result = AdaptiveDriver(runtime)._finish(state, requested, "late_reason", "late_error")
                self.assertEqual(result["status"], committed)
                self.assertEqual(result["reason"], "first_reason")
                self.assertEqual(result["error"], "first_message" if committed == "error" else "")
                self.assertEqual(runtime.sessions.read(sid), before)
                saved = json.loads(AdaptiveDriver(runtime).path(sid).read_text(encoding="utf-8"))
                self.assertEqual(saved["status"], committed)
                self.assertIsNone(saved["pending"])

    def test_runtime_terminal_published_during_seal_wins_driver_finish(self):
        runtime = self.runtime()

        def complete_elsewhere(driver, state, status):
            runtime._publish_terminal(state["session_id"], "partial", {"reason": "branches_partial"})
            return True

        with mock.patch("src.session_steering.seal", side_effect=complete_elsewhere):
            with mock.patch.object(llm, "chat", return_value=response("Late final message")):
                result = runtime.start("Fixture")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["reason"], "branches_partial")
        self.assertEqual(result["error"], "")
        finals = [event for event in runtime.sessions.read(result["session_id"]) if event["type"] in {"completed", "error"}]
        self.assertEqual(len(finals), 1)

    def test_pending_inbox_cannot_restart_an_already_completed_turn(self):
        runtime = self.runtime()
        sid = "completed-with-pending"
        runtime.sessions.append(sid, "user_message", {"text": "Fixture", "mode": "agent"})
        runtime._publish_terminal(sid, "partial", {"reason": "first_reason"})
        state = driver_state(runtime, sid, [{"role": "system", "content": "Fixture"},
            {"role": "user", "content": "Fixture"}])
        with mock.patch("src.session_steering.seal", return_value=False):
            with mock.patch.object(llm, "chat", return_value=response("Must not run")) as model:
                result = AdaptiveDriver(runtime)._finish(state, "done")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["reason"], "first_reason")
        model.assert_not_called()
        self.assertEqual(len([event for event in runtime.sessions.read(sid) if event["type"] in {"completed", "error"}]), 1)

    def test_cancel_pending_approval_is_terminal_without_executing_tool(self):
        runtime = self.runtime()
        writer = mock.Mock(return_value={"ok": True})
        runtime._register("test.write", "write", "Write", writer, runtime._obj_schema({}))
        with mock.patch.object(llm, "chat", return_value=response(tool="test.write")):
            pending = runtime.start("write")
        result = runtime.cancel_session(pending["session_id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(runtime.sessions.snapshot(pending["session_id"])["status"], "cancelled")
        writer.assert_not_called()

    def test_unknown_tool_name_receives_feedback_and_recovers(self):
        """幻觉工具名不再直接判死：错误回喂模型自我修正（真实 LLM 兼容性）。"""
        runtime = self.runtime()
        bad = response(tool="bogus_tool_abcdef0123456789")
        recover = response(tool="workspace.read", call_id="call-2")
        with mock.patch.object(llm, "chat", side_effect=[bad, recover, response("已改用可用工具完成任务")]) as chat:
            result = runtime.start("检索文献")
        self.assertEqual(result["status"], "done")
        feedback = chat.call_args_list[1].args[0]
        self.assertTrue(any(m["role"] == "tool" and "未知工具" in str(m.get("content"))
                            for m in feedback))

    def test_hallucinated_tool_name_suffix_is_normalized(self):
        """glm 系模型会把 workspace.read 发成 workspace_read_<16位hex>：归一化后正常执行。"""
        runtime = self.runtime()
        mangled = response(tool="workspace_read_0123456789abcdef", call_id="call-m")
        with mock.patch.object(llm, "chat", side_effect=[mangled, response("读取完成")]) as chat:
            result = runtime.start("读取工作区")
        self.assertEqual(result["status"], "done")
        feedback = chat.call_args_list[1].args[0]
        self.assertTrue(any(m["role"] == "tool" and m.get("name") == "workspace.read"
                            for m in feedback))

    def test_unknown_tool_repair_exhaustion_still_fails(self):
        """持续幻觉时修复轮数用尽仍以错误终止（自愈有界）。"""
        runtime = self.runtime()
        replies = [response(tool="nope.run", call_id=f"bad-{i}") for i in range(20)]
        with mock.patch.object(llm, "chat", side_effect=replies):
            result = runtime.start("检索文献")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "step_budget")
