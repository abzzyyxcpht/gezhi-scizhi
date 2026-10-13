"""Failed effectful operations cannot be silently replayed by model repair."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import llm, model_registry, prompts, settings
from src.agent_driver import AdaptiveDriver
from src.agent_runtime import AgentRuntime, ToolCall
from tests.test_adaptive_hardening import response
from tests.test_driver_resilience import driver_state


class ToolRetrySafetyTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        profile = {"connection_id": "connection", "profile_id": "profile", "model": "model",
                   "protocol": "responses", "base_url": "https://fixture.example/v1",
                   "context_window": 32768, "api_key": "test-key"}
        for patch in (mock.patch.object(settings, "_PATH", self.root / "settings.json"),
                      mock.patch.object(llm, "available", return_value=True),
                      mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(profile)),
                      mock.patch.object(prompts, "agent_system", return_value="Fixture system.")):
            patch.start()
            self.addCleanup(patch.stop)
        self.runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=self.root,
                                    permissions={"test.write": "allow"})

    def test_write_failure_after_effect_rejects_model_replay(self):
        effects = self.root / "effects.txt"

        def write_then_fail(_):
            with effects.open("a", encoding="utf-8") as stream:
                stream.write("effect\n")
            return {"ok": False, "error": "failed after writing"}

        self.runtime._register("test.write", "write", "fixture", write_then_fail)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("first", "test.write", {})]),
                response(calls=[("retry", "test.write", {})]), response("done")]):
            result = self.runtime.start("Write once")
        self.assertEqual(effects.read_text(encoding="utf-8"), "effect\n")
        self.assertEqual(result["reason"], "unsafe_tool_retry")
        first = next(event["payload"]["result"] for event in self.runtime.sessions.read(result["session_id"])
                     if event["type"] == "tool_result")
        self.assertIs(first["executed"], True)

    def test_invalid_parameters_are_corrected_before_handler_execution(self):
        effects = self.root / "effects.txt"

        def write(arguments):
            effects.write_text(arguments["text"], encoding="utf-8")
            return {"ok": True}

        self.runtime._register("test.write", "write", "fixture", write,
                               self.runtime._obj_schema({"text": {"type": "string"}}, ["text"]))
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("invalid", "test.write", {})]),
                response(calls=[("corrected", "test.write", {"text": "once"})]), response("done")]):
            result = self.runtime.start("Write once")
        self.assertEqual(result["status"], "done")
        self.assertEqual(effects.read_text(encoding="utf-8"), "once")
        receipts = [event["payload"]["result"] for event in self.runtime.sessions.read(result["session_id"])
                    if event["type"] == "tool_result"]
        self.assertEqual(receipts[0]["error"], "invalid_arguments")
        self.assertIs(receipts[0]["executed"], False)

    def test_explicitly_unexecuted_failure_can_retry(self):
        attempts = []

        def write(_):
            attempts.append(1)
            return ({"ok": False, "error": "precondition unavailable", "executed": False}
                    if len(attempts) == 1 else {"ok": True})

        self.runtime._register("test.write", "write", "fixture", write)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("first", "test.write", {})]),
                response(calls=[("retry", "test.write", {})]), response("done")]):
            result = self.runtime.start("Write once")
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(attempts), 2)

    def test_registered_safe_retry_can_replay(self):
        attempts = []

        def write(_):
            attempts.append(1)
            return {"ok": len(attempts) > 1, "error": "transient failure"}

        self.runtime._register("test.write", "write", "idempotent fixture", write, retry_safe=True)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("first", "test.write", {})]),
                response(calls=[("retry", "test.write", {})]), response("done")]):
            result = self.runtime.start("Ensure desired state")
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(attempts), 2)

    def test_large_failure_receipt_preserves_execution_contract(self):
        self.runtime._register("test.write", "write", "fixture", lambda _: {
            "ok": False, "error": "e" * 20000, "executed": False})
        result = self.runtime._execute_call("sid", ToolCall("first", "test.write", {}, "write"))
        self.assertTrue(result["truncated"])
        self.assertIs(result["executed"], False)
        self.assertEqual(self.runtime.sessions.read("sid")[-1]["payload"]["result"], result)

    def test_handler_exception_after_effect_rejects_model_replay(self):
        attempts = []

        def write(_):
            attempts.append(1)
            raise OSError("receipt failed after writing")

        self.runtime._register("test.write", "write", "fixture", write)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("first", "test.write", {})]),
                response(calls=[("retry", "test.write", {})]), response("done")]):
            result = self.runtime.start("Write once")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(result["reason"], "unsafe_tool_retry")

    def test_changed_arguments_do_not_bypass_unknown_effect_failure(self):
        attempts = []

        def write(arguments):
            attempts.append(arguments["text"])
            return {"ok": False, "error": "failed after writing"}

        self.runtime._register("test.write", "write", "fixture", write,
                               self.runtime._obj_schema({"text": {"type": "string"}}, ["text"]))
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("first", "test.write", {"text": "first"})]),
                response(calls=[("retry", "test.write", {"text": "revised"})]), response("done")]):
            result = self.runtime.start("Write once")
        self.assertEqual(attempts, ["first"])
        self.assertEqual(result["reason"], "unsafe_tool_retry")

    def test_read_tool_failure_remains_repairable(self):
        attempts = []

        def read(_):
            attempts.append(1)
            return {"ok": len(attempts) > 1, "error": "temporarily unavailable"}

        self.runtime._register("test.read", "read", "fixture", read)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("first", "test.read", {})]),
                response(calls=[("retry", "test.read", {})]), response("done")]):
            result = self.runtime.start("Read once")
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(attempts), 2)

    def test_invalid_execution_claim_is_rejected_before_model_repair(self):
        attempts = []

        def write(_):
            attempts.append(1)
            return {"ok": False, "error": "failure", "executed": "false"}

        self.runtime._register("test.write", "write", "fixture", write)
        with mock.patch.object(llm, "chat", return_value=response(calls=[("first", "test.write", {})])):
            result = self.runtime.start("Write once")
        self.assertEqual(result["reason"], "invalid_tool_result")
        self.assertEqual(len(attempts), 1)

    def test_result_cannot_enable_safe_retry_without_registered_contract(self):
        attempts = []

        def write(_):
            attempts.append(1)
            return {"ok": False, "error": "failure", "retry_safe": True}

        self.runtime._register("test.write", "write", "fixture", write)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("first", "test.write", {})]),
                response(calls=[("retry", "test.write", {})]), response("done")]):
            result = self.runtime.start("Write once")
        self.assertEqual(result["reason"], "unsafe_tool_retry")
        self.assertEqual(len(attempts), 1)

    def test_non_blocking_result_cannot_erase_failed_side_effect_evidence(self):
        attempts = []

        def write(_):
            attempts.append(1)
            return {"ok": False, "error": "failure after effect", "non_blocking": True}

        self.runtime._register("test.write", "write", "fixture", write)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("first", "test.write", {})]),
                response(calls=[("retry", "test.write", {})]), response("done")]):
            result = self.runtime.start("Write once")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(result["reason"], "unsafe_tool_retry")

    def test_malformed_retry_cannot_erase_previous_side_effect_failure(self):
        attempts = []

        def write(_):
            attempts.append(1)
            return {"ok": False, "error": "failure after effect"}

        self.runtime._register("test.write", "write", "fixture", write)
        malformed = response(calls=[("malformed", "test.write", {})])
        malformed["tool_calls"][0]["error"] = "invalid_json"
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("first", "test.write", {})]), malformed,
                response(calls=[("retry", "test.write", {})]), response("done")]):
            result = self.runtime.start("Write once")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(result["reason"], "unsafe_tool_retry")

    def test_legacy_string_failure_blocks_changed_argument_resume(self):
        attempts = []
        self.runtime._register("test.write", "write", "fixture", lambda _: attempts.append(1) or {"ok": True})
        sid = "legacy-string"
        request = {"text": "Write once", "mode": "agent", "agent": "research"}
        self.runtime.sessions.append(sid, "user_message", request)
        call = {"id": "retry", "name": "test.write", "arguments": {"version": 2}}
        state = driver_state(self.runtime, sid, [{"role": "system", "content": "fixture"},
            {"role": "user", "content": request["text"]}], queue=[call],
            unresolved={json.dumps(["test.write", {"version": 1}]): "unknown old failure"})
        AdaptiveDriver(self.runtime)._save(state)
        with mock.patch.object(llm, "chat", return_value=response("done")):
            result = self.runtime.continue_session(sid)
        self.assertEqual(attempts, [])
        self.assertEqual(result["reason"], "unsafe_tool_retry")

    def test_legacy_pending_approval_cannot_bypass_replay_gate(self):
        attempts = []
        self.runtime._register("test.write", "write", "fixture", lambda _: attempts.append(1) or {"ok": True})
        sid = "legacy-approval"
        self.runtime.sessions.append(sid, "user_message", {"text": "Write once", "mode": "agent", "agent": "research"})
        pending = {"id": "retry", "name": "test.write", "arguments": {}}
        self.runtime.sessions.append(sid, "permission_requested", {"tool": "test.write", "tool_call_id": "retry",
            "capability": "write", "arguments": {}, "driver": "adaptive"})
        state = driver_state(self.runtime, sid, [{"role": "system", "content": "fixture"},
            {"role": "user", "content": "Write once"}], queue=[pending], pending=pending, status="waiting_approval",
            unresolved={json.dumps(["test.write", {}]): {"name": "test.write", "arguments": {}, "error": "old failure"}})
        AdaptiveDriver(self.runtime)._save(state)
        with mock.patch.object(llm, "chat", return_value=response("done")) as model:
            result = self.runtime.approve(sid, "retry", "allow")
        self.assertEqual(attempts, [])
        self.assertEqual(result["reason"], "unsafe_tool_retry")
        model.assert_not_called()

    def test_archived_unexecuted_failure_is_repairable_after_restart(self):
        attempts = []

        def write(_):
            attempts.append(1)
            return ({"ok": False, "error": "e" * 20000, "executed": False}
                    if len(attempts) == 1 else {"ok": True})

        self.runtime._register("test.write", "write", "fixture", write)
        with mock.patch.object(AdaptiveDriver, "_observe", side_effect=SystemExit("crash after durable receipt")):
            with mock.patch.object(llm, "chat", return_value=response(calls=[("first", "test.write", {})])):
                with self.assertRaises(SystemExit):
                    self.runtime.start("Write once")
        sid = next(self.root.glob("*.driver.json")).name.removesuffix(".driver.json")
        with mock.patch.object(llm, "chat", side_effect=[response(calls=[("retry", "test.write", {})]), response("done")]):
            result = self.runtime.continue_session(sid)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(attempts), 2)
