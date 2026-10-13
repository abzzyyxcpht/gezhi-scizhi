# -*- coding: utf-8 -*-
"""排序层单元测试：BM25 打分、饱和归一、余弦融合。"""
from __future__ import annotations

import unittest

from src.ranking import bm25_normalized, bm25_scores, fuse, tokenize


class TokenizeTest(unittest.TestCase):
    def test_mixed_language(self):
        toks = tokenize("Large Language 大模型 Agents")
        self.assertIn("large", toks)
        self.assertIn("language", toks)
        self.assertIn("agents", toks)
        self.assertIn("大", toks)
        self.assertIn("模", toks)

    def test_empty(self):
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize(None if False else ""), [])


class Bm25Test(unittest.TestCase):
    def test_relevant_doc_scores_higher(self):
        query = "large language model agents"
        docs = ["large language model agents survey of tool use",
                "graph neural networks for molecule generation",
                ""]
        s = bm25_scores(query, docs)
        self.assertGreater(s[0], s[1])
        self.assertEqual(s[2], 0.0)

    def test_normalized_in_unit_range(self):
        query = "retrieval augmented generation"
        docs = ["retrieval augmented generation improves factuality",
                "retrieval retrieval retrieval augmented generation generation",
                "unrelated content entirely"]
        s = bm25_normalized(query, docs)
        for v in s:
            self.assertGreaterEqual(v, 0.0)
            self.assertLess(v, 1.0)

    def test_empty_inputs(self):
        self.assertEqual(bm25_scores("q", []), [])
        self.assertEqual(bm25_scores("", ["a", "b"]), [0.0, 0.0])


class FuseTest(unittest.TestCase):
    def test_weighted_fusion_bounded(self):
        fused = fuse([0.5, 0.2], [0.8, 0.0])
        self.assertAlmostEqual(fused[0], 0.6 * 0.5 + 0.4 * 0.8, places=6)
        self.assertAlmostEqual(fused[1], 0.6 * 0.2, places=6)

    def test_fusion_preserves_length(self):
        self.assertEqual(len(fuse([0.1] * 3, [0.9] * 3)), 3)


if __name__ == "__main__":
    unittest.main()
