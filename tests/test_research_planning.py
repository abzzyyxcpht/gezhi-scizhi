"""Durable planning contracts, with deterministic time and isolated state."""
from __future__ import annotations

import importlib
import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


def _process_run(root, schedule_id, barrier, out):
    from src.research_planning import ResearchPlanning
    def dispatch(req):
        with (Path(root) / "launches.txt").open("a", encoding="utf-8") as stream:
            stream.write("launch\n")
        time.sleep(.1)
        return {"ok": True, "task_id": "process-task"}
    planner = ResearchPlanning(root, dispatch=dispatch, tasks=lambda: [])
    barrier.wait(timeout=10)
    out.put(planner.handle({"cmd": "schedule_run_now", "id": schedule_id}))


class PlanningTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("src.research_planning")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
        self.task_rows, self.notebook_rows, self.launches = [], [], []
        self.planner = self.make_planner()

    def dispatch(self, req):
        self.launches.append(req)
        tid = f"task-{len(self.launches)}"
        self.task_rows.append({"task_id": tid, "status": "running", "topic": req["topic"]})
        return {"ok": True, "task_id": tid}

    def make_planner(self, **kwargs):
        return self.module.ResearchPlanning(
            self.root, dispatch=kwargs.pop("dispatch", self.dispatch),
            tasks=lambda: list(self.task_rows), notebook=lambda: list(self.notebook_rows),
            audit=lambda tid: {"task_id": tid, "source": "recorded"},
            clock=lambda: self.now, sessions=lambda req: [], **kwargs)

    def call(self, cmd, **kwargs):
        response = self.planner.handle({"cmd": cmd, **kwargs})
        self.assertTrue(response.get("ok"), response)
        return response

    def schedule(self, **kwargs):
        return self.call("schedule_save", schedule={
            "title": "Literature follow-up", "workflow_id": "literature_scan",
            "topic": "research agents", "run_at": self.now.isoformat(),
            "offline": True, "enabled": True, **kwargs})["schedule"]

    def test_board_progress_is_todo_completion_and_preserves_task_status(self):
        self.task_rows.append({"task_id": "real", "status": "waiting_human"})
        self.call("todo_save", todo={"project_id": "p", "title": "Review", "status": "doing", "task_id": "real"})
        self.call("todo_save", todo={"project_id": "p", "title": "Read", "status": "done"})
        self.call("todo_save", todo={"project_id": "other", "title": "Other", "status": "done"})
        board = self.call("board_snapshot", project_id="p")
        self.assertEqual(board["progress"], .5)
        self.assertEqual(board["counts"]["total"], 2)
        review = next(t for t in board["todos"] if t["title"] == "Review")
        self.assertEqual(review["status"], "doing")
        self.assertEqual(review["task_status"], "waiting_human")
        self.assertEqual(self.call("board_snapshot", project_id="empty")["progress"], 0)

    def test_parallel_ticks_persist_claim_before_single_launch(self):
        self.schedule()
        entered, release = threading.Event(), threading.Event()
        def dispatch(req):
            state = json.loads((self.root / "research_planning.json").read_text(encoding="utf-8"))
            self.assertEqual(next(iter(state["schedules"].values()))["status"], "claiming")
            entered.set()
            release.wait(timeout=5)
            return self.dispatch(req)
        self.planner.dispatch = dispatch
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(self.planner.tick)
            self.assertTrue(entered.wait(timeout=3))
            results = list(pool.map(lambda _: self.planner.tick(), range(3)))
            release.set()
            first.result(timeout=3)
        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(len(self.launches), 1)
        self.task_rows[0]["status"] = "done"
        self.planner.tick()
        self.planner.tick()
        row = self.call("schedule_list")["schedules"][0]
        self.assertEqual(row["status"], "completed")
        self.assertFalse(self.planner.handle({"cmd": "schedule_run_now", "id": row["id"]})["ok"])
        self.assertEqual(len(self.launches), 1)

    def test_separate_processes_cannot_double_claim(self):
        row = self.schedule(enabled=False)
        context = multiprocessing.get_context("spawn")
        barrier, out = context.Barrier(2), context.Queue()
        workers = [context.Process(target=_process_run, args=(str(self.root), row["id"], barrier, out)) for _ in range(2)]
        try:
            for worker in workers:
                worker.start()
            responses = [out.get(timeout=15) for _ in workers]
            for worker in workers:
                worker.join(timeout=5)
            self.assertEqual(sum(r["ok"] for r in responses), 1)
            self.assertEqual((self.root / "launches.txt").read_text().count("launch"), 1)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=2)
            out.close()

    def test_approval_prevents_overlap_and_interval_starts_after_completion(self):
        row = self.schedule(interval_seconds=60, max_runs=2)
        self.planner.tick()
        self.task_rows[0]["status"] = "waiting_human"
        self.now += timedelta(hours=3)
        self.planner.tick()
        self.assertEqual(self.call("schedule_list")["schedules"][0]["status"], "waiting_human")
        self.assertFalse(self.planner.handle({"cmd": "schedule_run_now", "id": row["id"]})["ok"])
        self.assertEqual(len(self.launches), 1)
        self.task_rows[0]["status"] = "done"
        self.planner.tick()
        self.assertEqual(len(self.launches), 1)
        self.now += timedelta(seconds=60)
        self.planner.tick()
        self.assertEqual(len(self.launches), 2)

    def test_restart_requires_explicit_resume_and_does_not_catch_up(self):
        row = self.schedule(run_at=(self.now + timedelta(seconds=60)).isoformat())
        restarted = self.make_planner(host_id="another-host")
        self.now += timedelta(seconds=60)
        restarted.tick()
        self.assertEqual(self.launches, [])
        snapshot = restarted.handle({"cmd": "schedule_list"})["schedules"][0]
        self.assertTrue(snapshot["requires_resume"])
        response = restarted.handle({"cmd": "schedule_save", "schedule": {"id": row["id"], "enabled": True}})
        self.assertTrue(response["ok"], response)
        restarted.tick()
        self.assertEqual(len(self.launches), 1)

    def test_missed_deadline_needs_explicit_run_now(self):
        row = self.schedule()
        self.now += timedelta(hours=2)
        self.planner.tick()
        self.assertEqual(self.launches, [])
        self.assertEqual(self.call("schedule_list")["schedules"][0]["status"], "missed")
        self.call("schedule_run_now", id=row["id"])
        self.assertEqual(len(self.launches), 1)

    def test_ambiguous_launch_is_never_retried_or_deleted(self):
        def ambiguous(req):
            self.dispatch(req)
            raise RuntimeError("reply lost after start")
        self.planner.dispatch = ambiguous
        row = self.schedule(interval_seconds=60, max_runs=3)
        self.planner.tick()
        restarted = self.make_planner(host_id="restarted")
        restarted.tick()
        for request in ({"cmd": "schedule_run_now", "id": row["id"]},
                        {"cmd": "schedule_save", "schedule": {"id": row["id"], "enabled": True}},
                        {"cmd": "schedule_delete", "id": row["id"]}):
            self.assertFalse(restarted.handle(request)["ok"])
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(restarted.handle({"cmd": "schedule_list"})["schedules"][0]["status"], "uncertain")

    def test_notifications_deduplicate_persist_and_ignore_initial_history(self):
        self.task_rows.extend([{"task_id": "old", "status": "done"}, {"task_id": "new", "status": "running"}])
        self.planner.observe_tasks()
        self.assertEqual(self.call("notifications_list")["notifications"], [])
        self.task_rows[1]["status"] = "done"
        self.planner.observe_tasks()
        self.planner.observe_tasks()
        notices = self.call("notifications_list", unread=True)["notifications"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["task_id"], "new")
        other = self.make_planner(host_id="reader")
        other.notify(notices[0]["event_key"], "Duplicate", "duplicate")
        self.assertEqual(len(other.handle({"cmd": "notifications_list"})["notifications"]), 1)
        self.call("notification_read", all=True)
        self.assertEqual(self.call("notifications_list", unread=True)["notifications"], [])
        self.call("notifications_settings", enabled=False)
        self.planner.notify("disabled", "Hidden", "hidden")
        self.assertEqual(len(self.call("notifications_list")["notifications"]), 1)

    def test_handoff_is_scoped_and_flags_missing_evidence_and_failed_results(self):
        self.call("todo_save", todo={"project_id": "p", "session_id": "s", "title": "Check experiment", "task_id": "ours"})
        self.call("todo_save", todo={"project_id": "other", "title": "Secret other", "task_id": "theirs"})
        self.task_rows.extend([{"task_id": "ours", "status": "failed"}, {"task_id": "theirs", "status": "done"}])
        self.notebook_rows.extend([
            {"id": "ours-note", "task_id": "ours", "kind": "hypothesis", "title": "Unverified hypothesis", "status": "failed", "evidence": [], "result": "Failed run"},
            {"id": "other-note", "task_id": "theirs", "title": "PRIVATE OTHER", "evidence": []},
            {"id": "unlinked", "task_id": "", "title": "PRIVATE UNLINKED", "evidence": []}])
        result = self.call("research_handoff", project_id="p", session_id="s")
        data = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
        markdown = Path(result["markdown_path"]).read_text(encoding="utf-8")
        self.assertEqual([r["id"] for r in data["notebook_records"]], ["ours-note"])
        self.assertEqual(data["manifest"]["reproducibility_verification"], "not_performed")
        self.assertEqual({r["code"] for r in data["evidence_deficits"]}, {"no_evidence", "failed_result", "task_failed"})
        self.assertNotIn("PRIVATE", markdown)
        self.assertNotIn("PRIVATE", json.dumps(data))
        self.assertEqual(Path(result["json_path"]).parent, self.root / "exports")
        self.assertFalse(self.planner.handle({"cmd": "research_handoff", "path": "../elsewhere"})["ok"])

    def test_invalid_inputs_and_corrupt_store_do_not_dispatch(self):
        for values in ({"run_at": "2026-09-19T01:00:00"}, {"interval_seconds": 10},
                       {"interval_seconds": float("nan")}, {"max_runs": 0},
                       {"enabled": "true"}, {"workflow_id": "../run"}):
            request = {"title": "Bad", "topic": "x", "run_at": self.now.isoformat(), **values}
            self.assertFalse(self.planner.handle({"cmd": "schedule_save", "schedule": request})["ok"])
        path = self.root / "research_planning.json"
        path.write_text("broken", encoding="utf-8")
        self.assertFalse(self.planner.handle({"cmd": "todo_save", "todo": {"title": "x"}})["ok"])
        self.assertEqual(path.read_text(), "broken")
        self.assertEqual(self.launches, [])

    def test_invalid_command_types_and_partial_store_are_reported(self):
        for request in (None, {}, {"cmd": []}, {"cmd": "todo_save", "todo": {"title": "x", "due_at": False}}):
            self.assertFalse(self.planner.handle(request)["ok"])
        self.call("todo_save", todo={"title": "Recorded"})
        path = self.root / "research_planning.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        next(iter(state["todos"].values())).pop("status")
        path.write_text(json.dumps(state), encoding="utf-8")
        response = self.planner.handle({"cmd": "board_snapshot"})
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "corrupt_store")

    def test_storage_failure_before_launch_never_dispatches(self):
        self.schedule()
        with mock.patch.object(self.module, "write_json", side_effect=OSError("disk full")):
            self.assertFalse(self.planner.tick()["ok"])
        self.assertEqual(self.launches, [])

    def test_storage_failure_after_launch_remains_claimed_and_cannot_retry(self):
        self.schedule(interval_seconds=60, max_runs=2)
        original = self.module.write_json
        def persist(path, value):
            if next(iter(value["schedules"].values()))["status"] == "running":
                raise OSError("write interrupted")
            return original(path, value)
        with mock.patch.object(self.module, "write_json", side_effect=persist):
            self.assertFalse(self.planner.tick()["ok"])
        self.planner.tick()
        restarted = self.make_planner(host_id="replacement")
        restarted.tick()
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(restarted.handle({"cmd": "schedule_list"})["schedules"][0]["status"], "uncertain")

    def test_disable_stops_future_runs_but_preserves_waiting_task(self):
        row = self.schedule(interval_seconds=60, max_runs=2)
        self.planner.tick()
        self.task_rows[0]["status"] = "waiting_human"
        self.call("schedule_save", schedule={"id": row["id"], "enabled": False})
        self.assertFalse(self.planner.handle({"cmd": "schedule_save", "schedule": {"id": row["id"], "topic": "changed"}})["ok"])
        self.now += timedelta(hours=1)
        self.planner.tick()
        self.assertEqual(self.task_rows[0]["status"], "waiting_human")
        self.task_rows[0]["status"] = "done"
        self.planner.tick()
        self.now += timedelta(hours=1)
        self.planner.tick()
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.call("schedule_list")["schedules"][0]["status"], "disabled")

    def test_parallel_notifications_are_deduplicated_and_muted_events_stay_muted(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: self.planner.notify("same", "Completed", "Recorded"), range(12)))
        self.assertEqual(len(self.call("notifications_list")["notifications"]), 1)
        self.call("notifications_settings", enabled=False)
        self.planner.notify("muted", "Muted", "Muted")
        self.call("notifications_settings", enabled=True)
        self.assertIsNone(self.planner.notify("muted", "Muted", "Muted")["notification"])

    def test_handoff_includes_project_session_links_and_redacts_secrets(self):
        self.planner.sessions = lambda req: [{"session_id": "s", "project_id": "p", "task_id": "session-task"}]
        self.task_rows.append({"task_id": "session-task", "status": "done"})
        self.notebook_rows.append({"id": "h", "task_id": "session-task", "title": "Hypothesis", "evidence": [],
                                   "question": "token=SECRET_SENTINEL"})
        response = self.call("research_handoff", project_id="p")
        self.assertEqual(response["handoff"]["notebook_records"][0]["id"], "h")
        self.assertNotIn("SECRET_SENTINEL", Path(response["json_path"]).read_text(encoding="utf-8"))

    def test_scheduler_start_is_idempotent_and_stop_joins_worker(self):
        self.planner.start_scheduler(.1)
        self.addCleanup(self.planner.stop_scheduler)
        worker = self.planner._worker
        self.planner.start_scheduler(.1)
        self.assertIs(self.planner._worker, worker)
        self.assertFalse(self.planner.stop_scheduler()["running"])

    def test_session_observer_ignores_history_and_notifies_approval_completion(self):
        from src.agent_runtime import SessionStore
        store = SessionStore(self.root / "sessions")
        store.append("old", "completed", {"status": "done"})
        store.append("active", "user_message", {"text": "Review evidence"})
        store.append("active", "permission_requested", {"tool_call_id": "approval-1", "tool": "research.create"})
        self.planner.sessions = lambda req: [{"session_id": "active", "project_id": "p", "title": "Evidence review"}]
        self.planner.observe_sessions()
        self.assertEqual(self.call("notifications_list")["notifications"], [])
        store.append("active", "permission_granted", {"tool_call_id": "approval-1"})
        store.append("active", "completed", {"status": "done"})
        self.planner.observe_sessions()
        self.planner.observe_sessions()
        notices = self.call("notifications_list", project_id="p")["notifications"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["session_id"], "active")
        restarted = self.make_planner(host_id="another-process")
        restarted.observe_sessions()
        self.assertEqual(len(self.call("notifications_list")["notifications"]), 1)
        store.append("active", "user_message", {"text": "Continue"})
        store.append("active", "error", {"error": "Recorded failure"})
        restarted.observe_sessions()
        self.assertEqual(len(self.call("notifications_list")["notifications"]), 2)

    def test_session_observer_catches_new_fast_session_after_baseline(self):
        from src.agent_runtime import SessionStore
        self.planner.observe_sessions()
        store = SessionStore(self.root / "sessions")
        store.append("fast", "user_message", {"text": "Quick check"})
        store.append("fast", "completed", {"status": "done"})
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _: self.planner.observe_sessions(), range(6)))
        self.assertEqual(len(self.call("notifications_list")["notifications"]), 1)

    def test_singleton_switches_data_directory_and_stops_previous_worker(self):
        from src import config
        old = self.make_planner()
        old.start_scheduler(.1)
        self.addCleanup(old.stop_scheduler)
        next_root = self.root / "new-data"
        with mock.patch.object(self.module, "_instance", old), mock.patch.object(config, "DATA_DIR", next_root):
            self.module.handle({"cmd": "todo_save", "todo": {"title": "New location"}})
            self.assertEqual(self.module._instance.data_dir, next_root)
            self.assertFalse(old._worker.is_alive())
            self.assertTrue((next_root / "research_planning.json").exists())

    def test_real_offline_workflow_completes_and_notifies(self):
        from src import service, workbench, llm
        from src.orchestrator import engine
        from src.agents import literature
        checkpoints = self.root / "checkpoints"
        checkpoints.mkdir()
        with mock.patch.object(service, "_tasks", {}), \
                mock.patch.object(service, "CHECKPOINT_DIR", checkpoints), \
                mock.patch.object(engine, "CHECKPOINT_DIR", checkpoints), \
                mock.patch.object(llm, "chat_json", side_effect=AssertionError("No LLM")), \
                mock.patch.object(literature, "_dual_source_search", side_effect=AssertionError("No network")):
            wb = workbench.Workbench(self.root)
            self.addCleanup(wb.close)
            self.planner.dispatch = wb.handle
            self.planner.tasks = service.list_tasks
            row = self.schedule()
            self.planner.tick()
            tid = self.call("schedule_list")["schedules"][0]["task_id"]
            deadline = time.monotonic() + 12
            while service.get(tid).status not in {"done", "failed"} and time.monotonic() < deadline:
                time.sleep(.025)
            self.planner.tick()
            self.assertEqual(service.get(tid).status, "done")
            self.assertEqual(self.call("schedule_list")["schedules"][0]["id"], row["id"])
            self.assertEqual(self.call("schedule_list")["schedules"][0]["status"], "completed")
            self.assertEqual(len(self.call("notifications_list")["notifications"]), 1)


if __name__ == "__main__":
    unittest.main()
