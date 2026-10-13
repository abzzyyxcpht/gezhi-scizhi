"""Durable approval and error-state regressions; all writes use temporary data."""
import json
import tempfile
import unittest
from pathlib import Path

from src.agent_runtime import MAX_STEPS, AgentRuntime


class ScriptedRuntime(AgentRuntime):
    def __init__(self, root, steps, calls, *, failure=None, permissions=None):
        super().__init__(session_root=root, permissions=permissions)
        self.steps = steps

        def execute(args):
            calls.append(args.get("value"))
            return failure or {"ok": True}

        self._register("test.write", "write", "Test write", execute)
        self._register("test.read", "read", "Test read", execute)

    def plan(self, request, mode="plan", agent="research"):
        return self._plan_payload(mode, request, self.steps, agent)


def step(value, tool="test.write"):
    return {"tool": tool, "arguments": {"value": value}}


class AgentSessionSafetyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.calls = []

    def runtime(self, steps, **kwargs):
        return ScriptedRuntime(self.root, steps, self.calls, **kwargs)

    def approve(self, runtime, result, decision="allow"):
        return runtime.approve(result["session_id"], result["pending"]["id"], decision)

    def test_remembered_permission_does_not_authorize_another_session(self):
        runtime = self.runtime([step(1), step(2)])
        first = runtime.start("First request")
        self.assertEqual(self.approve(runtime, first, "allow_session")["status"], "done")
        self.assertEqual(self.calls, [1, 2])

        second = runtime.start("Second request")
        self.assertEqual(second["status"], "waiting_approval")
        self.assertEqual(self.calls, [1, 2])

    def test_remembered_permission_survives_restart_for_its_session(self):
        steps = [step(1), step(2, "test.other"), step(3)]
        runtime = self.runtime(steps)
        runtime._register("test.other", "write", "Second tool", lambda args: {"ok": True})
        waiting = self.approve(runtime, runtime.start("Restart"), "allow_session")
        self.assertEqual(waiting["pending"]["tool"], "test.other")

        restarted = self.runtime(steps)
        restarted._register("test.other", "write", "Second tool", lambda args: {"ok": True})
        result = self.approve(restarted, waiting)
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.calls, [1, 3])
        self.assertEqual(restarted.start("New session")["status"], "waiting_approval")

    def test_decided_approval_cannot_be_replayed_after_restart(self):
        for decision in ("allow", "deny"):
            with self.subTest(decision=decision):
                runtime = self.runtime([step(decision)])
                waiting = runtime.start(decision)
                self.approve(runtime, waiting, decision)
                count = len(self.calls)
                restarted = self.runtime([step(decision)])
                with self.assertRaises(ValueError):
                    self.approve(restarted, waiting)
                self.assertEqual(len(self.calls), count)

    def test_stale_runtime_cannot_reapprove_a_call_decided_elsewhere(self):
        runtime = self.runtime([step(1)])
        waiting = runtime.start("First runtime")
        self.approve(self.runtime([step(1)]), waiting)
        with self.assertRaises(ValueError):
            self.approve(runtime, waiting)
        self.assertEqual(self.calls, [1])

    def test_restart_resumes_after_the_correct_repeated_tool(self):
        steps = [step(1), step(2), step(3)]
        runtime = self.runtime(steps)
        second = self.approve(runtime, runtime.start("Repeated tool"))
        third = self.approve(self.runtime(steps), second)
        self.assertEqual(third["pending"]["arguments"], {"value": 3})
        done = self.approve(self.runtime(steps), third)
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.calls, [1, 2, 3])

    def test_legacy_log_without_step_index_resumes_correct_repeated_tool(self):
        steps = [step(1), step(2), step(3)]
        runtime = self.runtime(steps)
        second = self.approve(runtime, runtime.start("Legacy session"))
        events = runtime.sessions.read(second["session_id"])
        for event in events:
            event["payload"].pop("step_index", None)
        runtime.sessions._path(second["session_id"]).write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        third = self.approve(self.runtime(steps), second)
        self.assertEqual(third["pending"]["arguments"], {"value": 3})
        self.assertEqual(self.calls, [1, 2])

    def test_failed_automatic_tool_stops_dependent_steps(self):
        runtime = self.runtime([step(1, "test.read"), step(2, "test.read")],
                               failure={"ok": False, "error": "fixture failure"})
        result = runtime.start("Fail automatic tool")
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.calls, [1])
        self.assertFalse(any(event["type"] == "completed"
                             for event in runtime.sessions.read(result["session_id"])))

    def test_failed_approved_tool_stops_dependent_steps(self):
        runtime = self.runtime([step(1), step(2, "test.read")],
                               failure={"ok": False, "error": "fixture failure"})
        waiting = runtime.start("Fail approved tool")
        result = self.approve(runtime, waiting)
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.calls, [1])
        events = runtime.sessions.read(result["session_id"])
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["payload"]["tool"], "test.write")

    def test_list_reports_request_agent_state_and_latest_timestamp(self):
        runtime = self.runtime([step(1)])
        result = runtime.start("A useful research question")
        row = runtime.sessions.list()[0]
        self.assertEqual(row["summary"], "A useful research question")
        self.assertEqual(row["agent"], "research")
        self.assertEqual(row["status"], "waiting_approval")
        self.assertEqual(row["updated_at"], runtime.sessions.read(result["session_id"])[-1]["timestamp"])
        self.assertEqual(row["session_id"], result["session_id"])
        self.assertGreater(row["events"], 0)
        self.assertEqual(row["last_type"], "permission_requested")
        self.approve(runtime, result)
        self.assertEqual(runtime.sessions.list()[0]["status"], "done")

    def test_list_distinguishes_planned_blocked_and_failed_sessions(self):
        for mode, expected in (("plan", "planned"), ("agent", "blocked")):
            with self.subTest(mode=mode):
                runtime = self.runtime([step(1)])
                result = runtime.start(mode, mode=mode)
                if mode == "agent":
                    self.approve(runtime, result, "deny")
                row = next(row for row in runtime.sessions.list()
                           if row["session_id"] == result["session_id"])
                self.assertEqual(row["status"], expected)
        runtime = self.runtime([step(1, "test.read")], failure={"ok": False})
        result = runtime.start("Failure")
        row = next(row for row in runtime.sessions.list()
                   if row["session_id"] == result["session_id"])
        self.assertEqual(row["status"], "error")

    def test_snapshot_recovers_pending_request_without_changing_log(self):
        runtime = self.runtime([step(1), step(2)])
        waiting = runtime.start("Recover this research question")
        path = runtime.sessions._path(waiting["session_id"])
        before = path.read_bytes()
        restored = self.runtime([step(1), step(2)])
        snapshot = restored.sessions.snapshot(waiting["session_id"])
        self.assertEqual(snapshot["session_id"], waiting["session_id"])
        self.assertEqual(snapshot["request"], "Recover this research question")
        self.assertEqual(snapshot["agent"], "research")
        self.assertEqual(snapshot["mode"], "agent")
        self.assertEqual(snapshot["status"], "waiting_approval")
        self.assertEqual(snapshot["plan"]["steps"], [step(1), step(2)])
        self.assertEqual(snapshot["pending"], waiting["pending"])
        self.assertEqual(path.read_bytes(), before)

    def test_snapshot_preserves_plan_only_session(self):
        runtime = self.runtime([step(1)])
        planned = runtime.start("Read-only planning", mode="plan")
        snapshot = runtime.sessions.snapshot(planned["session_id"])
        self.assertEqual(snapshot["status"], "planned")
        self.assertEqual(snapshot["mode"], "plan")
        self.assertEqual(snapshot["plan"], planned["plan"])
        self.assertIsNone(snapshot["pending"])
        self.assertEqual(self.calls, [])

    def test_snapshot_clears_pending_when_decided_executed_or_terminal(self):
        cases = [
            ("permission_granted", {"scope": "allow"}, "running"),
            ("permission_denied", {}, "blocked"),
            ("tool_call", {"name": "test.write"}, "running"),
            ("tool_result", {"result": {"ok": True}}, "running"),
            ("tool_result", {"result": {"ok": False}}, "error"),
            ("completed", {"status": "done"}, "done"),
            ("completed", {"status": "error"}, "error"),
            ("error", {"message": "Interrupted"}, "error"),
        ]
        for event_type, payload, expected in cases:
            with self.subTest(event_type=event_type, expected=expected):
                runtime = self.runtime([step(1)])
                waiting = runtime.start("Snapshot transition")
                sid, call_id = waiting["session_id"], waiting["pending"]["id"]
                runtime.sessions.append(sid, event_type, {
                    **payload, "id": call_id, "tool_call_id": call_id})
                snapshot = runtime.sessions.snapshot(sid)
                self.assertEqual(snapshot["status"], expected)
                self.assertIsNone(snapshot["pending"])
                row = next(row for row in runtime.sessions.list() if row["session_id"] == sid)
                self.assertEqual(row["status"], snapshot["status"])
                self.assertEqual(row["summary"], snapshot["request"])

    def test_snapshot_selects_new_pending_call_after_first_approval(self):
        runtime = self.runtime([step(1), step(2)])
        first = runtime.start("Two calls of the same tool")
        second = self.approve(runtime, first)
        snapshot = runtime.sessions.snapshot(first["session_id"])
        self.assertEqual(snapshot["pending"], second["pending"])
        self.assertNotEqual(snapshot["pending"]["id"], first["pending"]["id"])

    def test_snapshot_missing_session_is_unknown_and_creates_no_log(self):
        runtime = self.runtime([step(1)])
        snapshot = runtime.sessions.snapshot("missing-session")
        self.assertEqual(snapshot, {
            "session_id": "missing-session", "status": "unknown", "request": "",
            "agent": "research", "mode": "", "plan": None, "pending": None})
        self.assertFalse(runtime.sessions._path("missing-session").exists())

    def test_exact_step_limit_completes_after_individual_approvals(self):
        runtime = self.runtime([step(i) for i in range(MAX_STEPS)])
        result = runtime.start("Complete all permitted steps")
        for _ in range(MAX_STEPS):
            self.assertEqual(result["status"], "waiting_approval")
            result = self.approve(runtime, result)
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.calls, list(range(MAX_STEPS)))
        events = runtime.sessions.read(result["session_id"])
        self.assertEqual(events[-1]["type"], "completed")
        self.assertFalse(any(event["type"] == "error" for event in events))

    def test_plan_above_step_limit_executes_no_tools(self):
        runtime = self.runtime([step(i, "test.read") for i in range(MAX_STEPS + 1)])
        result = runtime.start("Reject an oversized plan")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "doom_loop")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
