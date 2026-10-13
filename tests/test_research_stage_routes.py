"""Stage-specific routes must work without a configured default model."""
from __future__ import annotations

import unittest
from unittest import mock

from src.agents import extraction, literature
from src.orchestrator.engine import TaskContext


WORK = {"id": "W1", "title": "Fixture", "abstract": "We propose a retrieval method."}
SECTIONS = [{"title": str(i), "points": "Pending review", "refs": ["Fixture"]} for i in range(3)]


class ResearchStageRoutesTest(unittest.TestCase):
    def context(self, offline=False):
        ctx = TaskContext("stage-fixture", "research_pipeline", {"topic": "Retrieval", "offline": offline})
        ctx.artifacts = {"lit_filter": {"pool": [WORK]}, "extract": {"records": [WORK]}}
        return ctx

    def test_extraction_only_route_upgrades_extraction(self):
        with mock.patch.object(extraction.llm, "available", side_effect=lambda tier=None: tier == "extraction") as available, \
             mock.patch.object(extraction.llm, "chat_json", return_value={"method": WORK["abstract"]}) as chat:
            result = extraction.extract_pool(self.context(), {})
        available.assert_called_once_with(tier="extraction")
        self.assertEqual(chat.call_args.kwargs["tier"], "extraction")
        self.assertEqual(result["records"][0]["extraction_method"], "llm")

    def test_extraction_without_its_route_uses_rules(self):
        with mock.patch.object(extraction.llm, "available", side_effect=lambda tier=None: tier == "synthesis") as available, \
             mock.patch.object(extraction.llm, "chat_json") as chat:
            result = extraction.extract_pool(self.context(), {})
        available.assert_called_once_with(tier="extraction")
        chat.assert_not_called()
        self.assertEqual(result["records"][0]["extraction_method"], "rule")

    def test_relevance_review_uses_review_availability_and_route(self):
        with mock.patch.object(literature.llm, "available", side_effect=lambda tier=None: tier == "review") as available, \
             mock.patch.object(literature.llm, "chat_json", return_value={
                 "reviews": [{"idx": 0, "keep": False, "reason": "off topic"}]}) as chat:
            reasons, veto = literature._llm_refine("topic", [WORK], [0.9])
        available.assert_called_once_with(tier="review")
        self.assertEqual(chat.call_args.kwargs["tier"], "review")
        self.assertEqual(veto, {0})
        self.assertEqual(reasons[0], "off topic")

    def test_synthesis_route_works_with_rule_plan_when_planning_unconfigured(self):
        with mock.patch.object(extraction.llm, "available", side_effect=lambda tier=None: tier == "synthesis") as available, \
             mock.patch.object(extraction.llm, "chat_json", return_value={"sections": SECTIONS}) as chat:
            result = extraction.build_review_frame(self.context(), {})
        self.assertEqual(available.call_args_list, [mock.call(tier="synthesis"), mock.call(tier="planning")])
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(chat.call_args.kwargs["tier"], "synthesis")
        self.assertEqual(result["sections"], SECTIONS)

    def test_plan_and_synthesis_use_separate_routes_and_pass_plan(self):
        with mock.patch.object(extraction.llm, "available", return_value=True) as available, \
             mock.patch.object(extraction.llm, "chat_json", side_effect=[
                 {"plan": ["Use controlled baseline comparisons"]}, {"sections": SECTIONS}]) as chat:
            extraction.build_review_frame(self.context(), {})
        self.assertEqual(available.call_args_list, [mock.call(tier="synthesis"), mock.call(tier="planning")])
        self.assertEqual([call.kwargs["tier"] for call in chat.call_args_list], ["planning", "synthesis"])
        self.assertIn("Use controlled baseline comparisons", chat.call_args_list[1].args[0][-1]["content"])

    def test_failed_planning_still_allows_configured_synthesis(self):
        with mock.patch.object(extraction.llm, "available", return_value=True), \
             mock.patch.object(extraction.llm, "chat_json", side_effect=[RuntimeError("fixture"),
                                                                         {"sections": SECTIONS}]) as chat:
            result = extraction.build_review_frame(self.context(), {})
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(chat.call_args.kwargs["tier"], "synthesis")
        self.assertEqual(result["sections"], SECTIONS)

    def test_offline_bypasses_every_model_route(self):
        with mock.patch.object(extraction.llm, "available", return_value=True) as available, \
             mock.patch.object(extraction.llm, "chat_json") as chat:
            extraction.extract_pool(self.context(offline=True), {})
            extraction.build_review_frame(self.context(offline=True), {})
        available.assert_not_called()
        chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
