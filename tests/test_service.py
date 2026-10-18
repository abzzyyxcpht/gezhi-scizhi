# -*- coding: utf-8 -*-
"""服务层单元测试：drop_works 人工干预语义（打桩 TaskContext，离线）。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.orchestrator import engine
from src.orchestrator.engine import TaskContext


class DropWorksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patcher = mock.patch.object(engine, "CHECKPOINT_DIR",
                                          Path(self._tmp.name))
        self._patcher.start()
        # service.drop_works 只依赖 ctx.artifacts / intervene（emit + save）
        self.ctx = TaskContext("droptest01", "research_pipeline", {"topic": "t"})
        self.ctx.artifacts = {"lit_filter": {"pool": [
            {"id": "W1", "title": "A", "score": 0.9},
            {"id": "W2", "title": "B", "score": 0.5},
            {"id": "W3", "title": "C", "score": 0.1},
        ]}}

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()
        import src.service as service
        service._tasks.pop(self.ctx.task_id, None)  # 清理任务注册表

    def _register(self):
        from src import service
        service._tasks[self.ctx.task_id] = self.ctx
        return service

    def test_drop_removes_and_records(self):
        service = self._register()
        n = service.drop_works(self.ctx.task_id, ["W1", "W3"])
        self.assertEqual(n, 2)
        pool = self.ctx.artifacts["lit_filter"]["pool"]
        self.assertEqual([w["id"] for w in pool], ["W2"])
        # 版本化留痕：修改前快照在 _history
        self.assertEqual(len(self.ctx.artifacts["_history"]), 1)

    def test_drop_unknown_ids_is_noop(self):
        service = self._register()
        n = service.drop_works(self.ctx.task_id, ["NOPE"])
        self.assertEqual(n, 0)
        self.assertEqual(len(self.ctx.artifacts["lit_filter"]["pool"]), 3)

    def test_artifacts_view_shape(self):
        service = self._register()
        self.ctx.artifacts["extract"] = {"digest_md": "# 摘要"}
        self.ctx.artifacts["citation"] = {"entries": [{"text": "[1] X"}]}
        art = service.artifacts_of(self.ctx.task_id)
        self.assertEqual(art["status"], "pending")
        self.assertEqual(len(art["pool"]), 3)
        self.assertEqual(art["digest_md"], "# 摘要")
        self.assertEqual(art["citations"], [{"text": "[1] X"}])


class TaskProgressTest(unittest.TestCase):
    def setUp(self):
        from src import service
        self.service = service
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        checkpoint_patch = mock.patch.object(engine, "CHECKPOINT_DIR", Path(self.directory.name))
        checkpoint_patch.start()
        self.addCleanup(checkpoint_patch.stop)
        self.ctx = TaskContext("progress-test", "research_pipeline", {"topic": "Fixture"})
        tasks_patch = mock.patch.object(service, "_tasks", {self.ctx.task_id: self.ctx})
        tasks_patch.start()
        self.addCleanup(tasks_patch.stop)
        actions_patch = mock.patch.dict(engine.ACTIONS, {
            "literature_recursive_filter": lambda ctx, params: {"pool": []},
            "extract_pool": lambda ctx, params: {},
            "build_review_frame": lambda ctx, params: {},
            "format_references": lambda ctx, params: {},
        })
        actions_patch.start()
        self.addCleanup(actions_patch.stop)

    def progress(self):
        row = self.service.list_tasks()[0]
        return row["done_nodes"], row["total_nodes"]

    def test_completed_workflow_counts_human_stage_without_artifact(self):
        engine.run(self.ctx)
        self.assertEqual(self.ctx.status, "waiting_human")
        self.assertEqual(self.progress(), (1, 5))
        # resume() advances the approval pointer; run synchronously in this fixture.
        with mock.patch.object(self.service, "_ensure_agents"), \
                mock.patch.object(self.service.threading, "Thread") as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            self.service.resume(self.ctx.task_id)
        self.assertEqual(self.ctx.status, "done")
        self.assertNotIn("human_review", self.ctx.artifacts)
        self.assertEqual(len(self.ctx.artifacts), 4)
        self.assertEqual(self.progress(), (5, 5))

    def test_partially_written_paused_node_is_not_complete(self):
        def pause_in_literature(ctx, params):
            ctx.artifacts["lit_filter"] = {"pool": [{"id": "partial"}]}
            ctx.artifacts["_evidence_graph"] = {"nodes": 1}
            raise engine.PauseRequested()

        with mock.patch.dict(engine.ACTIONS, {"literature_recursive_filter": pause_in_literature}):
            engine.run(self.ctx)
        self.assertEqual(self.ctx.status, "paused")
        self.assertEqual(self.ctx.pointer, "lit_filter")
        self.assertEqual(self.progress(), (0, 5))

    def test_paused_after_approval_counts_human_stage_not_partial_extract(self):
        engine.run(self.ctx)

        def pause_in_extract(ctx, params):
            ctx.artifacts["extract"] = {"records": []}
            ctx.artifacts["_evidence_graph"] = {"nodes": 3}
            raise engine.PauseRequested()

        with mock.patch.dict(engine.ACTIONS, {"extract_pool": pause_in_extract}), \
                mock.patch.object(self.service, "_ensure_agents"), \
                mock.patch.object(self.service.threading, "Thread") as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            self.service.resume(self.ctx.task_id)
        self.assertEqual(self.ctx.pointer, "extract")
        self.assertEqual(self.ctx.status, "paused")
        self.assertEqual(self.progress(), (2, 5))
        # Checkpoint reload has no events, so progress cannot depend on them.
        self.service._tasks[self.ctx.task_id] = TaskContext.load(self.ctx.task_id)
        self.assertEqual(self.progress(), (2, 5))

    def test_pending_task_does_not_count_injected_artifacts_as_finished(self):
        self.ctx.artifacts = {"lit_filter": {}, "_evidence_graph": {}}
        self.assertEqual(self.progress(), (0, 5))

    def test_conditional_workflow_does_not_count_skipped_branch(self):
        workflow = {"nodes": [
            {"id": "choose", "type": "condition", "action": "choose",
             "on_true": "selected", "on_false": "skipped"},
            {"id": "skipped", "type": "tool", "action": "unused", "next": "finish"},
            {"id": "selected", "type": "tool", "action": "selected", "next": "finish"},
            {"id": "finish", "type": "tool", "action": "pause", "next": None},
        ]}

        def pause(ctx, params):
            raise engine.PauseRequested()

        with mock.patch.object(engine, "load_workflow", return_value=workflow), \
                mock.patch.object(self.service, "load_workflow", return_value=workflow), \
                mock.patch.object(self.service, "workflow_node_ids", return_value=[
                    "choose", "skipped", "selected", "finish"]), \
                mock.patch.dict(engine.ACTIONS, {
                    "choose": lambda ctx, params: {"branch": True},
                    "selected": lambda ctx, params: {}, "pause": pause}):
            engine.run(self.ctx)
            self.ctx.artifacts["_evidence_graph"] = {}
            self.assertEqual(self.ctx.pointer, "finish")
            self.assertEqual(self.progress(), (2, 4))


if __name__ == "__main__":
    unittest.main()
