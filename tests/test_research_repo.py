# -*- coding: utf-8 -*-
"""研究档案库单元测试：commit/log/status（git 缺失时整类跳过）。"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import research_repo
from src.orchestrator.engine import TaskContext

GIT_OK = shutil.which("git") is not None


@unittest.skipUnless(GIT_OK, "git 不可用")
class RepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(research_repo, "REPO_DIR",
                                        Path(self._tmp.name) / "repo")
        self._patch.start()
        self._patcher2 = mock.patch("src.orchestrator.engine.CHECKPOINT_DIR",
                                    Path(self._tmp.name) / "ckpt")
        self._patcher2.start()
        self.ctx = TaskContext("repotest01", "research_pipeline", {"topic": "测试课题"})
        self.ctx.artifacts = {
            "lit_filter": {"pool": [{"id": "W1", "title": "A Survey",
                                     "authors": ["John Smith"], "year": 2025,
                                     "venue": "J", "doi": "10.1/x", "score": 0.9}]},
            "extract": {"digest_md": "# 摘要整理稿"},
            "review_frame": {"outline_md": "# 综述框架"},
        }
        import src.service as service
        self._service = service
        self._service._tasks[self.ctx.task_id] = self.ctx

    def tearDown(self):
        self._patch.stop()
        self._patcher2.stop()
        self._tmp.cleanup()
        self._service._tasks.pop(self.ctx.task_id, None)

    def test_ensure_creates_repo(self):
        self.assertTrue(research_repo.ensure())
        self.assertTrue((research_repo.REPO_DIR / ".git").exists())

    def test_commit_and_log(self):
        head = research_repo.commit_task(self.ctx.task_id, "测试课题")
        self.assertTrue(head)
        log = research_repo.log_text(5)
        self.assertIn("repotest01", log)
        self.assertIn("测试课题", log)

    def test_recommit_returns_head(self):
        research_repo.commit_task(self.ctx.task_id, "测试课题")
        again = research_repo.commit_task(self.ctx.task_id, "测试课题")
        self.assertTrue(again)  # 无变更 → 返回最近提交

    def test_status_text(self):
        research_repo.commit_task(self.ctx.task_id, "测试课题")
        st = research_repo.status_text()
        self.assertIn("HEAD", st)

    def test_graceful_when_git_missing(self):
        with mock.patch.object(research_repo, "git_available", lambda: False):
            self.assertIn("不可用", research_repo.log_text())
            self.assertIn("不可用", research_repo.status_text())
            self.assertIsNone(research_repo.commit_task(self.ctx.task_id))


if __name__ == "__main__":
    unittest.main()
