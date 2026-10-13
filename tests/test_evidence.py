# -*- coding: utf-8 -*-
"""证据图谱单元测试：锚定率 / 孤儿文献 / 缺证据章节 / 防幻觉砍引 / 渲染。"""
from __future__ import annotations

import unittest

from src import evidence, textfmt
from src.orchestrator.engine import TaskContext


def _ctx(pool, sections, records=None) -> TaskContext:
    ctx = TaskContext("graphtest01", "research_pipeline", {"topic": "图谱测试"})
    ctx.artifacts = {
        "lit_filter": {"pool": pool, "rounds": [{"round": 1}]},
        "extract": {"records": records if records is not None else
                    [{"title": w["title"]} for w in pool]},
        "review_frame": {"sections": sections},
    }
    return ctx


POOL = [
    {"id": "W1", "title": "Agents Survey", "score": 0.42},
    {"id": "W2", "title": "Tool Use in Agents", "score": 0.31},
    {"id": "W3", "title": "Memory Augmented Agents", "score": 0.27},
    {"id": "W4", "title": "Reliable Retrieval", "score": 0.12},
]


class GraphTest(unittest.TestCase):
    def test_anchor_rate_and_orphans(self):
        g = evidence.build_graph(_ctx(POOL, [
            {"title": "1 背景", "refs": ["Agents Survey", "Tool Use in Agents"]},
            {"title": "2 方法", "refs": ["Memory Augmented Agents"]},
        ]))
        self.assertEqual(g["pool"], 4)
        self.assertEqual(g["anchored"], 3)
        self.assertEqual(g["anchor_rate"], 75)
        self.assertEqual([w["title"] for w in g["orphans"]], ["Reliable Retrieval"])
        self.assertEqual(g["orphan_rate"], 25)

    def test_missing_section_detected(self):
        g = evidence.build_graph(_ctx(POOL, [
            {"title": "1 背景", "refs": ["Agents Survey"]},
            {"title": "2 空白方向", "refs": []},
        ]))
        self.assertEqual(g["missing_sections"], ["2 空白方向"])
        self.assertTrue(g["sections"][1]["missing"])

    def test_hallucinated_refs_counted_as_dropped(self):
        """章节声称 3 篇，其中 1 篇不在池里 → 计入 dropped（防幻觉校验的战果）。"""
        g = evidence.build_graph(_ctx(POOL, [
            {"title": "1 背景", "refs": ["Agents Survey", "Tool Use in Agents", "虚构文献 X"]},
        ]))
        self.assertEqual(g["dropped_refs"], 1)
        self.assertEqual(len(g["sections"][0]["refs"]), 2)

    def test_fuzzy_title_match_tolerates_punctuation(self):
        g = evidence.build_graph(_ctx(POOL, [
            {"title": "1 背景", "refs": ["Agents  Survey.", "Tool-Use in Agents"]},
        ]))
        self.assertEqual(g["anchored"], 2)

    def test_not_extracted_reported(self):
        g = evidence.build_graph(_ctx(POOL, [
            {"title": "1 背景", "refs": ["Agents Survey"]}],
            records=[{"title": "Agents Survey"}]))
        self.assertEqual(len(g["not_extracted"]), 3)

    def test_empty_pool_renders_hint(self):
        g = evidence.build_graph(_ctx([], []))
        self.assertEqual(g["pool"], 0)
        lines = evidence.render_graph(g)
        self.assertTrue(any("文献池为空" in line for line in lines))

    def test_render_has_key_sections(self):
        lines = evidence.render_graph(evidence.build_graph(_ctx(POOL, [
            {"title": "1 背景", "refs": ["Agents Survey"]},
            {"title": "2 空白", "refs": []},
        ])))
        text = "\n".join(lines)
        for seg in ("证据图谱", "锚定率", "缺证据", "孤儿文献"):
            self.assertIn(seg, text)

    def test_no_sections_still_renders_pool_side(self):
        lines = evidence.render_graph(evidence.build_graph(_ctx(POOL, [])))
        text = "\n".join(lines)
        self.assertIn("尚无综述框架", text)
        self.assertIn("孤儿文献", text)

    def test_long_titles_clipped_with_mark(self):
        """长题名裁切要带省略号：硬切到第 N 个字符看着像渲染故障。"""
        long_title = "Automated Literature Synthesis with Retrieval Augmented Agents"
        long_orphan = "Hierarchical Memory Architectures for Long-Horizon Research Assistants"
        pool = [{"id": "W1", "title": long_title, "score": 0.5},
                {"id": "W2", "title": long_orphan, "score": 0.4}]
        lines = evidence.render_graph(evidence.build_graph(_ctx(pool, [
            {"title": "1 背景", "refs": [long_title]}])))
        ref_rows = [line for line in lines if "├" in line or "└" in line]
        orphan_rows = [line for line in lines if line.strip().startswith("· [")]
        self.assertTrue(any("…" in line for line in ref_rows), lines)
        self.assertTrue(any("…" in line for line in orphan_rows), lines)
        for row in ref_rows + orphan_rows:
            self.assertLessEqual(textfmt.width(row), 72, row)   # 前缀 + clip(60)


if __name__ == "__main__":
    unittest.main()
