"""Independent adaptive-driver boundary regressions, using only temporary state."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import llm, model_registry, prompts, service, settings
from src.agent_driver import AdaptiveDriver
from src.agent_runtime import AgentRuntime


def response(text="", calls=(), private=""):
    calls = [{"id": cid, "name": name, "arguments": arguments} for cid, name, arguments in calls]
    assistant = {"role": "assistant", "content": text,
                 "tool_calls": [{"id": c["id"], "type": "function", "function": {
                     "name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in calls]}
    if private:
        assistant["_provider_state"] = {"encrypted_content": private}
    return {"content": text, "tool_calls": calls, "assistant_message": assistant,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "usage_reported": True}}


class AdaptiveHardeningTest(unittest.TestCase):
    def test_session_grant_for_one_mcp_tool_does_not_authorize_another(self):
        # C2 三档门后：full-access 工具需 full-access 档位才进入审批流（档位拒绝有专测）
        (self.root / "settings.json").write_text(
            json.dumps({"agent_permission_tier": "full-access"}), encoding="utf-8")
        runtime = self.runtime()
        handler = mock.Mock(return_value={"ok": True})
        runtime._register("mcp.call", "external", "MCP", handler,
                          runtime._obj_schema({"server": {"type": "string"}, "tool": {"type": "string"}}, ["server", "tool"]))
        with mock.patch.object(llm, "chat", side_effect=[response(calls=[
                ("m1", "mcp.call", {"server": "papers", "tool": "search"}),
                ("m2", "mcp.call", {"server": "compute", "tool": "execute"})]), response("done")]):
            pending = runtime.start("Use two external tools")
            result = runtime.approve(pending["session_id"], "m1", "allow_session")
        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(result["pending"]["id"], "m2")
        self.assertEqual(handler.call_count, 1)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = {"connection_id": "connection", "profile_id": "profile", "model": "model",
                        "protocol": "responses", "base_url": "https://first.example/v1",
                        "thinking_style": "auto", "reasoning_effort": "auto", "thinking_budget": 4096,
                        "context_window": 32768, "max_output_tokens": 8192, "api_key": "test-key"}
        for patch in (mock.patch.object(settings, "_PATH", self.root / "settings.json"),
                      mock.patch.object(llm, "available", return_value=True),
                      mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(self.profile)),
                      mock.patch.object(prompts, "agent_system", return_value="Test system.")):
            patch.start()
            self.addCleanup(patch.stop)

    def runtime(self, permissions=None):
        return AgentRuntime(offline=False, llm_enabled=True, session_root=self.root, permissions=permissions)

    @staticmethod
    def register(runtime, name, handler, capability="read"):
        runtime._register(name, capability, name, handler, runtime._obj_schema({}))

    def test_multitool_batch_requires_each_approval_and_preserves_observations(self):
        runtime = self.runtime()
        first, second = mock.Mock(return_value={"ok": True}), mock.Mock(return_value={"ok": True})
        self.register(runtime, "test.first", first, "write")
        self.register(runtime, "test.second", second, "write")
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("one", "test.first", {}), ("two", "test.second", {})]), response("done")]) as chat:
            waiting = runtime.start("Run both writes")
            self.assertEqual(waiting["pending"]["id"], "one")
            first.assert_not_called()
            waiting = runtime.approve(waiting["session_id"], "one", "allow")
            self.assertEqual(waiting["pending"]["id"], "two")
            first.assert_called_once()
            second.assert_not_called()
            done = runtime.approve(waiting["session_id"], "two", "allow")
            self.assertEqual(done["status"], "done")
            results = [m["tool_call_id"] for m in chat.call_args.args[0] if m["role"] == "tool"]
            self.assertEqual(results, ["one", "two"])
            second.assert_called_once()

    def test_pending_approval_must_honor_a_new_explicit_deny_policy(self):
        first = self.runtime()
        self.register(first, "test.write", mock.Mock(return_value={"ok": True}), "write")
        with mock.patch.object(llm, "chat", return_value=response(calls=[("write", "test.write", {})])):
            waiting = first.start("Write once")
        restored = self.runtime(permissions={"test.write": "deny"})
        writer = mock.Mock(return_value={"ok": True})
        self.register(restored, "test.write", writer, "write")
        with mock.patch.object(llm, "chat", return_value=response("done")):
            result = restored.approve(waiting["session_id"], "write", "allow")
        writer.assert_not_called()
        self.assertIn(result["status"], {"blocked", "error"})

    def test_cancel_during_task_launch_is_honored_at_tool_boundary(self):
        runtime = self.runtime()
        def launch(arguments):
            runtime.cancel_session(arguments["__session_id"])
            return {"ok": True, "status": "running", "task_id": "launched-task"}
        self.register(runtime, "test.launch", launch)
        with mock.patch.object(llm, "chat", return_value=response(calls=[("launch", "test.launch", {})])):
            result = runtime.start("Launch and cancel")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(runtime.sessions.snapshot(result["session_id"])["status"], "cancelled")

    def test_same_connection_endpoint_change_does_not_replay_private_history(self):
        runtime = self.runtime()
        self.register(runtime, "test.write", mock.Mock(return_value={"ok": True}), "write")
        with mock.patch.object(llm, "chat", return_value=response(
                calls=[("write", "test.write", {})], private="opaque-provider-state")):
            waiting = runtime.start("Prepare work")
        self.profile["base_url"] = "https://different.example/v1"
        with mock.patch.object(llm, "chat", return_value=response("done")) as chat:
            result = runtime.approve(waiting["session_id"], "write", "allow")
        self.assertEqual(chat.call_count, 0)
        self.assertEqual(result["reason"], "model_changed")

    def test_durable_completion_wins_over_stale_driver_state_after_save_failure(self):
        runtime = self.runtime()
        save = AdaptiveDriver._save
        def interrupted_save(driver, state):
            if state["status"] == "done":
                raise OSError("simulated crash between journal and driver snapshot")
            save(driver, state)
        with mock.patch.object(AdaptiveDriver, "_save", new=interrupted_save), \
                mock.patch.object(llm, "chat", return_value=response("done")):
            with self.assertRaises(OSError):
                runtime.start("Finish without tools")
        sid = next(self.root.glob("*.driver.json")).name.removesuffix(".driver.json")
        self.assertEqual(runtime.sessions.snapshot(sid)["status"], "done")
        restored = self.runtime()
        with mock.patch.object(llm, "chat", return_value=response("duplicated")) as chat:
            try:
                restored.continue_session(sid)
            except ValueError:
                pass
        self.assertEqual(chat.call_count, 0)

    def test_inflight_side_effect_is_not_replayed_after_restart(self):
        runtime = self.runtime(permissions={"test.write": "allow"})
        marker = self.root / "effects.txt"
        def interrupted_write(arguments):
            marker.write_text("once", encoding="utf-8")
            raise SystemExit("simulated process interruption")
        self.register(runtime, "test.write", interrupted_write, "write")
        with mock.patch.object(llm, "chat", return_value=response(calls=[("write", "test.write", {})])):
            with self.assertRaises(SystemExit):
                runtime.start("Write")
        sid = next(self.root.glob("*.driver.json")).name.removesuffix(".driver.json")
        restored = self.runtime(permissions={"test.write": "allow"})
        writer = mock.Mock(return_value={"ok": True})
        self.register(restored, "test.write", writer, "write")
        # 中断对账保留未知执行状态；文字说明不能证明副作用已完成。
        with mock.patch.object(llm, "chat", return_value=response("已恢复并说明情况")) as chat:
            result = restored.continue_session(sid)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "unresolved_tool_errors")
        writer.assert_not_called()
        self.assertEqual(chat.call_count, 1)
        state = json.loads(AdaptiveDriver(restored).path(sid).read_text(encoding="utf-8"))
        self.assertTrue(any(m["role"] == "user" and "工具执行反馈" in str(m.get("content")) for m in state["messages"]))
        self.assertEqual(marker.read_text(encoding="utf-8"), "once")

    def test_repaired_tool_error_updates_public_projection_to_done(self):
        runtime = self.runtime()
        handler = mock.Mock(side_effect=[{"ok": False, "error": "temporary"}, {"ok": True}])
        self.register(runtime, "test.retry", handler)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("try-one", "test.retry", {})]),
                response(calls=[("try-two", "test.retry", {})]), response("done")]):
            result = runtime.start("Retry transient failure")
        self.assertEqual(result["status"], "done")
        self.assertEqual(runtime.sessions.snapshot(result["session_id"])["status"], "done")

    def test_provider_private_state_stays_out_of_public_events_and_snapshots(self):
        runtime = self.runtime()
        with mock.patch.object(llm, "chat", return_value=response("Public answer", private="OPAQUE-SECRET")):
            result = runtime.start("Answer")
        sid = result["session_id"]
        public = json.dumps([result, runtime.sessions.read(sid), runtime.sessions.snapshot(sid)])
        self.assertNotIn("OPAQUE-SECRET", public)
        self.assertIn("OPAQUE-SECRET", (self.root / f"{sid}.driver.json").read_text(encoding="utf-8"))

    def test_over_token_budget_blocks_the_newly_requested_tool(self):
        settings.save({"agent_token_budget": 1024})
        runtime = self.runtime()
        handler = mock.Mock(return_value={"ok": True})
        self.register(runtime, "test.effect", handler)
        answer = response(calls=[("effect", "test.effect", {})])
        answer["usage"] = {"prompt_tokens": 1000, "completion_tokens": 25, "usage_reported": True}
        with mock.patch.object(llm, "chat", return_value=answer) as chat:
            result = runtime.start("Bounded work")
        self.assertEqual(result["reason"], "token_budget")
        self.assertEqual(result["status"], "error")
        self.assertEqual(chat.call_count, 1)
        handler.assert_not_called()

    def test_missing_usage_is_reported_as_incomplete_accounting(self):
        runtime = self.runtime()
        answer = response("Answer without usage")
        answer["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "usage_reported": False}
        with mock.patch.object(llm, "chat", return_value=answer):
            result = runtime.start("Bounded work")
        self.assertFalse(result["usage_complete"])

    def test_cancel_flag_stops_waiting_task_resume_without_polling(self):
        runtime = self.runtime()
        self.register(runtime, "test.launch", lambda _: {"ok": True, "status": "running", "task_id": "task"})
        with mock.patch.object(llm, "chat", return_value=response(calls=[("launch", "test.launch", {})])):
            waiting = runtime.start("Launch")
        sid = waiting["session_id"]
        # A process can stop after the cancellation flag is durable but before finalization.
        (self.root / f"{sid}.cancel").touch()
        with mock.patch.object(service, "get", return_value=mock.Mock(status="running", task_id="task")):
            result = self.runtime().continue_session(sid)
        self.assertEqual(result["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
