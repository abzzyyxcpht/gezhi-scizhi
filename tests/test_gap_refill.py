# -*- coding: utf-8 -*-
"""缺证据驱动补检：触发条件、入池/记录同步、框架重写与重放保护。

构造最小 TaskContext（动作不触盘，无需隔离 Checkpoint 目录），patch
双源检索、评分与规则分节，验证离线不触发、在线补检闭环、断点续跑不
二次触发、空结果/检索失败不报错等行为。
"""
from __future__ import annotations

import unittest
from concurrent.futures import CancelledError
from unittest import mock

from src.agents import extraction, literature
from src.connectors import crossref, openalex
from src.orchestrator.engine import PauseRequested, TaskContext


def _work(wid: str, title: str, score: float = 0.9) -> dict:
    return {"id": wid, "title": title, "authors": ["Fixture Author"],
            "year": 2024, "venue": "Fixture Journal", "doi": f"10.9999/{wid}",
            "score": score, "url": "", "source": "fixture",
            "abstract": "We propose a method. Results show improvement."}


def _record(work: dict) -> dict:
    return {**{k: work.get(k) for k in
               ("id", "title", "authors", "year", "venue", "doi", "score", "url", "source")},
            "method": work["abstract"], "innovation": "", "conclusion": "",
            "limitation": "", "abstract": work["abstract"], "demo": False,
            "source_type": "abstract_only", "material_scope": "abstract",
            "extraction_method": "rule", "provenance": {}, "flags": []}


def _ctx(offline: bool = False) -> TaskContext:
    w1, w2 = _work("W1", "Alpha retrieval"), _work("W2", "Beta ranking", 0.8)
    ctx = TaskContext("gaprefill01", "research_pipeline",
                      {"topic": "retrieval", "offline": offline})
    ctx.artifacts = {
        "lit_filter": {"pool": [w1, w2]},
        "extract": {"records": [_record(w1), _record(w2)], "digest_md": "# d"},
    }
    return ctx


def _fake_rule_sections(topic: str, records: list[dict]) -> list[dict]:
    """规则分节桩：第一节锚定首条记录，「空白方向」节在补检入池后才挂上新文献。"""
    first = records[0]["title"] if records else ""
    refill_refs = [r["title"] for r in records if r.get("id") == "W3"]
    return [
        {"title": "1 已有支撑", "points": "p", "refs": [first]},
        {"title": "2 空白方向", "points": "p", "refs": refill_refs},
    ]


def _fake_search(works: list[dict], metadata: dict | None = None):
    def _search(query: str, per_page: int, year_from, year_to, node: str, *, diagnostics=None):
        if diagnostics is not None:
            diagnostics.update(metadata or {
                "source_counts": {"openalex": len(works), "crossref": 0},
                "source_status": {"openalex": "success", "crossref": "disabled"},
                "degraded": False, "warnings": []})
        return list(works), {"openalex": len(works), "crossref": 0}
    return _search


def _fake_score(works: list[dict]):
    keep = {x["id"] for x in works}

    def _score(query: str, candidates: list[dict], tau=None, **kw):
        pool = [{**w, "score": 0.85, "reason": "fixture"}
                for w in candidates if w["id"] in keep]
        return {"pool": pool, "dropped": [], "summary": "fixture",
                "threshold": tau or 0.18}
    return _score


NEW_WORKS = [_work("W3", "Gamma evidence refill")]


class GapRefillTriggerTest(unittest.TestCase):
    def test_offline_does_not_trigger(self):
        ctx = _ctx(offline=True)
        with mock.patch.object(extraction, "_rule_sections",
                               side_effect=_fake_rule_sections), \
             mock.patch.object(literature, "_dual_source_search") as search:
            result = extraction.build_review_frame(ctx, {})
        search.assert_not_called()
        self.assertFalse(result["gap_refill"]["triggered"])
        self.assertIn("离线", result["gap_refill"]["reason"])
        # 文献池不被改写
        self.assertEqual(len(ctx.artifacts["lit_filter"]["pool"]), 2)

    def test_no_refill_param_disables(self):
        ctx = _ctx()
        with mock.patch.object(extraction, "_rule_sections",
                               side_effect=_fake_rule_sections), \
             mock.patch.object(literature, "_dual_source_search") as search:
            result = extraction.build_review_frame(ctx, {"no_refill": True})
        search.assert_not_called()
        self.assertFalse(result["gap_refill"]["triggered"])
        self.assertIn("no_refill", result["gap_refill"]["reason"])

    def test_no_missing_sections_returns_no_marker(self):
        ctx = _ctx()

        def all_anchored(topic: str, records: list[dict]) -> list[dict]:
            first = records[0]["title"] if records else ""
            return [{"title": "1 已有支撑", "points": "p", "refs": [first]},
                    {"title": "2 也有支撑", "points": "p", "refs": [first]}]

        with mock.patch.object(extraction, "_rule_sections",
                               side_effect=all_anchored), \
             mock.patch.object(literature, "_dual_source_search") as search:
            result = extraction.build_review_frame(ctx, {})
        search.assert_not_called()
        self.assertFalse(result["gap_refill"]["triggered"])
        self.assertIn("无缺证据章节", result["gap_refill"]["reason"])


class GapRefillRunTest(unittest.TestCase):
    def _run(self, ctx: TaskContext, works: list[dict] = NEW_WORKS) -> dict:
        with mock.patch.object(extraction, "_rule_sections",
                               side_effect=_fake_rule_sections), \
             mock.patch.object(literature, "_dual_source_search",
                               side_effect=_fake_search(works)), \
             mock.patch.object(literature, "score_pool",
                               side_effect=_fake_score(works)):
            return extraction.build_review_frame(ctx, {})

    def test_refill_adds_pool_records_and_rewrites_frame(self):
        ctx = _ctx()
        result = self._run(ctx)
        refill = result["gap_refill"]
        self.assertTrue(refill["triggered"])
        self.assertEqual(refill["sections"], ["2 空白方向"])
        self.assertEqual(refill["added"], 1)
        self.assertEqual(refill["anchor_rate_before"], 50)   # 1/2 锚定
        self.assertGreater(refill["anchor_rate_after"], refill["anchor_rate_before"])
        # 文献池增大并按 score 降序
        pool = ctx.artifacts["lit_filter"]["pool"]
        self.assertEqual(len(pool), 3)
        self.assertEqual({w["id"] for w in pool}, {"W1", "W2", "W3"})
        self.assertEqual(pool[0]["score"], 0.9)
        # 抽取记录同步新增，且带补检标注（下游证据图谱不计 not_extracted）
        records = ctx.artifacts["extract"]["records"]
        self.assertEqual(len(records), 3)
        new_rec = next(r for r in records if r["id"] == "W3")
        self.assertEqual(new_rec["reason"], "缺证据补检入池")
        self.assertEqual(new_rec["extraction_method"], "gap_refill")
        self.assertEqual(new_rec["method"], "")
        self.assertFalse(new_rec["demo"])
        # 框架被重写：新文献进入「空白方向」节的 refs
        sec_blank = next(s for s in result["sections"] if s["title"] == "2 空白方向")
        self.assertIn(NEW_WORKS[0]["title"], sec_blank["refs"])
        # 事件序列
        messages = [e["message"] for e in ctx.events if e["kind"] == "node"]
        self.assertTrue(any("框架缺证据章节" in m and "触发补充检索" in m
                            for m in messages))
        self.assertTrue(any("补检入池 1 篇" in m for m in messages))
        self.assertTrue(any("框架已按增补文献重写" in m for m in messages))

    def test_replay_does_not_refill_twice(self):
        ctx = _ctx()
        first = self._run(ctx)
        self.assertTrue(first["gap_refill"]["triggered"])
        # 模拟引擎：result 落为 review_frame 产物后节点被重放
        ctx.artifacts["review_frame"] = first
        with mock.patch.object(extraction, "_rule_sections",
                               side_effect=_fake_rule_sections), \
             mock.patch.object(literature, "_dual_source_search") as search:
            second = extraction.build_review_frame(ctx, {})
        search.assert_not_called()
        self.assertFalse(second["gap_refill"]["triggered"])
        self.assertIn("已补检过", second["gap_refill"]["reason"])

    def test_empty_refill_result_is_not_an_error(self):
        ctx = _ctx()
        result = self._run(ctx, works=[])
        self.assertFalse(result["gap_refill"]["triggered"])
        self.assertEqual(result["gap_refill"]["reason"], "补检无结果")
        self.assertEqual(result["gap_refill"]["status"], "empty")
        self.assertFalse(result["gap_refill"]["degraded"])
        self.assertEqual(len(ctx.artifacts["lit_filter"]["pool"]), 2)

    def test_search_failure_is_not_an_error(self):
        ctx = _ctx()
        with mock.patch.object(extraction, "_rule_sections",
                               side_effect=_fake_rule_sections), \
             mock.patch.object(literature, "_dual_source_search",
                               side_effect=RuntimeError("network down")):
            result = extraction.build_review_frame(ctx, {})
        self.assertFalse(result["gap_refill"]["triggered"])
        self.assertIn("补检检索失败", result["gap_refill"]["reason"])
        self.assertEqual(result["gap_refill"]["status"], "failed")
        self.assertTrue(result["gap_refill"]["degraded"])

    def test_partial_search_diagnostics_are_returned_and_emitted(self):
        ctx = _ctx()
        metadata = {"source_counts": {"openalex": 1, "crossref": 0},
                    "source_status": {"openalex": "success", "crossref": "failed"},
                    "degraded": True, "warnings": ["CrossRef 检索失败，本次结果未包含该源。"]}
        with mock.patch.object(extraction, "_rule_sections", side_effect=_fake_rule_sections), \
             mock.patch.object(literature, "_dual_source_search", side_effect=_fake_search(NEW_WORKS, metadata)), \
             mock.patch.object(literature, "score_pool", side_effect=_fake_score(NEW_WORKS)):
            result = extraction.build_review_frame(ctx, {})
        refill = result["gap_refill"]
        self.assertTrue(refill["triggered"])
        self.assertEqual(refill["status"], "completed")
        self.assertTrue(refill["degraded"])
        self.assertEqual(refill["search_metadata"], metadata)
        self.assertEqual(refill["warnings"], metadata["warnings"])
        self.assertTrue(any(metadata["warnings"][0] in event["message"] for event in ctx.events))

    def test_failed_refill_replay_does_not_search_again(self):
        ctx = _ctx()
        with mock.patch.object(extraction, "_rule_sections", side_effect=_fake_rule_sections), \
             mock.patch.object(literature, "_dual_source_search", side_effect=RuntimeError("source offline")):
            ctx.artifacts["review_frame"] = extraction.build_review_frame(ctx, {})
        with mock.patch.object(extraction, "_rule_sections", side_effect=_fake_rule_sections), \
             mock.patch.object(literature, "_dual_source_search") as search:
            result = extraction.build_review_frame(ctx, {})
        search.assert_not_called()
        self.assertEqual(result["gap_refill"]["status"], "failed")
        self.assertIn("补检检索失败", result["gap_refill"]["reason"])

    def test_search_control_exceptions_propagate(self):
        for error in (PermissionError("denied"), CancelledError(), PauseRequested()):
            ctx = _ctx()
            with self.subTest(exception=type(error).__name__), \
                 mock.patch.object(extraction, "_rule_sections", side_effect=_fake_rule_sections), \
                 mock.patch.object(literature, "_dual_source_search", side_effect=error):
                with self.assertRaises(type(error)):
                    extraction.build_review_frame(ctx, {})

    def test_all_source_failures_keep_metadata_without_changing_evidence(self):
        ctx = _ctx()
        old_pool = list(ctx.artifacts["lit_filter"]["pool"])
        old_records = list(ctx.artifacts["extract"]["records"])
        sections = _fake_rule_sections("retrieval", old_records)
        with mock.patch.object(literature.app_settings, "load", return_value={"enable_crossref": True}), \
             mock.patch.object(literature.config, "LIT_ENABLE_CROSSREF", True), \
             mock.patch.object(openalex, "search", side_effect=RuntimeError("source offline")), \
             mock.patch.object(crossref, "search", side_effect=RuntimeError("source offline")):
            refill, kept_sections = extraction._gap_refill(ctx, {}, "retrieval", sections, False)
        self.assertEqual(refill["status"], "failed")
        self.assertEqual(refill["search_metadata"]["source_status"],
                         {"openalex": "failed", "crossref": "failed"})
        self.assertEqual(refill["search_metadata"]["source_counts"], {"openalex": 0, "crossref": 0})
        self.assertEqual(ctx.artifacts["lit_filter"]["pool"], old_pool)
        self.assertEqual(ctx.artifacts["extract"]["records"], old_records)
        self.assertEqual(kept_sections, sections)

    def test_doi_variants_do_not_reenter_pool_during_refill(self):
        ctx = _ctx()
        duplicate = {**_work("W9", "Alpha retrieval"), "doi": "HTTP://DX.DOI.ORG/10.9999/W1"}
        result = self._run(ctx, works=[duplicate])
        self.assertFalse(result["gap_refill"]["triggered"])
        self.assertEqual(result["gap_refill"]["status"], "empty")
        self.assertEqual(len(ctx.artifacts["lit_filter"]["pool"]), 2)


if __name__ == "__main__":
    unittest.main()
