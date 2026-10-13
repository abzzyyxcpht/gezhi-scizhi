"""Independent crash-boundary and bounded-observation integration checks."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import llm, settings
from src.agent_driver import AdaptiveDriver
from src.agent_runtime import AgentRuntime, ToolCall
from src.tool_outputs import ToolOutputStore
from src.tool_output_runtime import prepare_result
from tests.test_adaptive_driver import response


class SimulatedProcessLoss(BaseException):
    pass


class ToolOutputIntegrationReviewTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for patch in (mock.patch.object(settings, "_PATH", self.root / "settings.json"),
                      mock.patch.object(llm, "available", return_value=True)):
            patch.start()
            self.addCleanup(patch.stop)
        settings.save({"agent_stream_enabled": False})
        self.invoked = mock.Mock(return_value={"ok": True, "text": "x" * 100000 + "FINAL_TAIL_中文"})

    def runtime(self):
        runtime = AgentRuntime(session_root=self.root / "sessions", offline=False, llm_enabled=True)
        runtime._register("fixture.once", "write", "Perform the authorized fixture operation", self.invoked)
        runtime.policy.decisions["fixture.once"] = "allow"
        return runtime

    def crash_after_journal(self, phase="before_observe", archive_failure=False):
        runtime = self.runtime()
        captured = {}
        original_save = AdaptiveDriver._save

        def interrupted_save(driver, state):
            if any(message.get("tool_call_id") == "once" for message in state["messages"]):
                raise SimulatedProcessLoss()
            original_save(driver, state)

        crash_patch = (mock.patch.object(AdaptiveDriver, "_observe", side_effect=SimulatedProcessLoss())
                       if phase == "before_observe" else mock.patch.object(AdaptiveDriver, "_save", interrupted_save))
        archive_patch = (mock.patch.object(ToolOutputStore, "publish", side_effect=OSError("disk full"))
                         if archive_failure else mock.patch.object(llm, "available", return_value=True))
        with crash_patch, archive_patch, mock.patch.object(llm, "chat", return_value=response(tool="fixture.once", call_id="once")):
            with self.assertRaises(SimulatedProcessLoss):
                runtime.start("Execute once and inspect the final bytes", on_started=lambda sid: captured.update(sid=sid))
        sid = captured["sid"]
        results = [event for event in runtime.sessions.read(sid) if event["type"] == "tool_result"]
        self.assertEqual(len(results), 1)
        saved = json.loads(AdaptiveDriver(runtime).path(sid).read_text(encoding="utf-8"))
        self.assertEqual(saved["active_call"]["id"], "once")
        self.assertFalse(any(message.get("tool_call_id") == "once" for message in saved["messages"]))
        return sid, results[0]["payload"]["result"]

    def read_tail_model(self, expected_ref, *, list_first=False):
        stage = []
        def model(messages, **kwargs):
            latest = json.loads(messages[-1]["content"]) if messages[-1]["role"] == "tool" else None
            if not stage and list_first:
                stage.append("list")
                return response(tool="tool_output.list", call_id="list")
            if not stage or stage == ["list"]:
                actual = latest["items"][0] if list_first else latest["output_ref"]
                self.assertEqual(actual, expected_ref)
                stage.append("search")
                return response(tool="tool_output.search", call_id="search", args={"output_id": actual["output_id"], "query": "FINAL_TAIL_中文"})
            if stage[-1] == "search":
                self.assertEqual(len(latest["matches"]), 1)
                stage.append("read")
                return response(tool="tool_output.read", call_id="read", args={"output_id": expected_ref["output_id"], "offset": latest["matches"][0]["offset"]})
            self.assertIn("FINAL_TAIL_中文", latest["text"])
            return response("Recovered the original output and inspected its tail")
        return model

    def test_restart_reconciles_journal_reference_without_replaying_tool(self):
        sid, result = self.crash_after_journal()
        restarted = self.runtime()
        with mock.patch.object(llm, "chat", side_effect=self.read_tail_model(result["output_ref"])):
            outcome = restarted.continue_session(sid)
        self.assertEqual(outcome["status"], "done", outcome)
        self.invoked.assert_called_once()
        logged = [event for event in restarted.sessions.read(sid)
                  if event["type"] == "tool_result" and event["payload"]["id"] == "once"]
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["payload"]["result"]["output_ref"], result["output_ref"])
        self.assertEqual(ToolOutputStore(restarted.sessions.root).list(sid)["total"], 1)

    def test_crash_after_observation_before_state_save_also_does_not_replay(self):
        sid, result = self.crash_after_journal(phase="before_save")
        restarted = self.runtime()
        with mock.patch.object(llm, "chat", side_effect=self.read_tail_model(result["output_ref"])):
            outcome = restarted.continue_session(sid)
        self.assertEqual(outcome["status"], "done", outcome)
        self.invoked.assert_called_once()

    def test_failed_archive_receipt_survives_restart_without_replay(self):
        sid, result = self.crash_after_journal(archive_failure=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["output_unavailable"])
        restarted = self.runtime()
        def model(messages, **kwargs):
            restored = json.loads(messages[-1]["content"])
            self.assertTrue(restored["output_unavailable"])
            self.assertNotIn("output_ref", restored)
            return response("The original operation completed; its complete output is unavailable")
        with mock.patch.object(llm, "chat", side_effect=model):
            outcome = restarted.continue_session(sid)
        self.assertEqual(outcome["status"], "done", outcome)
        self.invoked.assert_called_once()

    def test_compression_drops_reference_from_active_context_but_model_can_rediscover_it(self):
        sid, result = self.crash_after_journal()
        restarted = self.runtime()
        driver = AdaptiveDriver(restarted)
        state = driver._load(sid)
        driver._reconcile_interrupted(state)
        state["messages"].extend([
            response(tool="workspace.read", call_id="later")["assistant_message"],
            {"role": "tool", "tool_call_id": "later", "name": "workspace.read", "content": '{"ok":true}'},
        ])
        with mock.patch.object(driver, "_model_summary", return_value="Earlier work completed; inspect archived outputs before drawing conclusions."):
            self.assertTrue(driver._compress_context(state, {"context_window": 128000}))
        self.assertNotIn(result["output_ref"]["output_id"], json.dumps(state["messages"]))
        with mock.patch.object(llm, "chat", side_effect=self.read_tail_model(result["output_ref"], list_first=True)):
            outcome = restarted.continue_session(sid)
        self.assertEqual(outcome["status"], "done", outcome)
        self.invoked.assert_called_once()

    def test_huge_children_waiting_cannot_expand_public_receipt_or_create_reference_chain(self):
        runtime = self.runtime()
        runtime._register("fixture.children", "read", "Malformed huge control data", lambda _: {
            "ok": True, "status": "waiting_children", "children_waiting": [f"child_{index:05}" for index in range(10000)],
            "text": "x" * 20000})
        result = runtime._execute_call("bounded", ToolCall("children", "fixture.children", {}, "read"))
        self.assertLess(len(json.dumps(result, ensure_ascii=False)), 12000)
        self.assertTrue(result["control_state_error"])
        self.assertFalse(result["ok"])
        self.assertNotIn("children_waiting", result)
        self.assertNotIn("status", result)
        state = {"session_id": "bounded", "messages": [], "unresolved": {}, "queue": [], "active_calls": []}
        AdaptiveDriver(runtime)._observe(state, {"id": "children", "name": "fixture.children", "arguments": {}}, result)
        observed = json.loads(state["messages"][-1]["content"])
        self.assertEqual(observed.get("output_ref"), result.get("output_ref"))
        self.assertEqual(ToolOutputStore(runtime.sessions.root).list("bounded")["total"], 1)

    def test_malformed_large_ok_field_cannot_expand_public_receipt(self):
        runtime = self.runtime()
        runtime._register("fixture.bad_ok", "read", "Malformed control data", lambda _: {"ok": {"value": "x" * 30000}})
        result = runtime._execute_call("bounded", ToolCall("bad", "fixture.bad_ok", {}, "read"))
        self.assertTrue(isinstance(result["ok"], bool), "ok must be normalized to a boolean")
        self.assertLess(len(json.dumps(result, ensure_ascii=False)), 12000)

    def test_even_small_invalid_control_fields_are_archived_and_never_used_for_waiting(self):
        runtime = self.runtime()
        invalid = ({"ok": "yes"}, {"status": []}, {"status": "x" * 161}, {"task_id": "../other"},
                   {"children_waiting": [None]}, {"children_waiting": ["child"] * 65},
                   {"non_blocking": "true"}, {"timed_out": []}, {"timeout": float("nan")},
                   {"exit_code": 10 ** 200})
        for index, bad in enumerate(invalid):
            raw = {"ok": True, "status": "waiting_children", "task_id": "task1", "children_waiting": ["child1"], **bad}
            with self.subTest(field=next(iter(bad))):
                result = prepare_result(runtime, "small", {"id": str(index), "name": "fixture"}, raw)
                self.assertTrue(result.get("control_state_error"))
                self.assertFalse(result["ok"])
                self.assertIn("output_ref", result)
                for field in ("status", "task_id", "children_waiting", "non_blocking"):
                    self.assertNotIn(field, result)

    def test_maximum_valid_control_metadata_reduces_preview_without_losing_child_ids(self):
        runtime = self.runtime()
        children = ["c" * 125 + f"{index:03}" for index in range(64)]
        raw = {"ok": True, "status": "waiting_children", "children_waiting": children,
               "task_id": "t" * 128, "error": "e" * 1600, "text": "x" * 20000}
        call = {"id": "\t" * 200, "name": "\n" * 160}
        result = prepare_result(runtime, "maximum", call, raw)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 12000)
        self.assertEqual(result["children_waiting"], children)
        self.assertEqual(result["status"], "waiting_children")
        repeated = prepare_result(runtime, "maximum", call, result)
        self.assertEqual(repeated["output_ref"], result["output_ref"])
        self.assertEqual(ToolOutputStore(runtime.sessions.root).list("maximum")["total"], 1)

    def test_error_smaller_than_inline_limit_still_has_bounded_error_and_complete_archive(self):
        runtime = self.runtime()
        raw = {"ok": False, "error": "e" * 8000 + "ERROR_TAIL"}
        result = prepare_result(runtime, "error", {"id": "error", "name": "fixture"}, raw)
        self.assertLessEqual(len(result["error"]), 1600)
        ref = result["output_ref"]
        found = ToolOutputStore(runtime.sessions.root).search("error", ref["output_id"], "ERROR_TAIL")
        self.assertEqual(len(found["matches"]), 1)

    def test_model_cannot_override_session_scope_in_output_arguments(self):
        runtime = self.runtime()
        private = ToolOutputStore(runtime.sessions.root).publish("private", "call", "fixture", "PRIVATE_SENTINEL")
        result = runtime._execute_call("public", ToolCall("read", "tool_output.read",
            {"output_id": private["output_id"], "__session_id": "private"}, "read"))
        self.assertFalse(result["ok"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_invalid_control_result_stops_model_loop_without_repeating_completed_effect(self):
        self.invoked.return_value = {"ok": "yes", "text": "completed side effect"}
        runtime = self.runtime()
        with mock.patch.object(llm, "chat", return_value=response(tool="fixture.once", call_id="once")) as model:
            outcome = runtime.start("Perform the authorized operation once")
        self.assertEqual(outcome["status"], "error")
        self.assertEqual(outcome["reason"], "invalid_tool_result")
        self.invoked.assert_called_once()
        model.assert_called_once()
        receipts = [event["payload"]["result"] for event in runtime.sessions.read(outcome["session_id"])
                    if event["type"] == "tool_result"]
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0]["control_state_error"])
        self.assertIn("output_ref", receipts[0])

    def test_parallel_bad_control_result_keeps_peer_journal_and_does_not_retry_either(self):
        runtime = self.runtime()
        bad = mock.Mock(return_value={"ok": True, "status": "waiting_children", "children_waiting": ["child"] * 65})
        peer = mock.Mock(return_value={"ok": True, "text": "peer finished"})
        runtime._register("fixture.bad_children", "read", "Malformed child state", bad)
        runtime._register("fixture.peer", "read", "Independent peer result", peer)
        request = response(tool="fixture.bad_children", call_id="bad")
        second = response(tool="fixture.peer", call_id="peer")
        request["tool_calls"].extend(second["tool_calls"])
        request["assistant_message"]["tool_calls"].extend(second["assistant_message"]["tool_calls"])
        with mock.patch.object(llm, "chat", return_value=request) as model:
            outcome = runtime.start("Read both independent fixtures")
        self.assertEqual(outcome["status"], "error")
        self.assertEqual(outcome["reason"], "invalid_tool_result")
        bad.assert_called_once()
        peer.assert_called_once()
        model.assert_called_once()
        receipts = {event["payload"]["id"]: event["payload"]["result"]
                    for event in runtime.sessions.read(outcome["session_id"]) if event["type"] == "tool_result"}
        self.assertEqual(set(receipts), {"bad", "peer"})
        self.assertTrue(receipts["bad"]["control_state_error"])
        self.assertEqual(receipts["peer"]["text"], "peer finished")
        state = json.loads(AdaptiveDriver(runtime).path(outcome["session_id"]).read_text(encoding="utf-8"))
        observed = [message["tool_call_id"] for message in state["messages"] if message["role"] == "tool"]
        self.assertEqual(observed, ["bad", "peer"])


if __name__ == "__main__":
    unittest.main()
