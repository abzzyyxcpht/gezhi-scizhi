# -*- coding: utf-8 -*-
"""插件系统单元测试：发现、命令执行、工作流动作注册。"""
from __future__ import annotations

import re
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
    def _render_keys(self, pool, limit=None):
        from plugins.bibtex_export import _render
        return re.findall(r"@article\{([^,]+),", _render(pool, len(pool) if limit is None else limit))

    def test_same_author_year_gets_distinct_keys(self):
        pool = [{"title": f"Paper {i}", "authors": ["John Smith"], "year": 2024}
                for i in range(3)]
        self.assertEqual(["Smith2024", "Smith2024a", "Smith2024b"],
                         self._render_keys(pool))

    def test_many_collisions_keep_every_entry_and_are_repeatable(self):
        pool = [{"title": f"Paper {i}", "authors": ["John Smith"], "year": 2024}
                for i in range(80)]
        keys = self._render_keys(pool)
        self.assertEqual(len(pool), len(keys))
        self.assertEqual(len(pool), len(set(keys)))
        self.assertEqual(keys, self._render_keys(pool))

    def test_generated_suffix_does_not_take_later_original_key(self):
        repeated = {"authors": ["John Smith"], "year": 2024}
        pool = [repeated, repeated]
        pool.extend({"authors": [f"Other{i}"], "year": 2024} for i in range(24))
        pool.append(repeated)
        keys = self._render_keys(pool)
        self.assertEqual("Smith2024a", keys[26])
        self.assertEqual("Smith2024b", keys[1])
        self.assertEqual(len(pool), len(set(keys)))

    def test_distinct_keys_retain_existing_identifiers(self):
        pool = [{"authors": [f"Author{i}"], "year": 2024} for i in range(40)]
        keys = self._render_keys(pool)
        self.assertEqual("Author02024", keys[0])
        self.assertEqual("Author252024", keys[25])
        self.assertEqual("Author262024a", keys[26])
        self.assertEqual("Author392024n", keys[39])
        self.assertEqual(len(pool), len(set(keys)))

    def test_changing_export_limit_preserves_existing_citation_keys(self):
        repeated = {"authors": ["John Smith"], "year": 2024}
        pool = [repeated, repeated]
        pool.extend({"authors": [f"Other{i}"], "year": 2024} for i in range(24))
        pool.append(repeated)
        complete_keys = self._render_keys(pool)
        for limit in (20, 27, 2, 30, 0, 26):
            with self.subTest(limit=limit):
                self.assertEqual(complete_keys[:limit], self._render_keys(pool, limit))

    def test_missing_author_year_still_has_distinct_keys(self):
        self.assertEqual(["anonnd", "anonnda", "anonndb"],
                         self._render_keys([{}, {}, {}]))

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
