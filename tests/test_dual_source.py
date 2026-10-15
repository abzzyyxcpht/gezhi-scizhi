# -*- coding: utf-8 -*-
"""双源并行检索单元测试：多源去重合并、CrossRef 归一化、单源失败降级。"""
from __future__ import annotations

import unittest
from concurrent.futures import CancelledError
from unittest import mock

import httpx

from src.agents import literature
from src.agents.literature import merge_sources
from src.connectors import crossref, openalex
from src.orchestrator.engine import PauseRequested, TaskContext

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

    def test_doi_urls_prefixes_and_case_share_one_identity(self):
        first = {"id": "W1", "doi": "10.9999/ABC"}
        variants = [" https://doi.org/10.9999/abc ",
                    "HTTP://DX.DOI.ORG/10.9999/ABC", "doi: 10.9999/Abc"]
        merged = merge_sources([first], [
            {"id": f"C{i}", "doi": doi} for i, doi in enumerate(variants)])
        self.assertEqual(merged, [first])
        self.assertEqual(first["doi"], "10.9999/ABC")


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

    def test_missing_doi_keeps_url_identity(self):
        item = {**_CR_ITEM, "DOI": ""}
        work = crossref._normalize(item)
        self.assertEqual(work["id"], "crossref:" + item["URL"])
        self.assertEqual(work["doi"], "")

    def test_missing_doi_and_url_uses_stable_distinct_identity(self):
        item = {key: value for key, value in _CR_ITEM.items() if key not in {"DOI", "URL"}}
        first = crossref._normalize(item)["id"]
        self.assertEqual(first, crossref._normalize(dict(reversed(list(item.items()))))["id"])
        self.assertNotEqual(first, crossref._normalize({**item, "title": ["Another paper"]})["id"])


class ConnectorFailureTest(unittest.TestCase):
    def test_permissions_and_cancellation_do_not_retry_or_become_network_errors(self):
        for connector in (openalex, crossref):
            for error in (PermissionError("denied"), CancelledError(), PauseRequested()):
                with self.subTest(connector=connector.__name__, exception=type(error).__name__), \
                     mock.patch.object(connector, "_cache_get", return_value=None), \
                     mock.patch.object(connector._client, "get", side_effect=error) as get, \
                     mock.patch.object(connector.time, "sleep") as sleep, \
                     mock.patch.object(connector, "log_tool"):
                    with self.assertRaises(type(error)):
                        connector._get("/works", {}, "search")
                    get.assert_called_once()
                    sleep.assert_not_called()

    def test_rate_limit_exhaustion_preserves_http_status(self):
        for connector in (openalex, crossref):
            response = httpx.Response(429, request=httpx.Request("GET", connector.BASE + "/works"))
            with self.subTest(connector=connector.__name__), \
                 mock.patch.object(connector, "_cache_get", return_value=None), \
                 mock.patch.object(connector._client, "get", return_value=response), \
                 mock.patch.object(connector.time, "sleep"), \
                 mock.patch.object(connector, "log_tool"):
                with self.assertRaisesRegex(RuntimeError, "429"):
                    connector._get("/works", {}, "search")


class _SourceTestCase(unittest.TestCase):
    def setUp(self):
        self.lit = literature
        patcher = mock.patch.object(literature.app_settings, "load", return_value={"enable_crossref": True})
        patcher.start()
        self.addCleanup(patcher.stop)
        flag = mock.patch.object(literature.config, "LIT_ENABLE_CROSSREF", True)
        flag.start()
        self.addCleanup(flag.stop)


class DualSourceSearchTest(_SourceTestCase):
    def test_both_sources_merged(self):
        with mock.patch.object(openalex, "search", return_value=[{"id": "A", "doi": "10.1/a"}]), \
             mock.patch.object(crossref, "search", return_value=[{"id": "C1", "doi": "10.1/b"}]):
            merged, counts = literature._dual_source_search("q", 10, None, None, "search")
            self.assertEqual(counts, {"openalex": 1, "crossref": 1})
            self.assertEqual(len(merged), 2)

    def test_crossref_failure_degrades(self):
        with mock.patch.object(openalex, "search", return_value=[{"id": "A", "doi": ""}]), \
             mock.patch.object(crossref, "search", side_effect=RuntimeError("crossref down")):
            merged, counts = literature._dual_source_search("q", 10, None, None, "search")
            self.assertEqual(counts["crossref"], 0)
            self.assertEqual(len(merged), 1)

    def test_openalex_failure_keeps_crossref_results(self):
        work = {"id": "C1", "doi": "10.9999/abc", "source": "crossref"}
        with mock.patch.object(openalex, "search", side_effect=RuntimeError("openalex down")), \
             mock.patch.object(crossref, "search", return_value=[work]):
            merged, counts = literature._dual_source_search("q", 10, None, None, "search")
        self.assertEqual(merged, [work])
        self.assertEqual(counts, {"openalex": 0, "crossref": 1})

    def test_all_enabled_sources_failing_is_explicit(self):
        with mock.patch.object(openalex, "search", side_effect=RuntimeError("openalex down")), \
             mock.patch.object(crossref, "search", side_effect=RuntimeError("crossref down")):
            with self.assertRaisesRegex(RuntimeError, "所有已启用数据源.*失败"):
                literature._dual_source_search("q", 10, None, None, "search")

    def test_source_diagnostics_distinguish_empty_results_from_failure(self):
        diagnostics = {}
        with mock.patch.object(openalex, "search", return_value=[]), \
             mock.patch.object(crossref, "search", side_effect=RuntimeError("crossref down")):
            merged, counts = literature._dual_source_search(
                "q", 10, None, None, "search", diagnostics=diagnostics)
        self.assertEqual(merged, [])
        self.assertEqual(counts, {"openalex": 0, "crossref": 0})
        self.assertEqual(diagnostics["source_status"], {"openalex": "success", "crossref": "failed"})
        self.assertTrue(diagnostics["degraded"])
        self.assertIn("CrossRef", " ".join(diagnostics["warnings"]))

    def test_empty_success_is_not_degraded(self):
        diagnostics = {}
        with mock.patch.object(openalex, "search", return_value=[]), \
             mock.patch.object(crossref, "search", return_value=[]):
            merged, _ = literature._dual_source_search(
                "q", 10, None, None, "search", diagnostics=diagnostics)
        self.assertEqual(merged, [])
        self.assertFalse(diagnostics["degraded"])
        self.assertEqual(diagnostics["warnings"], [])

    def test_control_exceptions_are_never_downgraded(self):
        for source in (openalex, crossref):
            for error in (PermissionError("denied"), CancelledError(), PauseRequested()):
                with self.subTest(source=source.__name__, exception=type(error).__name__), \
                     mock.patch.object(openalex, "search", return_value=[]), \
                     mock.patch.object(crossref, "search", return_value=[]), \
                     mock.patch.object(source, "search", side_effect=error):
                    with self.assertRaises(type(error)):
                        literature._dual_source_search("q", 10, None, None, "search")

    def test_disabled_crossref_skips(self):
        with mock.patch.object(literature.config, "LIT_ENABLE_CROSSREF", False), \
             mock.patch.object(openalex, "search", return_value=[{"id": "A", "doi": ""}]), \
             mock.patch.object(crossref, "search") as search:
            merged, counts = literature._dual_source_search("q", 10, None, None, "search")
            self.assertEqual(counts["crossref"], 0)
            self.assertEqual(len(merged), 1)
            search.assert_not_called()

    def test_disabled_crossref_cannot_mask_sole_source_failure(self):
        with mock.patch.object(literature.config, "LIT_ENABLE_CROSSREF", False), \
             mock.patch.object(openalex, "search", side_effect=RuntimeError("openalex down")), \
             mock.patch.object(crossref, "search") as search:
            with self.assertRaisesRegex(RuntimeError, "所有已启用数据源.*失败"):
                literature._dual_source_search("q", 10, None, None, "search")
            search.assert_not_called()


class RecursiveSearchReliabilityTest(_SourceTestCase):
    def setUp(self):
        super().setUp()
        for patcher in (
            mock.patch.object(literature, "_rank_candidates",
                              side_effect=lambda query, works: ([0.9] * len(works), {"tier": "lexical"})),
            mock.patch.object(literature, "_llm_refine", return_value=({}, set())),
            mock.patch.object(literature.config, "LIT_MAX_ROUND", 3),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_crossref_only_results_survive_all_recursive_rounds(self):
        ctx = TaskContext("source-fallback", "literature_scan", {"topic": "retrieval"})
        work = {"id": "https://doi.org/10.9999/a", "doi": "10.9999/a", "source": "crossref"}
        with mock.patch.object(openalex, "search", side_effect=RuntimeError("openalex down")), \
             mock.patch.object(crossref, "search", return_value=[work]), \
             mock.patch.object(openalex, "fetch_many") as fetch, \
             mock.patch.object(openalex, "cited_by_batch") as cited:
            result = literature.literature_recursive_filter(ctx, {})
        fetch.assert_not_called()
        cited.assert_not_called()
        self.assertEqual([item["id"] for item in result["pool"]], [work["id"]])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["search_metadata"]["source_status"]["openalex"], "failed")
        self.assertTrue(any("OpenAlex" in event["message"] and "失败" in event["message"]
                            for event in ctx.events))

    def test_crossref_ids_are_excluded_from_openalex_snowball(self):
        ctx = TaskContext("source-seeds", "literature_scan", {"topic": "retrieval"})
        oa = {"id": "https://openalex.org/W1", "doi": "10.9999/a", "source": "openalex"}
        cr = {"id": "https://doi.org/10.9999/b", "doi": "10.9999/b", "source": "crossref"}
        with mock.patch.object(openalex, "search", return_value=[oa]), \
             mock.patch.object(crossref, "search", return_value=[cr]), \
             mock.patch.object(openalex, "fetch_many", return_value=[]), \
             mock.patch.object(openalex, "cited_by_batch", return_value=[]) as cited:
            result = literature.literature_recursive_filter(ctx, {})
        cited.assert_called_once_with([oa["id"]], per_page=10, node="expand")
        self.assertEqual(len(result["pool"]), 2)

    def test_expansion_failure_keeps_existing_pool_with_warning(self):
        ctx = TaskContext("expand-fallback", "literature_scan", {"topic": "retrieval"})
        work = {"id": "https://openalex.org/W1", "source": "openalex", "referenced_works": ["W2"]}
        with mock.patch.object(openalex, "search", return_value=[work]), \
             mock.patch.object(crossref, "search", return_value=[]), \
             mock.patch.object(openalex, "fetch_many", side_effect=RuntimeError("expand down")), \
             mock.patch.object(openalex, "cited_by_batch", return_value=[]):
            result = literature.literature_recursive_filter(ctx, {})
        self.assertEqual(len(result["pool"]), 1)
        self.assertTrue(result["degraded"])
        self.assertIn("引用扩展", result["notice"])
        self.assertEqual(result["search_metadata"]["warnings"], result["warnings"])
        self.assertTrue(result["search_metadata"]["degraded"])

    def test_expansion_deduplicates_against_crossref_and_within_round(self):
        ctx = TaskContext("expand-dedup", "literature_scan", {"topic": "retrieval"})
        oa = {"id": "https://openalex.org/W1", "doi": "10.9999/a", "source": "openalex"}
        cr = {"id": "https://doi.org/10.9999/b", "doi": "10.9999/B", "source": "crossref"}
        duplicate = {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.9999/b",
                     "source": "openalex"}
        new = {"id": "https://openalex.org/W3", "doi": "10.9999/c", "source": "openalex"}
        with mock.patch.object(literature.config, "LIT_MAX_ROUND", 2), \
             mock.patch.object(openalex, "search", return_value=[oa]), \
             mock.patch.object(crossref, "search", return_value=[cr]), \
             mock.patch.object(openalex, "fetch_many", return_value=[duplicate, new]), \
             mock.patch.object(openalex, "cited_by_batch", return_value=[new]):
            result = literature.literature_recursive_filter(ctx, {})
        self.assertEqual([work["id"] for work in result["pool"]], [oa["id"], cr["id"], new["id"]])
        self.assertEqual(result["rounds"][1]["new_relevant"], 1)
        self.assertEqual(result["rounds"][1]["candidates"], 1)

    def test_source_warning_survives_pause_and_resume(self):
        ctx = TaskContext("source-resume", "literature_scan", {"topic": "retrieval"})
        work = {"id": "https://openalex.org/W1", "source": "openalex"}

        def score_then_pause(query, works):
            ctx.request_pause()
            return [0.9] * len(works), {"tier": "lexical"}

        with mock.patch.object(openalex, "search", return_value=[work]), \
             mock.patch.object(crossref, "search", side_effect=RuntimeError("crossref down")), \
             mock.patch.object(literature, "_rank_candidates", side_effect=score_then_pause):
            with self.assertRaises(PauseRequested):
                literature.literature_recursive_filter(ctx, {})
        ctx.clear_pause()
        with mock.patch.object(openalex, "search") as search, \
             mock.patch.object(openalex, "fetch_many", return_value=[]), \
             mock.patch.object(openalex, "cited_by_batch", return_value=[]):
            result = literature.literature_recursive_filter(ctx, {})
        search.assert_not_called()
        self.assertTrue(result["degraded"])
        self.assertEqual(result["search_metadata"]["source_status"]["crossref"], "failed")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertNotIn("_lit_state", ctx.artifacts)

    def test_expansion_control_exceptions_propagate(self):
        work = {"id": "https://openalex.org/W1", "source": "openalex"}
        for error in (PermissionError("denied"), CancelledError(), PauseRequested()):
            ctx = TaskContext("expand-control", "literature_scan", {"topic": "retrieval"})
            with self.subTest(exception=type(error).__name__), \
                 mock.patch.object(openalex, "search", return_value=[dict(work)]), \
                 mock.patch.object(crossref, "search", return_value=[]), \
                 mock.patch.object(openalex, "fetch_many", side_effect=error):
                with self.assertRaises(type(error)):
                    literature.literature_recursive_filter(ctx, {})


if __name__ == "__main__":
    unittest.main()
