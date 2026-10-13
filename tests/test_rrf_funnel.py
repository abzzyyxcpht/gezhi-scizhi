# -*- coding: utf-8 -*-
"""RRF 融合数学正确性与文献漏斗集成测试（含离线基线逐比特回归）。"""
from __future__ import annotations

import unittest
from unittest import mock

from src import ranking
from src.agents import literature
from src.agents.fixtures import demo_works


class RrfFuseTest(unittest.TestCase):
    def test_hand_computed_example(self):
        # k=1：doc0 一路第 1 一路第 2，doc1 反之，RRF 对称 → 两者均 1/2+1/3=5/6
        fused = ranking.rrf_fuse([0, 1], [1, 0], k=1)
        self.assertAlmostEqual(fused[0], 5.0 / 6.0, places=9)
        self.assertAlmostEqual(fused[1], 5.0 / 6.0, places=9)

    def test_default_k_alignment_and_bounds(self):
        fused = ranking.rrf_fuse([0, 1, 2], [2, 1, 0])  # k=60
        self.assertEqual(len(fused), 3)
        self.assertAlmostEqual(fused[0], (1 / 61 + 1 / 63) / (2 / 61), places=9)
        self.assertAlmostEqual(fused[1], (1 / 62 + 1 / 62) / (2 / 61), places=9)
        self.assertAlmostEqual(fused[2], (1 / 63 + 1 / 61) / (2 / 61), places=9)
        for value in fused:
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)
        self.assertAlmostEqual(fused[0], fused[2], places=9)  # 对称序列表

    def test_unanimous_top_scores_one(self):
        fused = ranking.rrf_fuse([0, 1], [0, 1])
        self.assertAlmostEqual(fused[0], 1.0, places=9)

    def test_empty_orders(self):
        self.assertEqual(ranking.rrf_fuse(), [])
        self.assertEqual(ranking.rrf_fuse([], []), [])

    def test_single_order_normalization(self):
        fused = ranking.rrf_fuse([2, 0, 1])
        self.assertAlmostEqual(fused[2], 1.0, places=9)            # 独占第 1 → 归一 1.0
        self.assertAlmostEqual(fused[0], (1 / 62) / (1 / 61), places=9)  # 第 2 名


class FakeEmbeddingProvider:
    """可控向量桩：按显式映射返回向量，未知文本给零向量（零向量安全路径）。"""

    model = "fake-emb"

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self.mapping = mapping
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        self.calls += 1
        return [list(self.mapping.get(t, [0.0, 0.0])) for t in texts]


def _fake_fixture() -> tuple[str, list[dict], dict[str, list[float]]]:
    query = "alpha beta"
    works = [{"id": "w1", "title": "alpha beta", "abstract": ""},
             {"id": "w2", "title": "gamma", "abstract": ""}]
    # 与 lexical 同文本：title + abstract（注意尾部空格）
    mapping = {
        query: [1.0, 0.0],
        "alpha beta ": [1.0, 0.0],
        "gamma ": [0.0, 1.0],
    }
    return query, works, mapping


class RankCandidatesIntegrationTest(unittest.TestCase):
    def test_no_provider_matches_lexical_baseline_bit_exact(self):
        query, works, _ = _fake_fixture()
        with mock.patch("src.embedding.get_provider", return_value=None):
            scores, info = literature._rank_candidates(query, works)
        self.assertEqual(scores, literature._tfidf_score(query, works))
        self.assertEqual(info["tier"], "lexical")

    def test_provider_failure_silently_degrades(self):
        query, works, _ = _fake_fixture()
        broken = mock.Mock()
        broken.embed.return_value = None
        broken.model = "broken"
        with mock.patch("src.embedding.get_provider", return_value=broken):
            scores, info = literature._rank_candidates(query, works)
        self.assertEqual(scores, literature._tfidf_score(query, works))
        self.assertEqual(info["tier"], "lexical")

    def test_embedding_fusion_math_and_tier(self):
        query, works, mapping = _fake_fixture()
        fake = FakeEmbeddingProvider(mapping)
        with mock.patch("src.embedding.get_provider", return_value=fake):
            scores, info = literature._rank_candidates(query, works)
        self.assertEqual(info["tier"], "lexical+embedding")
        self.assertEqual(info["embedding_model"], "fake-emb")
        lexical = literature._tfidf_score(query, works)
        cos = [1.0, 0.0]  # query [1,0] 与 [1,0] 余弦 1，与 [0,1] 余弦 0
        expected = [literature._HYBRID_W_LEXICAL * lex + literature._HYBRID_W_EMB * c
                    for lex, c in zip(lexical, cos)]
        for got, want in zip(scores, expected):
            self.assertAlmostEqual(got, want, places=9)
        self.assertNotEqual(scores, lexical)  # 分数确实被向量级改写

    def test_hybrid_weights_are_injectable(self):
        query, works, mapping = _fake_fixture()
        fake = FakeEmbeddingProvider(mapping)
        with mock.patch("src.embedding.get_provider", return_value=fake), \
                mock.patch.object(literature, "_HYBRID_W_LEXICAL", 1.0), \
                mock.patch.object(literature, "_HYBRID_W_EMB", 0.0):
            scores, info = literature._rank_candidates(query, works)
        self.assertEqual(info["tier"], "lexical+embedding")  # tier 不随权重变化
        self.assertEqual(scores, literature._tfidf_score(query, works))

    def test_offtopic_doc_filtered_by_threshold_with_embedding(self):
        """6 篇 fixtures + 1 篇明显跑题：向量级启用后跑题篇应被阈值滤掉。"""
        query = "large language model agents"
        works = demo_works(query)
        works.append({"id": "w-off", "title": "Ancient pottery techniques in the Mediterranean",
                      "abstract": "A survey of ceramic glazing methods in antiquity."})

        class KeywordFake:
            """按关键词计数构造向量：query 与 demo 文献高相似，跑题篇近零。"""
            model = "keyword-fake"

            def embed(self, texts: list[str]) -> list[list[float]] | None:
                out = []
                for t in texts:
                    low = t.lower()
                    out.append([float(sum(low.count(w) for w in ("large", "language", "model", "agents"))), 1.0])
                return out

        with mock.patch("src.embedding.get_provider", return_value=KeywordFake()), \
                mock.patch.object(literature, '_llm_refine', return_value=({}, set())):
            result = literature.score_pool(query, works, 0.18, offline=False)
        self.assertEqual(result["funnel"]["tier"], "lexical+embedding")
        self.assertEqual(result["funnel"]["recalled"], 7)
        off = next((w for w in result["pool"] if w["id"] == "w-off"), None)
        self.assertIsNone(off, "跑题篇不应进入文献池")
        dropped = next(w for w in result["dropped"] if w["id"] == "w-off")
        self.assertLess(dropped["score"], result["threshold"])
        self.assertEqual(len(result["pool"]), 6)  # 6 篇 demo 全部保留

    def test_score_pool_funnel_fields_both_modes(self):
        query, works, mapping = _fake_fixture()
        fake = FakeEmbeddingProvider(mapping)
        with mock.patch("src.embedding.get_provider", return_value=None):
            base = literature.score_pool(query, works, 0.18, offline=True)
        self.assertEqual(base["funnel"]["tier"], "lexical")
        self.assertEqual(base["funnel"]["recalled"], len(works))
        with mock.patch("src.embedding.get_provider", return_value=fake), \
                mock.patch.object(literature, '_llm_refine', return_value=({}, set())):
            fused = literature.score_pool(query, works, 0.18, offline=False)
        self.assertEqual(fused["funnel"]["tier"], "lexical+embedding")
        self.assertIn("funnel", fused["rounds"][0])
        # 无 provider 时 score_pool 分数与基线逐比特一致
        base_scores = [w["score"] for w in base["pool"]]
        with mock.patch("src.embedding.get_provider", return_value=None):
            again = literature.score_pool(query, works, 0.18, offline=True)
        self.assertEqual([w["score"] for w in again["pool"]], base_scores)


class OfflineRecursiveFlowTest(unittest.TestCase):
    """离线 fixtures 递归主流程：无 provider 时路径与漏斗口径保持不变。"""

    def _run_offline(self):
        from src.orchestrator.engine import TaskContext
        topic = "large language model agents"
        ctx = TaskContext("eval-test-1", "literature_recursive_filter",
                          {"topic": topic, "offline": True})
        return topic, ctx, literature.literature_recursive_filter(ctx, {"threshold": 0.18})

    def test_offline_flow_unchanged_without_provider(self):
        with mock.patch("src.embedding.get_provider", return_value=None):
            _topic, ctx, result = self._run_offline()
        self.assertGreater(len(result["pool"]), 0)
        self.assertEqual(result["funnel"]["tier"], "lexical")
        self.assertEqual(result["funnel"]["recalled"],
                         sum(r["candidates"] for r in result["rounds"]))
        self.assertEqual(result["funnel"]["vetoed"], 0)
        for rnd in result["rounds"]:
            self.assertIn("funnel", rnd)
        messages = [e.get("message", "") for e in ctx.events]
        self.assertFalse(any("语义向量级评分已启用" in m for m in messages))

    def test_offline_flow_does_not_activate_even_a_configured_provider(self):
        with mock.patch("src.embedding.get_provider", side_effect=AssertionError('offline embedding')) as provider:
            _topic, ctx, result = self._run_offline()
        provider.assert_not_called()
        self.assertEqual(result["funnel"]["tier"], "lexical")
        messages = [e.get("message", "") for e in ctx.events]
        self.assertFalse(any("语义向量级评分已启用" in message for message in messages))


class DemoWorksSanityTest(unittest.TestCase):
    def test_demo_works_scored_offline(self):
        works = demo_works("retrieval augmented generation")
        result = literature.score_pool("retrieval augmented generation", works, 0.18, offline=True)
        self.assertEqual(len(result["pool"]) + len(result["dropped"]), len(works))
        self.assertEqual(result["funnel"]["recalled"], len(works))


if __name__ == "__main__":
    unittest.main()
