# -*- coding: utf-8 -*-
"""双源并行检索单元测试：多源去重合并、CrossRef 归一化、单源失败降级。"""
from __future__ import annotations

import unittest

from src.agents.literature import merge_sources
from src.connectors import crossref

_CR_ITEM = {
    "DOI": "10.9999/abc",
    "title": ["Retrieval Augmented Generation: A Survey"],
    "author": [{"given": "John M.", "family": "Smith"},
               {"name": "集体作者"}],
    "issued": {"date-parts": [[2024, 5]]},
    "container-title": ["Journal of AI"],
    "volume": "12", "issue": "3", "page": "1-20",
    "abstract": "<jats:p>Deep <jats:italic>learning</jats:italic> survey.</jats:p>",
    "is-referenced-by-count": 42,
    "URL": "https://doi.org/10.9999/abc",
    "type": "journal-article",
}


class MergeSourcesTest(unittest.TestCase):
    def test_dedup_by_doi_prefers_first_batch(self):
        a = [{"id": "A", "doi": "10.1/x", "title": "from openalex"}]
        b = [{"id": "crossref:1", "doi": "10.1/X", "title": "from crossref"}]
        c = [{"id": "B", "doi": "", "title": "no doi"}]
        merged = merge_sources(a, b, c)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["title"], "from openalex")  # 先到先得
        self.assertEqual(merged[1]["title"], "no doi")

    def test_empty_and_doi_less(self):
        self.assertEqual(merge_sources([], []), [])
        merged = merge_sources([{"id": "A", "doi": ""}, {"id": "B", "doi": ""}])
        self.assertEqual(len(merged), 2)


class CrossrefNormalizeTest(unittest.TestCase):
    def test_schema_matches_openalex_contract(self):
        w = crossref._normalize(_CR_ITEM)
        self.assertEqual(w["id"], "https://doi.org/10.9999/abc")
        self.assertEqual(w["title"], "Retrieval Augmented Generation: A Survey")
        self.assertEqual(w["authors"][0], "John M. Smith")
        self.assertEqual(w["authors"][1], "集体作者")
        self.assertEqual(w["year"], 2024)
        self.assertEqual(w["venue"], "Journal of AI")
        self.assertEqual(w["volume"], "12")
        self.assertEqual(w["issue"], "3")
        self.assertEqual(w["pages"], "1-20")
        self.assertEqual(w["doi"], "10.9999/abc")
        self.assertEqual(w["abstract"], "Deep learning survey.")  # JATS 标签已剥
        self.assertEqual(w["cited_by_count"], 42)
        self.assertEqual(w["source"], "crossref")
        self.assertEqual(w["referenced_works"], [])  # 引用图归 OpenAlex 单源


class DualSourceSearchTest(unittest.TestCase):
    def setUp(self):
        from src.agents import literature
        self.lit = literature

    def test_both_sources_merged(self):
        import src.agents.literature as lit
        orig_openalex = lit.openalex.search
        try:
            lit.openalex.search = lambda *a, **k: [{"id": "A", "doi": "10.1/a"}]
            crossref.search = lambda *a, **k: [{"id": "C1", "doi": "10.1/b"}]
            merged, counts = lit._dual_source_search("q", 10, None, None, "search")
            self.assertEqual(counts, {"openalex": 1, "crossref": 1})
            self.assertEqual(len(merged), 2)
        finally:
            lit.openalex.search = orig_openalex
            from src.connectors import crossref as cr
            del cr.search  # 恢复模块属性（由 patch 覆盖）

    def test_crossref_failure_degrades(self):
        import src.agents.literature as lit
        orig_openalex = lit.openalex.search

        def boom(*a, **k):
            raise RuntimeError("crossref down")
        try:
            lit.openalex.search = lambda *a, **k: [{"id": "A", "doi": ""}]
            from src.connectors import crossref as cr
            cr.search = boom
            merged, counts = lit._dual_source_search("q", 10, None, None, "search")
            self.assertEqual(counts["crossref"], 0)
            self.assertEqual(len(merged), 1)
        finally:
            lit.openalex.search = orig_openalex
            from src.connectors import crossref as cr
            del cr.search

    def test_disabled_crossref_skips(self):
        import src.agents.literature as lit
        from src import config
        orig_openalex = lit.openalex.search
        orig_flag = config.LIT_ENABLE_CROSSREF

        def fail(*a, **k):
            raise AssertionError("crossref should not be called")
        try:
            config.LIT_ENABLE_CROSSREF = False
            lit.openalex.search = lambda *a, **k: [{"id": "A", "doi": ""}]
            from src.connectors import crossref as cr
            cr.search = fail
            merged, counts = lit._dual_source_search("q", 10, None, None, "search")
            self.assertEqual(counts["crossref"], 0)
            self.assertEqual(len(merged), 1)
        finally:
            lit.openalex.search = orig_openalex
            config.LIT_ENABLE_CROSSREF = orig_flag
            from src.connectors import crossref as cr
            del cr.search


if __name__ == "__main__":
    unittest.main()
