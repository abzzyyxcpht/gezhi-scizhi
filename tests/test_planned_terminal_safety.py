"""Deterministic execution publishes one terminal result despite late cancel."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.agent_runtime import AgentRuntime
from src.agents.specs import get_spec
from src.swarm import SwarmCoordinator


class PlannedTerminalSafetyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = AgentRuntime(session_root=self.root, offline=True)

    def test_cancel_after_last_planned_check_wins_over_completion(self):
        append = self.runtime.sessions.append
        cancelled = False

        def before_final_append(sid, kind, payload=None):
            nonlocal cancelled
            # The offline general response is emitted after _continue's final
            # cancellation check and directly before completed(done).
            if kind == "assistant_message" and not cancelled:
                cancelled = True
                self.runtime.cancel_session(sid)
            return append(sid, kind, payload)

        with mock.patch.object(self.runtime.sessions, "append", side_effect=before_final_append):
            result = self.runtime.start("Inspect workspace", agent="general")
        self.assertTrue(cancelled)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.runtime.sessions.snapshot(result["session_id"])["status"], "cancelled")
        events = self.runtime.sessions.read(result["session_id"])
        self.assertFalse(any(event["type"] == "completed" and event["payload"]["status"] == "done" for event in events))

    def test_planned_completion_wins_over_late_cancel(self):
        result = self.runtime.start("Inspect workspace", agent="general")
        before = self.runtime.sessions.read(result["session_id"])
        stopped = self.runtime.cancel_session(result["session_id"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(stopped["status"], "done")
        self.assertEqual(self.runtime.sessions.read(result["session_id"]), before)
        self.assertFalse((self.root / f"{result['session_id']}.cancel").exists())

    def test_child_completion_does_not_overwrite_cancel_marker_or_meta(self):
        self.runtime.sessions.append("parent", "user_message", {"text": "delegate"})
        spec = get_spec("explore")
        child = self.runtime._begin_child("parent", spec, "Inspect")
        self.runtime.cancel_session(child)
        result = self.runtime._finish_child("parent", child, spec, {"ok": True, "status": "done"})
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["ok"])
        self.assertEqual(self.runtime.sessions.snapshot(child)["status"], "cancelled")
        meta = json.loads((self.root / "parent" / "children" / f"{child}.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "cancelled")
        statuses = [event["payload"] for event in self.runtime.sessions.read("parent") if event["type"] == "subagent_status"]
        self.assertEqual(statuses[-1]["status"], "cancelled")

    def test_stale_child_completion_keeps_committed_cancelled_status(self):
        self.runtime.sessions.append("parent", "user_message", {"text": "delegate"})
        spec = get_spec("explore")
        child = self.runtime._begin_child("parent", spec, "Inspect")
        self.runtime.sessions.append(child, "completed", {"status": "cancelled"})
        result = self.runtime._finish_child("parent", child, spec, {"ok": True, "status": "done"})
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["ok"])
        meta = json.loads((self.root / "parent" / "children" / f"{child}.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "cancelled")

    def test_stale_waiting_child_sync_cannot_reopen_finished_meta(self):
        self.runtime.sessions.append("parent", "user_message", {"text": "delegate"})
        spec = get_spec("explore")
        child = self.runtime._begin_child("parent", spec, "Inspect")
        self.runtime._finish_child("parent", child, spec, {"ok": False, "status": "cancelled"})
        stale = {"status": "waiting_approval", "pending": {"id": "old"}}
        self.runtime._sync_child_outcome(child, stale)
        self.assertEqual(stale["status"], "cancelled")
        meta = json.loads((self.root / "parent" / "children" / f"{child}.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "cancelled")

    def test_fanout_branch_uses_child_terminal_receipt(self):
        self.runtime.sessions.append("parent", "user_message", {"text": "delegate"})
        finish = self.runtime._finish_child

        def cancel_at_child_finish(parent, child, spec, result, **options):
            self.runtime.cancel_session(child)
            return finish(parent, child, spec, result, **options)

        with mock.patch.object(self.runtime, "_finish_child", side_effect=cancel_at_child_finish):
            result = self.runtime._subagent_fanout({"__session_id": "parent", "requests": ["Inspect"], "agents": ["explore"]})
        branch = result["branches"][0]
        self.assertEqual(branch["status"], "cancelled")
        self.assertFalse(branch["ok"])
        self.assertEqual(branch["pool_count"], 0)

    def _stale_waiting_child_driver(self, parent, child, spec, request):
        self.runtime._publish_terminal(child, "cancelled")
        return {"session_id": child, "status": "waiting_approval", "pending": {"id": "stale"}}

    def test_spawn_refreshes_stale_waiting_outcome_after_sync(self):
        self.runtime.sessions.append("parent", "user_message", {"text": "delegate"})
        with mock.patch.object(self.runtime, "_subagent_driver_available", return_value=True):
            with mock.patch.object(self.runtime, "_run_child_driver", side_effect=self._stale_waiting_child_driver):
                result = self.runtime._subagent_spawn({"__session_id": "parent", "agent": "explore", "request": "Inspect"})
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["ok"])
        self.assertNotIn("children_waiting", result)
        self.assertNotIn("pending", result)

    def test_fanout_refreshes_stale_waiting_outcome_after_sync(self):
        self.runtime.sessions.append("parent", "user_message", {"text": "delegate"})
        with mock.patch.object(self.runtime, "_subagent_driver_available", return_value=True):
            with mock.patch.object(self.runtime, "_run_child_driver", side_effect=self._stale_waiting_child_driver):
                result = self.runtime._subagent_fanout({"__session_id": "parent", "agents": ["explore"], "requests": ["Inspect"]})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["children_waiting"], [])
        self.assertEqual(result["branches"][0]["status"], "cancelled")
        self.assertFalse(result["branches"][0]["ok"])
        self.assertNotIn("pending", result["branches"][0])

    def test_projected_tool_error_does_not_block_actual_cancel_request(self):
        self.runtime.sessions.append("session", "user_message", {"text": "Inspect"})
        self.runtime.sessions.append("session", "tool_result", {"id": "failed", "result": {"ok": False}})
        self.assertEqual(self.runtime.sessions.snapshot("session")["status"], "error")
        result = self.runtime.cancel_session("session")
        self.assertEqual(result["status"], "cancelling")
        self.assertTrue((self.root / "session.cancel").exists())
        final = self.runtime._publish_terminal("session", "error", {"message": "failed"})
        self.assertEqual(final, "cancelled")

    def test_swarm_parent_finalization_respects_cancel_marker(self):
        self.runtime.sessions.append("parent", "user_message", {"text": "delegate", "mode": "swarm"})
        spec = get_spec("explore")
        child = self.runtime._begin_child("parent", spec, "Inspect")
        self.runtime.sessions.append("parent", "driver_paused", {"status": "waiting_children"})
        original = self.runtime._save_workspace

        def cancel_when_parent_ready(sid):
            original(sid)
            if sid == "parent":
                (self.root / "parent.cancel").touch()

        with mock.patch.object(self.runtime, "_save_workspace", side_effect=cancel_when_parent_ready):
            self.runtime._sync_child_outcome(child, {"status": "done", "content": "finished"})
        self.assertEqual(self.runtime.sessions.snapshot("parent")["status"], "cancelled")

    def test_swarm_exception_after_terminal_does_not_replace_it(self):
        coordinator = SwarmCoordinator(self.runtime, root=self.root / "swarms")
        for committed in ("done", "cancelled"):
            with self.subTest(committed=committed):
                planned = coordinator.plan("Inspect workspace", branches=[{
                    "agent": "explore", "request": "Inspect"}])
                sid = planned["session_id"]

                def seal_then_raise(session_id, steps, index):
                    self.runtime._publish_terminal(session_id, committed)
                    raise OSError("post-completion storage failure")

                with mock.patch.object(self.runtime, "_continue", side_effect=seal_then_raise):
                    result = coordinator.start(planned["plan_id"])
                self.assertEqual(result["status"], committed)
                self.assertEqual(self.runtime.sessions.snapshot(sid)["status"], committed)
                terminal = [event for event in self.runtime.sessions.read(sid)
                            if event["type"] in {"completed", "error"}]
                self.assertEqual(len(terminal), 1)

    def test_swarm_cancel_rechecks_terminal_after_stale_snapshot(self):
        coordinator = SwarmCoordinator(self.runtime, root=self.root / "swarms")
        planned = coordinator.plan("Inspect workspace", branches=[{
            "agent": "explore", "request": "Inspect"}])
        sid, plan_id = planned["session_id"], planned["plan_id"]
        snapshot = coordinator.snapshot
        first = True

        def complete(session_id, steps, index):
            return {"status": self.runtime._publish_terminal(session_id, "done")}

        def stale_snapshot(selected):
            nonlocal first
            if first:
                first = False
                stale = snapshot(selected)
                # Completion occurs after cancel's initial read and before it
                # can publish markers; cancellation must read the durable seal.
                coordinator.start(selected)
                return stale
            return snapshot(selected)

        with mock.patch.object(coordinator, "snapshot", side_effect=stale_snapshot), \
             mock.patch.object(self.runtime, "_continue", side_effect=complete):
            result = coordinator.cancel(plan_id)
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.runtime.sessions.snapshot(sid)["status"], "done")
        self.assertFalse(coordinator._cancel_path(plan_id).exists())
        self.assertFalse((self.root / f"{sid}.cancel").exists())
