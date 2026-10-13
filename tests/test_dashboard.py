# -*- coding: utf-8 -*-
"""科研看板与时间账单元测试（纯离线，合成任务数据）。"""
from __future__ import annotations

import unittest

from src import dashboard, textfmt
from src.orchestrator.engine import TaskContext


def _ctx() -> TaskContext:
    ctx = TaskContext("boardtest1", "research_pipeline", {"topic": "LLM 智能体"})
    ctx.status = "done"
    ctx.artifacts = {
        "lit_filter": {
            "pool": [
                {"id": "W1", "title": "Agents survey", "abstract": "agents agents rag",
                 "year": 2024, "venue": "J AI", "doi": "10.1/a", "pages": "1-9",
                 "score": 0.42},
                {"id": "W2", "title": "RAG systems", "abstract": "retrieval augmented",
                 "year": 2023, "venue": "J AI", "doi": "10.1/b", "score": 0.31},
                {"id": "W3", "title": "Tool use", "abstract": "agents tools",
                 "year": 2021, "venue": "KDD", "doi": "", "score": 0.09},
            ],
            "rounds": [
                {"round": 1, "candidates": 25, "new_relevant": 6, "ratio": 0.24},
                {"round": 2, "candidates": 68, "new_relevant": 2, "ratio": 0.03},
            ],
        }
    }
    return ctx


def _section(lines: list[str], title: str) -> list[str]:
    """取看板中某个小节的行（到空行为止），避免跨节比较列宽。"""
    out: list[str] = []
    for line in lines[lines.index(title) + 1:]:
        if not line.strip():
            break
        out.append(line)
    return out


class BoardTest(unittest.TestCase):
    def test_board_metrics(self):
        b = dashboard.board(_ctx())
        self.assertEqual(b["pool_size"], 3)
        self.assertEqual(b["year_hist"], {2021: 1, 2023: 1, 2024: 1})
        self.assertEqual(b["year_span"], (2021, 2024))
        self.assertEqual(b["median_year"], 2023)
        self.assertEqual(b["coverage"]["doi"], 67)     # 2/3
        self.assertEqual(b["coverage"]["abstract"], 100)
        self.assertEqual(sum(b["score_hist"]), 3)
        self.assertEqual(len(b["rounds"]), 2)
        self.assertTrue(0 <= b["health"] <= 100)
        self.assertEqual(b["saved_minutes"], 3 * 6 + 2 * 25)
        self.assertTrue(b["venue_top"][0][0] == "J AI")
        self.assertTrue(any(t == "agents" for t, _ in b["term_top"]))

    def test_empty_pool_board(self):
        ctx = TaskContext("empty1", "research_pipeline", {"topic": "t"})
        b = dashboard.board(ctx)
        self.assertEqual(b["pool_size"], 0)
        lines = dashboard.render_board(b)
        self.assertTrue(any("文献池为空" in line for line in lines))

    def test_render_board_has_sections(self):
        lines = dashboard.render_board(dashboard.board(_ctx()))
        text = "\n".join(lines)
        for seg in ("年份分布", "递归收敛曲线", "相关度分布", "元数据完整度",
                    "来源 Top", "研究热词", "时间账"):
            self.assertIn(seg, text)
        self.assertTrue(all(isinstance(line, str) for line in lines))

    def test_score_histogram_shows_all_bins(self):
        """空档也要落行：直方图缺行会让读者分不清「0 篇」与「没有这一档」。"""
        lines = dashboard.render_board(dashboard.board(_ctx()))
        i = lines.index("相关度分布")
        bins = [line for line in lines[i + 1:i + 12] if "–" in line]
        self.assertEqual(len(bins), 10)
        self.assertIn("0.1–0.2", "\n".join(bins))

    def test_columns_align_with_cjk(self):
        """中文标签/来源与拉丁混排时，条形图必须落在同一显示宽度（否则整块错位）。"""
        ctx = _ctx()
        ctx.artifacts["lit_filter"]["pool"][0]["venue"] = "生物医学工程学杂志"   # 全角来源
        lines = dashboard.render_board(dashboard.board(ctx))
        for title in ("元数据完整度", "来源 Top"):
            rows = _section(lines, title)
            starts = {textfmt.width(row.split("█")[0]) for row in rows if "█" in row}
            self.assertEqual(len(starts), 1, f"{title} 条形图未对齐：{starts}")

    def test_long_venue_and_term_clipped_with_mark(self):
        ctx = _ctx()
        ctx.artifacts["lit_filter"]["pool"][0]["venue"] = "Journal of the Association for Information Science"
        lines = dashboard.render_board(dashboard.board(ctx))
        i = lines.index("来源 Top")
        venue_rows = [line for line in lines[i + 1:i + 6] if "█" in line]
        self.assertTrue(any("…" in line for line in venue_rows))


class LedgerTest(unittest.TestCase):
    def test_ledger_totals(self):
        tasks = [
            {"task_id": "a", "topic": "甲", "status": "done",
             "status_text": "已完成", "created_at": "2026-09-11 10:00"},
            {"task_id": "b", "topic": "乙", "status": "paused",
             "status_text": "已暂停", "created_at": "2026-09-10 09:00"},
        ]
        entry = dashboard.ledger(tasks, {"a": 15, "b": 4}, {"a": 3, "b": 1})
        self.assertEqual(entry["tasks"], 2)
        self.assertEqual(entry["done"], 1)
        self.assertEqual(entry["works"], 19)
        self.assertEqual(entry["rounds"], 4)
        self.assertEqual(entry["saved_minutes"], 19 * 6 + 4 * 25)
        lines = dashboard.render_ledger(entry)
        self.assertTrue(any("研究时间账" in x for x in lines))
        self.assertTrue(any("甲" in x for x in lines))

    def test_ledger_empty(self):
        lines = dashboard.render_ledger(dashboard.ledger([]))
        self.assertTrue(any("暂无任务档案" in x for x in lines))

    def test_ledger_rows_align_with_cjk_topic(self):
        """台账最近任务表：中文题名后紧跟的「篇」列要对齐（按显示宽度补白）。"""
        tasks = [
            {"task_id": "a", "topic": "大语言模型智能体在科研综述中的应用", "status": "done",
             "status_text": "已完成", "created_at": "2026-09-11 10:00"},
            {"task_id": "b", "topic": "RAG", "status": "done",
             "status_text": "已完成", "created_at": "2026-09-10 09:00"},
        ]
        lines = dashboard.render_ledger(dashboard.ledger(tasks, {"a": 15, "b": 4}))
        i = lines.index("最近任务")
        cols = {textfmt.width(row.split("篇 ·")[0]) for row in lines[i + 1:i + 3] if "篇 ·" in row}
        self.assertEqual(len(cols), 1, f"台账列未对齐：{cols}")

    def test_saved_minutes_format(self):
        self.assertEqual(dashboard._fmt_minutes(45), "45 分钟")
        self.assertTrue(dashboard._fmt_minutes(90).endswith("小时"))


if __name__ == "__main__":
    unittest.main()
