"""Approval serialization using real threads/processes and temporary journals."""
import multiprocessing
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from src.agent_runtime import AgentRuntime


class ApprovalRuntime(AgentRuntime):
    def __init__(self, root, *, held=None, release=None, phase="recover"):
        super().__init__(session_root=root)
        self.held, self.release, self.phase = held, release, phase
        self._register("test.write", "write", "Fixture write", self.execute)

    def plan(self, request, mode="agent", agent="research"):
        return self._plan_payload(mode, request, [{"tool": "test.write", "arguments": {}}], agent)

    def hold(self, phase):
        if self.held is not None and self.phase == phase:
            self.held.set()
            if not self.release.wait(10):
                raise RuntimeError("Concurrency fixture was not released")

    def _recover_pending(self, session_id, tool_call_id):
        pending = super()._recover_pending(session_id, tool_call_id)
        if pending:
            self.hold("recover")
        return pending

    def execute(self, args):
        with (self.sessions.root / "effects.txt").open("a", encoding="utf-8") as stream:
            stream.write(args["__session_id"] + "\n")
        self.hold("execute")
        return {"ok": True}


def approve(runtime, waiting, *, started=None, finished=None):
    if started is not None:
        started.set()
    try:
        result = runtime.approve(waiting["session_id"], waiting["pending"]["id"], "allow")
        return "accepted", result["status"]
    except ValueError as exc:
        return "rejected", str(exc)
    finally:
        if finished is not None:
            finished.set()


def approval_worker(root, waiting, outcomes, held, release, started, finished):
    runtime = ApprovalRuntime(root, held=held, release=release)
    outcomes.put(approve(runtime, waiting, started=started, finished=finished))


def lock_holder(path, entered, release):
    from src.session_lock import session_lock
    with session_lock(path):
        entered.set()
        release.wait(30)


class SessionApprovalConcurrencyTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        environment = mock.patch.dict(os.environ, {"GEZHI_DATA_DIR": str(self.root / "data")})
        environment.start()
        self.addCleanup(environment.stop)

    def effects(self):
        path = self.root / "effects.txt"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def assert_single_execution(self, waiting, results):
        self.assertEqual(sorted(result[0] for result in results), ["accepted", "rejected"])
        self.assertEqual(self.effects(), [waiting["session_id"]])
        runtime = ApprovalRuntime(self.root)
        events = runtime.sessions.read(waiting["session_id"])
        self.assertEqual(sum(event["type"] == "permission_granted" for event in events), 1)
        self.assertEqual(sum(event["type"] == "tool_call" for event in events), 1)
        self.assertEqual(runtime.sessions.snapshot(waiting["session_id"])["status"], "done")

    def test_threads_on_same_or_distinct_runtime_execute_approval_once(self):
        for shared in (False, True):
            with self.subTest(shared_runtime=shared):
                held, release, started = threading.Event(), threading.Event(), threading.Event()
                first = ApprovalRuntime(self.root, held=held, release=release)
                waiting = first.start("Concurrent approval")
                second = first if shared else ApprovalRuntime(self.root)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first_result = pool.submit(approve, first, waiting)
                    try:
                        self.assertTrue(held.wait(5))
                        second_result = pool.submit(approve, second, waiting, started=started)
                        self.assertTrue(started.wait(5))
                        # Give the competing request the chance to read the same pending log.
                        try:
                            second_result.result(timeout=0.2)
                        except TimeoutError:
                            pass
                    finally:
                        release.set()
                    results = [first_result.result(timeout=5), second_result.result(timeout=5)]
                self.assert_single_execution(waiting, results)
                (self.root / "effects.txt").unlink()

    def test_lock_remains_held_until_tool_execution_finishes(self):
        held, release, started, finished = (threading.Event() for _ in range(4))
        first = ApprovalRuntime(self.root, held=held, release=release, phase="execute")
        waiting = first.start("Hold through execution")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_result = pool.submit(approve, first, waiting)
            try:
                self.assertTrue(held.wait(5))
                second_result = pool.submit(approve, ApprovalRuntime(self.root), waiting,
                                            started=started, finished=finished)
                self.assertTrue(started.wait(5))
                self.assertFalse(finished.wait(0.2), "Competing approval bypassed execution lock")
            finally:
                release.set()
            results = [first_result.result(timeout=5), second_result.result(timeout=5)]
        self.assert_single_execution(waiting, results)

    def test_different_sessions_can_execute_while_one_session_is_locked(self):
        held, release = threading.Event(), threading.Event()
        first = ApprovalRuntime(self.root, held=held, release=release, phase="execute")
        second = ApprovalRuntime(self.root)
        first_waiting, second_waiting = first.start("Slow"), second.start("Independent")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_result = pool.submit(approve, first, first_waiting)
            try:
                self.assertTrue(held.wait(5))
                result = pool.submit(approve, second, second_waiting).result(timeout=3)
                self.assertEqual(result, ("accepted", "done"))
            finally:
                release.set()
            self.assertEqual(first_result.result(timeout=5), ("accepted", "done"))
        self.assertCountEqual(self.effects(), [first_waiting["session_id"], second_waiting["session_id"]])

    def test_spawned_processes_execute_approval_once(self):
        context = multiprocessing.get_context("spawn")
        held, release, started, finished = (context.Event() for _ in range(4))
        outcomes = context.Queue()
        waiting = ApprovalRuntime(self.root).start("Cross-process approval")
        first = context.Process(target=approval_worker, args=(
            self.root, waiting, outcomes, held, release, None, None))
        second = context.Process(target=approval_worker, args=(
            self.root, waiting, outcomes, None, None, started, finished))
        first.start()
        try:
            self.assertTrue(held.wait(10))
            second.start()
            self.assertTrue(started.wait(10))
            finished.wait(0.3)
        finally:
            release.set()
            for process in (first, second):
                if process.pid is not None:
                    process.join(10)
                    if process.is_alive():
                        process.terminate()
                        process.join(5)
        self.assertEqual(first.exitcode, 0)
        self.assertEqual(second.exitcode, 0)
        results = [outcomes.get(timeout=3), outcomes.get(timeout=3)]
        outcomes.close()
        outcomes.join_thread()
        self.assert_single_execution(waiting, results)

    def test_process_exit_releases_kernel_lock(self):
        context = multiprocessing.get_context("spawn")
        held, release = context.Event(), context.Event()
        waiting = ApprovalRuntime(self.root).start("Survive lock owner exit")
        path = self.root / (waiting["session_id"] + ".jsonl")
        process = context.Process(target=lock_holder, args=(path, held, release))
        process.start()
        try:
            self.assertTrue(held.wait(10))
        finally:
            process.terminate()
            process.join(5)
        self.assertEqual(approve(ApprovalRuntime(self.root), waiting), ("accepted", "done"))


if __name__ == "__main__":
    unittest.main()
