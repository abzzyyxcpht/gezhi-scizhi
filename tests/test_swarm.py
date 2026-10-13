"""Persisted, permission-aware Swarm orchestration."""
from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from src import llm, settings
from src.agent_runtime import AgentRuntime
from tests.test_subagent_isolation import response


class SwarmCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(settings, "_PATH", self.root / "settings.json")
        patch.start()
        self.addCleanup(patch.stop)

    def coordinator(self, permissions=None):
        from src.swarm import SwarmCoordinator
        runtime = AgentRuntime(session_root=self.root / "sessions", permissions=permissions)
        return SwarmCoordinator(runtime, root=self.root / "swarms")

    def test_plan_is_durable_and_does_not_execute(self):
        coordinator = self.coordinator()
        plan = coordinator.plan("research agents", branches=[{"agent": "explore", "request": "inspect"}])
        self.assertEqual(plan["status"], "planned")
        self.assertTrue(plan["offline"])
        self.assertEqual(coordinator.runtime.sessions.list(), [])
        self.assertEqual(self.coordinator().snapshot(plan["plan_id"])["request"], "research agents")
        self.assertEqual(len(self.coordinator().list()), 1)

    def test_start_respects_tool_approval_and_snapshot_reflects_approval(self):
        coordinator = self.coordinator()
        plan = coordinator.plan("inspect", branches=[{"agent": "explore", "request": "inspect"}])
        outcome = coordinator.start(plan["plan_id"])
        self.assertEqual(outcome["status"], "waiting_approval")
        pending = outcome["pending"][0]
        coordinator.runtime.approve(pending["session_id"], pending["id"], "allow")
        completed = self.coordinator().snapshot(plan["plan_id"])
        self.assertEqual(completed["status"], "done")
        self.assertEqual(completed["progress"]["completed"], 1)

    def test_parallel_starts_do_not_duplicate_branches(self):
        coordinator = self.coordinator({"subagent.fanout": "allow"})
        plan = coordinator.plan("inspect", branches=[{"agent": "explore", "request": "inspect"}])
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(coordinator.start, [plan["plan_id"], plan["plan_id"]]))
        self.assertTrue(all(row["status"] == "done" for row in outcomes))
        children = list((coordinator.runtime.sessions.root / plan["session_id"] / "children").glob("*.meta.json"))
        self.assertEqual(len(children), 1)

    def test_cancel_planned_run_does_not_start_any_tools(self):
        coordinator = self.coordinator({"subagent.fanout": "allow"})
        plan = coordinator.plan("inspect")
        self.assertEqual(coordinator.cancel(plan["plan_id"])["status"], "cancelled")
        self.assertEqual(coordinator.start(plan["plan_id"])["status"], "cancelled")
        self.assertEqual(coordinator.runtime.sessions.list(), [])

    def test_invalid_plan_values_are_rejected_before_persistence(self):
        coordinator = self.coordinator()
        for kwargs in ({"request": ""}, {"request": "x", "max_parallel": 9},
                       {"request": "x", "branches": []},
                       {"request": "x", "branches": [{"agent": "missing", "request": "x"}]},
                       {"request": "x", "branches": [{"agent": "explore", "request": "x"}] * 17}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                coordinator.plan(**kwargs)
        self.assertEqual(coordinator.list(), [])

    def test_crashed_owner_is_reported_interrupted_without_replaying(self):
        from src.state_store import write_json
        coordinator = self.coordinator()
        plan = coordinator.plan("inspect")
        plan.update(status="running", owner_pid=2147483647)
        write_json(coordinator.root / f"{plan['plan_id']}.json", plan)
        outcome = coordinator.snapshot(plan["plan_id"])
        self.assertEqual(outcome["status"], "interrupted")
        self.assertEqual(coordinator.start(plan["plan_id"])["status"], "interrupted")

    def test_child_approvals_complete_parent_and_keep_branch_identity(self):
        from src.swarm import SwarmCoordinator
        runtime = AgentRuntime(session_root=self.root / "sessions", offline=False, llm_enabled=True,
                               permissions={"subagent.fanout": "allow"})
        coordinator = SwarmCoordinator(runtime, root=self.root / "swarms")
        plan = coordinator.plan("research", branches=[{"agent": "literature", "request": "same"}] * 2)
        with mock.patch.object(llm, "available", return_value=True), \
             mock.patch.object(llm, "chat", return_value=response(tool="literature.run",
                 args={"request": "research"}, call_id="child-pending")):
            outcome = coordinator.start(plan["plan_id"])
        self.assertEqual(outcome["status"], "waiting_approval")
        self.assertEqual(len(outcome["pending"]), 2)
        with mock.patch.object(llm, "chat", return_value=response("complete")), \
             mock.patch.object(runtime, "_run_named", return_value={"ok": True}):
            for pending in outcome["pending"]:
                runtime.approve(pending["session_id"], pending["id"], "allow")
        self.assertEqual(coordinator.snapshot(plan["plan_id"])["status"], "done")
        self.assertEqual(runtime.sessions.snapshot(plan["session_id"])["status"], "done")
        for branch in coordinator.snapshot(plan["plan_id"])["branches"]:
            import json
            meta_path = runtime.sessions.root / plan["session_id"] / "children" / f"{branch['session_id']}.meta.json"
            self.assertEqual(json.loads(meta_path.read_text(encoding="utf-8"))["branch_id"], branch["id"])

    def test_cancel_running_swarm_terminates_parent_and_does_not_start_queue(self):
        coordinator = self.coordinator({"subagent.fanout": "allow"})
        plan = coordinator.plan("inspect", branches=[{"agent": "explore", "request": str(i)} for i in range(6)],
                                max_parallel=1)
        entered, release = threading.Event(), threading.Event()
        executed = []
        def run(name, request, **kwargs):
            executed.append(request)
            entered.set()
            self.assertTrue(release.wait(5))
            return {"ok": True}
        with mock.patch("src.agents.specialists.run", side_effect=run):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(coordinator.start, plan["plan_id"])
                self.assertTrue(entered.wait(5))
                self.assertEqual(coordinator.cancel(plan["plan_id"])["status"], "cancelling")
                release.set()
                self.assertEqual(future.result(timeout=5)["status"], "cancelled")
        self.assertEqual(executed, ["0"])
        self.assertEqual(coordinator.runtime.sessions.snapshot(plan["session_id"])["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
