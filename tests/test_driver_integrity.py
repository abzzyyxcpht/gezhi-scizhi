"""Regression tests for truthful recovery, preserved context and inclusive budgets."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import llm, model_registry, prompts, service, settings
from src.agent_driver import AdaptiveDriver, COMPRESS_SUMMARY_MAX_CHARS
from src.agent_runtime import AgentRuntime
from tests.test_driver_resilience import driver_state, response


class DriverIntegrityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = {"connection_id": "connection", "profile_id": "profile", "model": "model",
                        "protocol": "responses", "base_url": "https://fixture.invalid/v1",
                        "thinking_style": "auto", "reasoning_effort": "auto", "thinking_budget": 4096,
                        "context_window": 32768, "max_output_tokens": 8192, "api_key": "fixture"}
        for patch in (mock.patch.object(settings, "_PATH", self.root / "settings.json"),
                      mock.patch.object(llm, "available", return_value=True),
                      mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(self.profile)),
                      mock.patch.object(prompts, "agent_system", return_value="Stable system.")):
            patch.start()
            self.addCleanup(patch.stop)
        self.runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=self.root)
        self.driver = AdaptiveDriver(self.runtime)

    def register(self, handler):
        self.runtime._register("test.read", "read", "Fixture", handler,
                               self.runtime._obj_schema({"path": {"type": "string"}}))

    def history(self):
        messages = [{"role": "system", "content": "Stable system."},
                    {"role": "user", "content": "原始任务" + "甲" * 220 + "必须保留最后约束"}]
        for i in range(3):
            answer = response(calls=[(f"c{i}", "test.read", {"path": str(i)})])["assistant_message"]
            answer["_provider_state"] = {"protocol": "responses", "opaque": f"SECRET-{i}"}
            messages.extend([answer, {"role": "tool", "tool_call_id": f"c{i}",
                "name": "test.read", "content": json.dumps({"ok": True, "path": str(i)})}])
        return messages

    def test_prose_cannot_resolve_a_failed_tool(self):
        self.register(lambda _: {"ok": False, "error": "missing evidence"})
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("failed", "test.read", {})]), response("Everything completed")]):
            result = self.runtime.start("Validate evidence")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "unresolved_tool_errors")

    def test_success_for_different_arguments_does_not_erase_failure(self):
        self.register(lambda args: {"ok": args.get("path") == "B", "error": "missing A"})
        with mock.patch.object(llm, "chat", side_effect=[response(calls=[
                ("A", "test.read", {"path": "A"}), ("B", "test.read", {"path": "B"})]),
                response("Both files verified")]):
            result = self.runtime.start("Read files A and B")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "unresolved_tool_errors")

    def test_parallel_crash_after_first_observation_never_reexecutes_second(self):
        handler = mock.Mock(return_value={"ok": True})
        self.register(handler)
        save = AdaptiveDriver._save
        crashed = False

        def interrupt(driver, state):
            nonlocal crashed
            save(driver, state)
            if not crashed and len([m for m in state["messages"] if m["role"] == "tool"]) == 1:
                crashed = True
                raise SystemExit("crash after first observation persisted")

        with mock.patch.object(AdaptiveDriver, "_save", new=interrupt), mock.patch.object(
                llm, "chat", return_value=response(calls=[("A", "test.read", {}), ("B", "test.read", {})])):
            with self.assertRaises(SystemExit):
                self.runtime.start("Read twice")
        sid = next(self.root.glob("*.driver.json")).name.removesuffix(".driver.json")
        with mock.patch.object(llm, "chat", return_value=response("Verified")):
            result = self.runtime.continue_session(sid)
        self.assertEqual(result["status"], "done")
        self.assertEqual(handler.call_count, 2)
        saved = json.loads(self.driver.path(sid).read_text(encoding="utf-8"))
        self.assertEqual([m["tool_call_id"] for m in saved["messages"] if m["role"] == "tool"], ["A", "B"])

    def test_compression_keeps_original_prefix_and_native_tail_across_rounds(self):
        original = self.history()
        state = driver_state(self.runtime, "prefix01", copy.deepcopy(original))
        with mock.patch.object(llm, "chat", return_value=response("A compact verified history")):
            self.assertTrue(self.driver._compress_context(state, self.profile))
            self.assertEqual(state["messages"][:2], original[:2])
            self.assertEqual(state["messages"][-2:], original[-2:])
            state["messages"].extend(copy.deepcopy(original[-2:]))
            self.assertTrue(self.driver._compress_context(state, self.profile))
        self.assertEqual(state["messages"][:2], original[:2])
        self.assertNotIn("SECRET", state["last_compression_summary"])
        self.assertNotIn("SECRET", json.dumps(self.runtime.sessions.read("prefix01")))

    def test_summary_usage_can_exhaust_budget_before_next_agent_call(self):
        state = driver_state(self.runtime, "budget01", self.history(), tokens=995, token_budget=1000)
        with mock.patch.object(llm, "chat", return_value=response("Summary")) as chat:
            self.driver._compress_context(state, self.profile)
            result = self.driver._run(state)
        self.assertEqual(result["reason"], "token_budget")
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(result["tokens"], 1010)

    def test_summary_calls_share_model_step_budget(self):
        state = driver_state(self.runtime, "steps01", self.history(), max_steps=2)
        with mock.patch.object(llm, "chat", return_value=response("Summary")) as chat:
            self.driver._compress_context(state, self.profile)
            result = self.driver._run(state)
        self.assertEqual(result["reason"], "step_budget")
        self.assertEqual(chat.call_count, 1)

    def test_missing_summary_usage_is_explicitly_incomplete(self):
        state = driver_state(self.runtime, "unknown01", self.history())
        with mock.patch.object(llm, "chat", return_value={"content": "Summary", "usage": {}}):
            self.driver._compress_context(state, self.profile)
        self.assertFalse(state["usage_complete"])

    def test_exact_token_budget_blocks_requested_tool(self):
        settings.save({"agent_token_budget": 1024})
        handler = mock.Mock(return_value={"ok": True})
        self.register(handler)
        answer = response(calls=[("A", "test.read", {})])
        answer["usage"] = {"prompt_tokens": 1000, "completion_tokens": 24, "usage_reported": True}
        with mock.patch.object(llm, "chat", return_value=answer):
            result = self.runtime.start("Bounded execution")
        self.assertEqual(result["reason"], "token_budget")
        handler.assert_not_called()

    def test_deterministic_summary_is_bounded(self):
        self.assertLessEqual(len(self.driver._deterministic_summary(
            "summary01", self.history() * 100, "Goal")), COMPRESS_SUMMARY_MAX_CHARS)

    def test_public_usage_has_explicit_context_measurement_and_budgets(self):
        with mock.patch.object(llm, "chat", return_value=response("Done")):
            result = self.runtime.start("Explain usage")
        self.assertEqual(result["context"]["measurement"], "heuristic_tokens")
        self.assertGreater(result["context"]["tokens"], 0)
        self.assertGreater(result["context"]["bytes"], 0)
        self.assertEqual(result["context"]["limit_tokens"], 32768)
        self.assertEqual(result["budget"]["remaining_tokens"], 100000 - 15)
        self.assertEqual(result["budget"]["model_calls"], 1)

    def test_crash_before_tool_journal_uses_saved_call_metadata(self):
        self.register(lambda _: {"ok": True})
        sid = "prejournal01"
        call = {"id": "original", "name": "test.read", "arguments": {"path": "A"}}
        messages = self.history()[:2] + [response(calls=[("original", "test.read", {"path": "A"})])["assistant_message"]]
        self.driver._save(driver_state(self.runtime, sid, messages, queue=[call], active_call=call))
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("retry", "test.read", {"path": "A"})]), response("Verified")]):
            result = self.runtime.continue_session(sid)
        self.assertEqual(result["status"], "done")
        saved = json.loads(self.driver.path(sid).read_text(encoding="utf-8"))
        self.assertEqual([m["name"] for m in saved["messages"] if m["role"] == "tool"], ["test.read", "test.read"])

    def test_changed_model_blocks_queued_effect_before_execution(self):
        writer = mock.Mock(return_value={"ok": True})
        self.runtime._register("test.write", "write", "Write", writer, self.runtime._obj_schema({}))
        self.runtime.policy.decisions["test.write"] = "allow"
        call = {"id": "write", "name": "test.write", "arguments": {}}
        state = driver_state(self.runtime, "changed01", self.history()[:2], queue=[call])
        self.driver._save(state)
        self.profile["base_url"] = "https://changed.invalid/v1"
        with mock.patch.object(llm, "chat", return_value=response("Done")):
            result = self.runtime.continue_session("changed01")
        self.assertEqual(result["reason"], "model_changed")
        writer.assert_not_called()

    def test_interrupted_effect_cannot_be_reissued_under_new_call_id(self):
        writer = mock.Mock(return_value={"ok": True})
        self.runtime._register("test.write", "write", "Write", writer, self.runtime._obj_schema({}))
        self.runtime.policy.decisions["test.write"] = "allow"
        sid = "unknownwrite01"
        call = {"id": "write", "name": "test.write", "arguments": {}}
        self.runtime.sessions.append(sid, "tool_call", call)
        messages = self.history()[:2] + [response(calls=[("write", "test.write", {})])["assistant_message"]]
        self.driver._save(driver_state(self.runtime, sid, messages, queue=[call], active_call=call))
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("retry-write", "test.write", {})]), response("Done")]):
            result = self.runtime.continue_session(sid)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "interrupted_tool")
        writer.assert_not_called()

    def test_recovered_task_launch_stays_waiting_without_model_request(self):
        sid = "taskrecover01"
        call = {"id": "launch", "name": "test.launch", "arguments": {}}
        self.runtime.sessions.append(sid, "tool_call", call)
        self.runtime.sessions.append(sid, "tool_result", {**call, "result": {
            "ok": True, "status": "running", "task_id": "task01"}})
        messages = self.history()[:2] + [response(calls=[("launch", "test.launch", {})])["assistant_message"]]
        self.driver._save(driver_state(self.runtime, sid, messages, queue=[call], active_call=call))
        with mock.patch.object(llm, "chat", return_value=response("Done")) as chat, \
                mock.patch.object(service, "get", return_value=mock.Mock(status="running", task_id="task01")):
            result = self.runtime.continue_session(sid)
        self.assertEqual(result["status"], "waiting_task")
        chat.assert_not_called()

    def test_legacy_compacted_state_restores_full_request_from_journal(self):
        sid = "legacygoal01"
        request = "完整原始请求" + "甲" * 220 + "尾部硬性约束"
        self.runtime.sessions.append(sid, "user_message", {"text": request})
        state = driver_state(self.runtime, sid, [{"role": "system", "content": "Stable"},
                    {"role": "user", "content": "[上下文压缩] 旧摘要"}], compressions=1)
        self.driver._save(state)
        with mock.patch.object(llm, "chat", return_value=response("Done")) as chat:
            self.runtime.continue_session(sid)
        self.assertEqual(chat.call_args.args[0][1], {"role": "user", "content": request})

    def test_crashed_model_request_marks_usage_unknown_on_resume(self):
        with mock.patch.object(llm, "chat", side_effect=SystemExit("provider response interrupted")):
            with self.assertRaises(SystemExit):
                self.runtime.start("Explain after restart")
        sid = next(self.root.glob("*.driver.json")).name.removesuffix(".driver.json")
        with mock.patch.object(llm, "chat", return_value=response("Done")):
            result = self.runtime.continue_session(sid)
        self.assertFalse(result["usage_complete"])
        self.assertEqual(result["budget"]["model_calls"], 2)

    def test_task_wait_is_durable_with_its_first_observation(self):
        self.register(lambda _: {"ok": True, "status": "running", "task_id": "task01"})
        save = AdaptiveDriver._save

        def interrupt(driver, state):
            save(driver, state)
            if any(m["role"] == "tool" for m in state["messages"]):
                raise SystemExit("crash immediately after observation")

        with mock.patch.object(AdaptiveDriver, "_save", new=interrupt), mock.patch.object(
                llm, "chat", return_value=response(calls=[("launch", "test.read", {})])):
            with self.assertRaises(SystemExit):
                self.runtime.start("Wait for task")
        sid = next(self.root.glob("*.driver.json")).name.removesuffix(".driver.json")
        with mock.patch.object(llm, "chat", return_value=response("Done")) as chat, \
                mock.patch.object(service, "get", return_value=mock.Mock(status="running", task_id="task01")):
            result = self.runtime.continue_session(sid)
        self.assertEqual(result["status"], "waiting_task")
        chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
