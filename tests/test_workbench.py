"""Workbench contracts: no network, bounded loops, safe session exports."""
from __future__ import annotations

import importlib
import errno
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from src import service
from src.orchestrator import engine


class WorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("src.workbench")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.checkpoints = self.root / "checkpoints"
        self.checkpoints.mkdir()
        for target, name, value in ((service, "_tasks", {}),
                                     (service, "CHECKPOINT_DIR", self.checkpoints),
                                     (engine, "CHECKPOINT_DIR", self.checkpoints)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.wb = self.module.Workbench(self.root)
        self.addCleanup(self.wb.close)

    def wait_for(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.025)
        self.fail("Timed out waiting for workbench state")

    def call(self, cmd, **kwargs):
        result = self.wb.handle({"cmd": cmd, **kwargs})
        self.assertIs(result.get("ok"), True, result)
        return result

    @staticmethod
    def sharing_error(code=5):
        error = PermissionError(errno.EACCES, "fixture sharing violation")
        error.winerror = code
        return error

    def test_atomic_save_retries_transient_windows_sharing_errors(self):
        original_replace = Path.replace
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                attempts = 0

                def transient(source, destination):
                    nonlocal attempts
                    attempts += 1
                    if attempts < 3:
                        raise self.sharing_error(code)
                    return original_replace(source, destination)

                with mock.patch.object(Path, "replace", autospec=True, side_effect=transient), \
                        mock.patch.object(self.module.time, "sleep") as sleep:
                    with self.wb._lock:
                        self.wb._persist_locked()
                self.assertEqual(attempts, 3)
                self.assertEqual(sleep.call_count, 2)
                self.assertEqual(json.loads(self.wb._path.read_text(encoding="utf-8")),
                                 {"version": 1, "loops": []})
                self.assertEqual(list(self.root.glob("workbench_loops.*.tmp")), [])

    def test_atomic_save_bounds_permanent_windows_denial_and_cleans_temp(self):
        self.wb._path.write_text("previous state", encoding="utf-8")
        failure = self.sharing_error()
        with mock.patch.object(Path, "replace", side_effect=failure) as replace, \
                mock.patch.object(self.module.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as caught, self.wb._lock:
                self.wb._persist_locked()
        self.assertIs(caught.exception, failure)
        self.assertGreater(replace.call_count, 1)
        self.assertLessEqual(replace.call_count, 6)
        self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), .5)
        self.assertEqual(self.wb._path.read_text(encoding="utf-8"), "previous state")
        self.assertEqual(list(self.root.glob("workbench_loops.*.tmp")), [])

    def test_atomic_save_does_not_retry_other_io_errors_or_replace_prior_state(self):
        for failure in (OSError(errno.ENOSPC, "disk full"),
                        PermissionError(errno.EACCES, "non-Windows permission error")):
            with self.subTest(error=str(failure)):
                self.wb._path.write_text("previous state", encoding="utf-8")
                with mock.patch.object(Path, "replace", side_effect=failure) as replace, \
                        mock.patch.object(self.module.time, "sleep") as sleep:
                    with self.assertRaises(OSError) as caught, self.wb._lock:
                        self.wb._persist_locked()
                self.assertIs(caught.exception, failure)
                replace.assert_called_once()
                sleep.assert_not_called()
                self.assertEqual(self.wb._path.read_text(encoding="utf-8"), "previous state")
                self.assertEqual(list(self.root.glob("workbench_loops.*.tmp")), [])

    def test_atomic_save_cleans_partial_temp_when_writing_fails(self):
        self.wb._path.write_text("previous state", encoding="utf-8")
        write_text = Path.write_text
        failure = OSError(errno.ENOSPC, "fixture disk full")

        def interrupted_write(path, text, **kwargs):
            write_text(path, text[:5], **kwargs)
            raise failure

        with mock.patch.object(Path, "write_text", autospec=True, side_effect=interrupted_write), \
                mock.patch.object(Path, "replace") as replace:
            with self.assertRaises(OSError) as caught, self.wb._lock:
                self.wb._persist_locked()
        self.assertIs(caught.exception, failure)
        replace.assert_not_called()
        self.assertEqual(self.wb._path.read_text(encoding="utf-8"), "previous state")
        self.assertEqual(list(self.root.glob("workbench_loops.*.tmp")), [])

    def test_atomic_save_cleanup_error_does_not_mask_original_io_failure(self):
        failure = OSError(errno.ENOSPC, "primary disk error")
        with mock.patch.object(Path, "replace", side_effect=failure), \
                mock.patch.object(Path, "unlink", side_effect=PermissionError("cleanup denied")):
            with self.assertRaises(OSError) as caught, self.wb._lock:
                self.wb._persist_locked()
        self.assertIs(caught.exception, failure)

    def test_initial_loop_save_failure_leaves_no_running_loop_or_task(self):
        with mock.patch.object(Path, "replace", side_effect=OSError(errno.ENOSPC, "disk full")), \
                mock.patch.object(service, "create_task") as create:
            result = self.wb.handle({"cmd": "loop_start", "topic": "agents", "offline": True})
        self.assertFalse(result["ok"])
        self.assertIn("disk full", result["error"])
        self.assertEqual(self.wb.loops(), [])
        create.assert_not_called()
        self.assertEqual(list(self.root.glob("workbench_loops.*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing behavior")
    def test_atomic_save_recovers_when_windows_reader_releases_target(self):
        self.wb._path.write_text("previous state", encoding="utf-8")
        opened, release = threading.Event(), threading.Event()

        def reader():
            with self.wb._path.open("rb"):
                opened.set()
                release.wait(timeout=5)

        worker = threading.Thread(target=reader, daemon=True)
        worker.start()
        self.assertTrue(opened.wait(timeout=2))

        def release_reader(delay):
            release.set()
            worker.join(timeout=2)

        try:
            with mock.patch.object(self.module.time, "sleep", side_effect=release_reader) as sleep:
                with self.wb._lock:
                    self.wb._persist_locked()
            self.assertGreater(sleep.call_count, 0)
            self.assertEqual(json.loads(self.wb._path.read_text(encoding="utf-8"))["version"], 1)
            self.assertEqual(list(self.root.glob("workbench_loops.*.tmp")), [])
        finally:
            release.set()
            worker.join(timeout=2)

    def test_catalog_has_four_different_executable_paths(self):
        rows = self.call("workflows")["workflows"]
        self.assertEqual({r["id"] for r in rows},
                         {"research_pipeline", "literature_scan", "evidence_review", "reproduce"})
        paths = {tuple(s["id"] for s in r["stages"]) for r in rows}
        self.assertEqual(len(paths), 4)
        for row in rows:
            self.assertTrue(row["title"])
            self.assertTrue(row["description"])

    def test_offline_scan_completes_without_network_or_llm(self):
        from src import llm
        from src.agents import literature
        with mock.patch.object(llm, "available", return_value=True), \
                mock.patch.object(llm, "chat_json", side_effect=AssertionError("LLM forbidden")), \
                mock.patch.object(literature, "_dual_source_search", side_effect=AssertionError("Network forbidden")):
            tid = self.call("workflow_run", workflow_id="literature_scan",
                            topic="research agents", offline=True)["task_id"]
            self.wait_for(lambda: service.get(tid).status in {"done", "failed"})
        ctx = service.get(tid)
        self.assertEqual(ctx.status, "done", ctx.events)
        self.assertGreater(len(ctx.artifacts["citation"]["entries"]), 0)
        self.assertTrue(ctx.artifacts["lit_filter"]["offline"])

    def test_offline_pipeline_keeps_human_gate_and_extraction_offline(self):
        from src import llm
        with mock.patch.object(llm, "available", return_value=True), \
                mock.patch.object(llm, "chat_json", side_effect=AssertionError("LLM forbidden")) as chat:
            tid = self.call("workflow_run", workflow_id="research_pipeline",
                            topic="research agents", offline=True)["task_id"]
            self.wait_for(lambda: service.get(tid).status == "waiting_human")
            self.assertNotIn("extract", service.get(tid).artifacts)
            service.resume(tid)
            self.wait_for(lambda: service.get(tid).status in {"done", "failed"})
            self.assertEqual(service.get(tid).status, "done")
            chat.assert_not_called()
            self.assertTrue(service.get(tid).artifacts["extract"]["records"])

    def test_workflow_validation_cannot_escape_templates(self):
        for workflow in ("../settings", "unknown"):
            self.assertFalse(self.wb.handle({"cmd": "workflow_run", "workflow_id": workflow,
                                           "topic": "test"})["ok"])
        self.assertFalse(self.wb.handle({"cmd": "workflow_run", "topic": " "})["ok"])
        self.assertFalse(self.wb.handle({"cmd": "workflow_run", "topic": "x", "offline": "false"})["ok"])

    def test_service_restore_never_replaces_active_context(self):
        ctx = engine.TaskContext("active", "research_pipeline", {"topic": "live"})
        ctx.status = "running"
        ctx.save()
        service._tasks[ctx.task_id] = ctx
        service.restore_tasks()
        self.assertIs(service.get("active"), ctx)
        self.assertEqual(service.get("active").status, "running")

    def test_loop_is_bounded_and_no_tasks_overlap(self):
        loop = self.call("loop_start", workflow_id="literature_scan", topic="agents",
                         offline=True, interval_seconds=1, max_iterations=2)["loop"]
        lid = loop["loop_id"]
        self.wait_for(lambda: self.wb.loops()[0]["status"] == "completed")
        row = self.wb.loops()[0]
        self.assertEqual(row["loop_id"], lid)
        self.assertEqual(row["iteration"], 2)
        self.assertEqual(len(service.list_tasks()), 2)
        tasks = sorted(service._tasks.values(), key=lambda t: t.started_at)
        self.assertLessEqual(tasks[0].finished_at, tasks[1].started_at)

    def test_loop_cannot_approve_human_gate(self):
        loop = self.call("loop_start", workflow_id="research_pipeline", topic="agents",
                         interval_seconds=1, max_iterations=2)["loop"]
        self.wait_for(lambda: self.wb.loops()[0]["status"] == "waiting_human")
        self.call("loop_resume", loop_id=loop["loop_id"])
        self.assertEqual(service.get(self.wb.loops()[0]["task_id"]).status, "waiting_human")
        self.assertEqual(len(service.list_tasks()), 1)
        self.call("loop_stop", loop_id=loop["loop_id"])
        self.assertEqual(self.wb.loops()[0]["status"], "stopped")

    def test_restart_loads_loop_as_interrupted_and_never_runs(self):
        loop = self.call("loop_start", workflow_id="research_pipeline", topic="agents",
                         interval_seconds=1, max_iterations=2)["loop"]
        self.wait_for(lambda: self.wb.loops()[0]["status"] == "waiting_human")
        second = self.module.Workbench(self.root)
        self.addCleanup(second.close)
        self.assertEqual(second.loops()[0]["status"], "interrupted")
        self.assertEqual(len(service.list_tasks()), 1)
        self.call("loop_stop", loop_id=loop["loop_id"])

    def test_stop_wins_over_a_late_worker_status_update(self):
        entered = threading.Event()
        release = threading.Event()
        update = self.wb._update

        def delayed_update(loop_id, **changes):
            if changes.get("status") == "waiting_human":
                entered.set()
                release.wait(timeout=5)
            return update(loop_id, **changes)

        with mock.patch.object(self.wb, "_update", side_effect=delayed_update):
            loop = self.call("loop_start", topic="agents", max_iterations=2,
                             interval_seconds=1)["loop"]
            self.assertTrue(entered.wait(timeout=5))
            self.call("loop_stop", loop_id=loop["loop_id"])
            release.set()
            self.wb.close()
        self.assertEqual(self.wb.loops()[0]["status"], "stopped")

    def test_failed_or_cancelled_task_stops_loop_without_new_iteration(self):
        for status in ("failed", "cancelled"):
            with self.subTest(status=status):
                loop = self.call("loop_start", topic="agents", max_iterations=2,
                                 interval_seconds=1)["loop"]
                self.wait_for(lambda: self.wb.loops()[0]["status"] == "waiting_human")
                service.get(self.wb.loops()[0]["task_id"]).status = status
                self.wait_for(lambda: self.wb.loops()[0]["status"] == status)
                self.assertEqual(self.wb.loops()[0]["iteration"], 1)
                self.assertFalse(self.wb.handle({"cmd": "loop_resume", "loop_id": loop["loop_id"]})["ok"])

    def test_stop_resume_observes_current_paused_task_without_duplicate(self):
        loop = self.call("loop_start", topic="agents", max_iterations=2,
                         interval_seconds=1)["loop"]
        self.wait_for(lambda: self.wb.loops()[0]["status"] == "waiting_human")
        task_id = self.wb.loops()[0]["task_id"]
        self.call("loop_stop", loop_id=loop["loop_id"])
        self.call("loop_resume", loop_id=loop["loop_id"])
        self.assertEqual(self.wb.loops()[0]["task_id"], task_id)
        self.assertEqual(self.wb.loops()[0]["iteration"], 1)
        self.assertEqual(service.get(task_id).status, "waiting_human")

    def test_export_redacts_json_text_and_preserves_numeric_token_usage(self):
        result = self.module.redact({"tokens": 127, "token_count": 33,
                                     "note": '{"api_key": "HIDDEN_SECRET", "access_token": "TOKEN_SECRET"}'})
        self.assertEqual(result["tokens"], 127)
        self.assertEqual(result["token_count"], 33)
        self.assertNotIn("HIDDEN_SECRET", result["note"])
        self.assertNotIn("TOKEN_SECRET", result["note"])

    def test_export_redacts_basic_auth_and_generic_token_text(self):
        value = self.module.redact({"trace": "Authorization: Basic dXNlcjpzZWNyZXQ=",
                                    "error": "token=TOKEN_SECRET secret=SECRET_VALUE"})
        self.assertNotIn("dXNlcjpzZWNyZXQ=", value["trace"])
        self.assertNotIn("TOKEN_SECRET", value["error"])
        self.assertNotIn("SECRET_VALUE", value["error"])

    def test_terminal_opens_only_the_project_directory_without_shell(self):
        with mock.patch.object(self.module.subprocess, "Popen") as popen:
            result = self.call("terminal_open")
        self.assertTrue(result["opened"])
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.module.config.ROOT_DIR.resolve()))
        self.assertFalse(popen.call_args.kwargs.get("shell", False))
        self.assertIsInstance(popen.call_args.args[0], list)

    def test_restore_stale_running_checkpoint_is_paused_without_rewriting(self):
        ctx = engine.TaskContext("previous-process", "research_pipeline", {"topic": "saved"})
        ctx.status = "running"
        ctx.save()
        source = (self.checkpoints / "previous-process.json").read_bytes()
        service.restore_tasks()
        self.assertEqual(service.get(ctx.task_id).status, "paused")
        self.assertEqual((self.checkpoints / "previous-process.json").read_bytes(), source)

    def test_invalid_loop_limits_and_terminal_input_rejected(self):
        for payload in ({"max_iterations": 0}, {"max_iterations": 101},
                        {"max_iterations": True}, {"interval_seconds": 0},
                        {"interval_seconds": "NaN"}):
            self.assertFalse(self.wb.handle({"cmd": "loop_start", "topic": "x", **payload})["ok"])
        with mock.patch.object(self.module.subprocess, "Popen") as popen:
            self.assertFalse(self.wb.handle({"cmd": "terminal_open", "command": "whoami"})["ok"])
            popen.assert_not_called()

    def test_session_exports_redact_nested_secrets_preserve_counts_and_source(self):
        from src.agent_runtime import SessionStore
        store = SessionStore(self.root / "sessions")
        store.append("sess_safe", "user_message", {"text": "test"})
        store.append("sess_safe", "tool_result", {"result": {
            "api_key": "KEY_SENTINEL", "nested": [{"access_token": "TOKEN_SENTINEL"}],
            "Authorization": "Bearer BEARER_SENTINEL", "input_tokens": 42,
            "total_tokens": 99, "note": "api_key=INLINE_SENTINEL"}})
        source = (self.root / "sessions" / "sess_safe.jsonl").read_bytes()
        for fmt in ("json", "markdown"):
            result = self.call("session_export", session_id="sess_safe", format=fmt)
            exported = Path(result["path"]).read_text(encoding="utf-8")
            for secret in ("KEY_SENTINEL", "TOKEN_SENTINEL", "BEARER_SENTINEL", "INLINE_SENTINEL"):
                self.assertNotIn(secret, exported)
            self.assertIn("42", exported)
            self.assertIn("99", exported)
            self.assertIn("sess_safe", exported)
            self.assertIn("exported_at", exported)
        self.assertEqual(source, (self.root / "sessions" / "sess_safe.jsonl").read_bytes())
        for sid in ("../settings", "C:\\secret", "", "missing"):
            self.assertFalse(self.wb.handle({"cmd": "session_export", "session_id": sid})["ok"])


if __name__ == "__main__":
    unittest.main()
