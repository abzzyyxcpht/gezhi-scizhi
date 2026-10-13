# -*- coding: utf-8 -*-
"""插件系统单元测试：发现、命令执行、工作流动作注册。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import plugins


class DiscoveryTest(unittest.TestCase):
    def test_builtin_discovered(self):
        plugins.load()
        names = set(plugins.PLUGINS)
        self.assertIn("bibtex", names)
        self.assertIn("termfreq", names)

    def test_commands_registered(self):
        cmds = {c["name"] for c in plugins.commands()}
        self.assertIn("bib", cmds)
        self.assertIn("freq", cmds)

    def test_action_registered_in_engine(self):
        from src.orchestrator.engine import ACTIONS
        plugins.load()
        self.assertIn("export_bibtex", ACTIONS)

    def test_summary_shape(self):
        s = plugins.summary()
        self.assertGreaterEqual(s["count"], 2)
        self.assertEqual(s["errors"], [])


class BibCommandTest(unittest.TestCase):
    def test_requires_task(self):
        out = plugins.run_command("bib", "")
        self.assertIn("打开任务", out)

    def test_render_bibtex(self):
        from src.orchestrator import engine
        from src.orchestrator.engine import TaskContext
        from src import service
        ctx = TaskContext("plugtest1", "research_pipeline", {"topic": "t"})
        ctx.artifacts = {"lit_filter": {"pool": [
            {"id": "W1", "title": "Agents Survey", "authors": ["John Smith"],
             "year": 2025, "venue": "J of AI", "doi": "10.1/x"}]}}
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(engine, "CHECKPOINT_DIR", Path(td)):
            service._tasks[ctx.task_id] = ctx
            try:
                out = plugins.run_command("bib", "--limit 5", ctx.task_id)
            finally:
                service._tasks.pop(ctx.task_id, None)
        self.assertIn("@article{Smith2025", out)
        self.assertIn("doi = {10.1/x}", out)

    def test_freq_command(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "t.txt"
            f.write_text("retrieval augmented generation agents agents", encoding="utf-8")
            out = plugins.run_command("freq", str(f), None)
        self.assertIn("agents", out)


if __name__ == "__main__":
    unittest.main()
