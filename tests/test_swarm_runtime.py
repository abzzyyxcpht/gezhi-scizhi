"""Swarm lifecycle regressions: approvals, isolation and cooperative cancellation."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from src import llm, settings
from src.agent_runtime import AgentRuntime
from src.agents.specs import get_spec
from tests.test_subagent_isolation import response


class SwarmRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(settings, "_PATH", self.root / "settings.json")
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(llm, "available", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)

    def runtime(self, online=False):
        runtime = AgentRuntime(session_root=self.root, offline=not online,
                               llm_enabled=online,
                               permissions={"subagent.fanout": "allow", "subagent.spawn": "allow"})
        runtime._session_agent["parent"] = "research"
        runtime._workspaces["parent"] = {"pool": [{"title": "original"}]}
        runtime.sessions.append("parent", "user_message", {"text": "research"})
        return runtime

    def fanout(self, runtime, requests, agent="explore", **extra):
        return runtime._subagent_fanout({"__session_id": "parent",
            "agents": [agent] * len(requests), "requests": requests, **extra})

    def test_nested_child_workspace_cannot_mutate_parent_or_sibling(self):
        runtime = self.runtime()
        first = runtime._begin_child("parent", get_spec("explore"), "first")
        second = runtime._begin_child("parent", get_spec("explore"), "second")
        runtime._workspaces[first]["pool"][0]["title"] = "child change"
        self.assertEqual(runtime._workspaces["parent"]["pool"][0]["title"], "original")
        self.assertEqual(runtime._workspaces[second]["pool"][0]["title"], "original")

    def test_running_child_is_registered_before_completion(self):
        runtime = self.runtime()
        child = runtime._begin_child("parent", get_spec("explore"), "first")
        self.assertIn(child, runtime._children["parent"])
        restored = AgentRuntime(session_root=self.root)
        restored._restore_session_meta("parent")
        self.assertIn(child, restored._children["parent"])

    def test_fanout_approval_stays_open_and_works_after_runtime_restart(self):
        runtime = self.runtime(online=True)
        with mock.patch.object(llm, "chat", return_value=response(tool="literature.run",
                args={"request": "research"}, call_id="approval-1")):
            outcome = self.fanout(runtime, ["research"], "literature")
        child = outcome["branches"][0]["child_session"]
        self.assertEqual(runtime.sessions.snapshot(child)["status"], "waiting_approval")
        self.assertEqual(outcome["status"], "waiting_children")
        restored = AgentRuntime(session_root=self.root, offline=False, llm_enabled=True)
        with mock.patch.object(llm, "chat", return_value=response("completed")), \
             mock.patch.object(restored, "_run_named", return_value={"ok": True}):
            approved = restored.approve(child, "approval-1", "allow")
        self.assertEqual(approved["status"], "done")
        meta = json.loads((self.root / "parent" / "children" / f"{child}.meta.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "done")

    def test_cancel_parent_cancels_waiting_children(self):
        runtime = self.runtime(online=True)
        with mock.patch.object(llm, "chat", return_value=response(tool="literature.run",
                args={"request": "research"}, call_id="approval-2")):
            outcome = self.fanout(runtime, ["research"], "literature")
        child = outcome["branches"][0]["child_session"]
        runtime.cancel_session("parent")
        self.assertTrue((self.root / f"{child}.cancel").is_file())
        self.assertEqual(runtime.sessions.snapshot(child)["status"], "cancelled")

    def test_fanout_queues_more_than_four_branches_without_dropping_work(self):
        runtime = self.runtime()
        outcome = self.fanout(runtime, [str(i) for i in range(6)])
        self.assertEqual(outcome["children"], 6)
        self.assertEqual([branch["request"] for branch in outcome["branches"]], [str(i) for i in range(6)])

    def test_fanout_stops_queued_branches_after_cancel(self):
        runtime = self.runtime()
        entered, release = threading.Event(), threading.Event()
        executed = []

        def runner(name, request, **kwargs):
            executed.append(request)
            entered.set()
            self.assertTrue(release.wait(5))
            return {"ok": True}

        with mock.patch("src.agents.specialists.run", side_effect=runner):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.fanout, runtime, [str(i) for i in range(6)], max_parallel=1)
                self.assertTrue(entered.wait(5))
                runtime.cancel_session("parent")
                release.set()
                result = future.result(timeout=5)
        self.assertEqual(executed, ["0"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(sum(b["status"] == "cancelled" for b in result["branches"]), 6)

    def test_unexpected_branch_error_does_not_abandon_siblings(self):
        runtime = self.runtime()
        def runner(name, request, **kwargs):
            if request == "bad":
                raise RuntimeError("unexpected branch error")
            return {"ok": True}
        with mock.patch("src.agents.specialists.run", side_effect=runner):
            outcome = self.fanout(runtime, ["bad", "good"])
        self.assertEqual(len(outcome["branches"]), 2)
        self.assertFalse(outcome["branches"][0]["ok"])
        self.assertTrue(outcome["branches"][1]["ok"])
        self.assertEqual(outcome["status"], "partial")

    def test_all_failed_branches_do_not_report_success(self):
        runtime = self.runtime()
        with mock.patch("src.agents.specialists.run", return_value={"ok": False, "error": "failed"}):
            outcome = self.fanout(runtime, ["one", "two"])
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["status"], "error")

    def test_parent_driver_waits_for_child_approval_and_resumes(self):
        runtime = self.runtime(online=True)
        def chat(messages, **kwargs):
            if "文献猎手" in str(messages[0].get("content")):
                return response(tool="literature.run", call_id="child-approval", args={"request": "research"})
            return response(tool="subagent.spawn", call_id="spawn-child",
                            args={"agent": "literature", "request": "research"})
        with mock.patch.object(llm, "chat", side_effect=chat):
            outcome = runtime.start("delegate research")
        self.assertEqual(outcome["status"], "waiting_children")
        sid = outcome["session_id"]
        child = runtime._children[sid][0]
        with mock.patch.object(llm, "chat", return_value=response("completed")), \
             mock.patch.object(runtime, "_run_named", return_value={"ok": True}):
            self.assertEqual(runtime.approve(child, "child-approval", "allow")["status"], "done")
            self.assertEqual(runtime.continue_session(sid)["status"], "done")

    def test_corrupt_optional_chat_index_does_not_hide_durable_sessions(self):
        runtime = AgentRuntime(session_root=self.root / "sessions")
        runtime.sessions.append("known", "user_message", {"text": "durable request"})
        for damaged in ("{broken", "[]", '{"sessions": []}', '{"sessions": {"known": 9}}'):
            with self.subTest(damaged=damaged):
                (self.root / "chat_index.json").write_text(damaged, encoding="utf-8")
                self.assertEqual(runtime.sessions.list()[0]["summary"], "durable request")

    def test_bridge_routes_online_child_approval_to_online_runtime(self):
        from src import agent_runtime, bridge
        runtime = AgentRuntime(session_root=self.root / "sessions", offline=False, llm_enabled=True)
        child = runtime._begin_child("onlineparent", get_spec("literature"), "research")
        with mock.patch.object(agent_runtime, "DATA_DIR", self.root), \
             mock.patch.object(bridge, "_runtime") as factory:
            bridge._session_runtime(child)
        factory.assert_called_once_with(offline=False)


if __name__ == "__main__":
    unittest.main()
