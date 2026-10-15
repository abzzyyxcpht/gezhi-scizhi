"""专项智能体编制表、独立执行、流水线调度、并行 fan-out 与权限隔离。"""
from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from src.agent_runtime import AgentRuntime
from src.agents import literature, specialists
from src.agents.fixtures import demo_works
from src.agents.specialists import merge_pools, run as run_spec
from src.agents.specs import ROSTER, get_spec, list_agents
from src.bridge import handle
from src.connectors import crossref, openalex
from src.orchestrator.engine import PauseRequested
from scripts.gezhi import parse_args


class RosterTest(unittest.TestCase):
    def test_roster_covers_competition_slots(self):
        names = set(ROSTER)
        self.assertTrue(
            {"research", "pipeline", "literature", "extraction", "synthesis",
             "citation", "stats", "critic", "explore"} <= names)
        for spec in ROSTER.values():
            self.assertTrue(spec.title and spec.description and spec.prompt)
            self.assertIn(spec.mode, {"primary", "subagent"})

    def test_unknown_agent_raises(self):
        with self.assertRaises(ValueError):
            get_spec("not-an-agent")


class SpecialistRunTest(unittest.TestCase):
    def test_literature_offline_returns_scored_pool(self):
        ws: dict = {}
        result = run_spec("literature", "large language model agents",
                          workspace=ws, offline=True)
        self.assertTrue(result["ok"])
        self.assertGreater(result["pool_count"], 0)
        self.assertEqual(len(ws["pool"]), result["pool_count"])
        self.assertTrue(all("score" in w and w.get("reason") for w in ws["pool"]))

    def test_extraction_requires_pool(self):
        result = run_spec("extraction", "topic", workspace={}, offline=True)
        self.assertFalse(result["ok"])
        self.assertIn("文献池", result["error"])

    def test_synthesis_returns_gap_search_failure_diagnostics(self):
        gap = {"triggered": False, "status": "failed", "degraded": True,
               "warnings": ["补检检索失败"], "search_metadata": {
                   "source_status": {"openalex": "failed", "crossref": "failed"}}}
        workspace = {"records": [{"id": "saved", "title": "Existing evidence"}]}
        with mock.patch.object(specialists.extraction, "build_review_frame", return_value={
                "sections": [], "outline_md": "Existing evidence", "gap_refill": gap}):
            result = run_spec("synthesis", "research", workspace=workspace, offline=False)
        self.assertTrue(result["ok"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["gap_refill"], gap)
        self.assertEqual(workspace["gap_refill"], gap)
        self.assertEqual(result["warnings"], gap["warnings"])

    def test_chain_literature_extract_synthesis_citation_critic(self):
        ws: dict = {}
        topic = "large language model agents"
        self.assertTrue(run_spec("literature", topic, workspace=ws, offline=True)["ok"])
        self.assertTrue(run_spec("extraction", topic, workspace=ws, offline=True)["ok"])
        self.assertTrue(run_spec("synthesis", topic, workspace=ws, offline=True)["ok"])
        self.assertTrue(run_spec("citation", topic, workspace=ws, offline=True)["ok"])
        critic = run_spec("critic", topic, workspace=ws, offline=True)
        self.assertTrue(critic["ok"])
        self.assertGreater(len(ws["records"]), 0)
        self.assertGreater(len(ws["sections"]), 0)
        self.assertGreater(len(ws["citations"]), 0)
        self.assertIn("[J].", ws["citations"][0]["text"])
        self.assertEqual(critic["verdict"], "pass")
        self.assertEqual(critic["high"], 0)

    def test_critic_flags_empty_pool(self):
        critic = run_spec("critic", "审计", workspace={}, offline=True)
        self.assertTrue(critic["ok"])
        self.assertEqual(critic["verdict"], "continue")
        self.assertTrue(any(f["id"] == "A0" for f in critic["findings"]))

    def test_merge_pools_keeps_higher_score(self):
        a = [{"id": "W1", "title": "A", "score": 0.2}]
        b = [{"id": "W1", "title": "A", "score": 0.9},
             {"id": "W2", "title": "B", "score": 0.4}]
        merged = merge_pools(a, b)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["id"], "W1")
        self.assertEqual(merged[0]["score"], 0.9)

    def test_demo_works_are_isolated_copies(self):
        first = demo_works()
        first[0]["title"] = "mutated"
        second = demo_works()
        self.assertNotEqual(second[0]["title"], "mutated")


class OnlineLiteratureReliabilityTest(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.object(literature.app_settings, "load", return_value={"enable_crossref": True}),
            mock.patch.object(literature.config, "LIT_ENABLE_CROSSREF", True),
            mock.patch.object(literature.config, "LIT_MAX_ROUND", 1),
            mock.patch.object(literature, "_rank_candidates",
                              side_effect=lambda query, works: ([0.9] * len(works), {"tier": "lexical"})),
            mock.patch.object(literature, "_llm_refine", return_value=({}, set())),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_all_source_failure_returns_error_and_preserves_existing_workspace(self):
        existing = [{"id": "saved", "title": "Existing evidence"}]
        ws = {"pool": existing, "search_metadata": {"saved": True}}
        with mock.patch.object(openalex, "search", side_effect=RuntimeError("source offline")), \
             mock.patch.object(crossref, "search", side_effect=RuntimeError("source offline")), \
             mock.patch.object(specialists, "demo_works") as demo:
            result = run_spec("literature", "research", workspace=ws, offline=False)
        self.assertFalse(result["ok"])
        self.assertIn("所有已启用数据源", result["error"])
        self.assertEqual(result["pool_count"], 0)
        self.assertEqual(result["search_metadata"]["source_status"],
                         {"openalex": "failed", "crossref": "failed"})
        self.assertEqual(ws, {"pool": existing, "search_metadata": {"saved": True}})
        self.assertIs(ws["pool"], existing)
        self.assertEqual(result["local_docs"], {"loaded": 0, "ingested": 0, "skipped": []})
        demo.assert_not_called()

    def test_online_failure_still_ingests_explicit_text_without_relabeling_old_pool(self):
        existing = [{"id": "saved", "title": "Existing evidence", "source": "openalex"}]
        original_metadata = {"saved": True}
        ws = {"pool": existing, "search_metadata": original_metadata, "rounds": [{"round": 1}]}
        with tempfile.TemporaryDirectory() as directory:
            text = Path(directory) / "evidence.txt"
            text.write_text("We measured a controlled experiment with real supplied material.", encoding="utf-8")
            with mock.patch.object(openalex, "search", side_effect=RuntimeError("source offline")), \
                 mock.patch.object(crossref, "search", side_effect=RuntimeError("source offline")), \
                 mock.patch.object(specialists, "demo_works") as demo:
                result = run_spec("literature", "research", workspace=ws, offline=False,
                                  extra={"local_docs": [str(text)]})
        self.assertTrue(result["ok"])
        self.assertTrue(result["partial"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["search_status"], "failed")
        self.assertEqual(result["retrieved_count"], 0)
        self.assertEqual(result["pool_count"], 1)
        self.assertEqual(result["total_pool_count"], 2)
        self.assertEqual(result["retained_pool_count"], 1)
        self.assertEqual(result["local_docs"]["ingested"], 1)
        self.assertIs(ws["pool"][0], existing[0])
        self.assertIs(ws["search_metadata"], original_metadata)
        self.assertEqual(ws["rounds"], [{"round": 1}])
        self.assertEqual(ws["pool"][1]["source_type"], "local_text")
        self.assertTrue(ws["pool"][1]["full_text"])
        self.assertFalse(any(work.get("demo") for work in ws["pool"]))
        self.assertNotEqual(result["source_type"], "retrieved_metadata")
        self.assertIn("在线", result["notice"])
        demo.assert_not_called()

    def test_online_failure_with_corrupt_pdf_reports_skip_and_keeps_old_workspace(self):
        existing = [{"id": "saved", "title": "Existing evidence"}]
        ws = {"pool": existing}
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "broken.pdf"
            pdf.write_bytes(b"not a PDF document")
            with mock.patch.object(openalex, "search", side_effect=RuntimeError("source offline")), \
                 mock.patch.object(crossref, "search", side_effect=RuntimeError("source offline")), \
                 mock.patch.object(specialists, "demo_works") as demo:
                result = run_spec("literature", "research", workspace=ws, offline=False,
                                  extra={"local_docs": [str(pdf)]})
        self.assertFalse(result["ok"])
        self.assertFalse(result["partial"])
        self.assertEqual(result["pool_count"], 0)
        self.assertEqual(result["local_docs"]["ingested"], 0)
        self.assertEqual(len(result["local_docs"]["skipped"]), 1)
        self.assertIn("提取失败", result["local_docs"]["skipped"][0]["reason"])
        self.assertEqual(ws, {"pool": existing})
        self.assertIs(ws["pool"], existing)
        demo.assert_not_called()

    def test_online_failure_with_already_ingested_text_does_not_count_old_material_as_new(self):
        with tempfile.TemporaryDirectory() as directory:
            text = Path(directory) / "evidence.txt"
            text.write_text("Supplied experiment record.", encoding="utf-8")
            entries, _ = specialists.local_docs.load_entries([str(text)])
            ws = {"pool": entries}
            with mock.patch.object(openalex, "search", side_effect=RuntimeError("source offline")), \
                 mock.patch.object(crossref, "search", side_effect=RuntimeError("source offline")):
                result = run_spec("literature", "research", workspace=ws, offline=False,
                                  extra={"local_docs": [str(text)]})
        self.assertTrue(result["partial"])
        self.assertEqual(result["pool_count"], 0)
        self.assertEqual(result["retrieved_count"], 0)
        self.assertEqual(result["local_docs"]["loaded"], 1)
        self.assertEqual(result["local_docs"]["ingested"], 0)
        self.assertEqual(result["total_pool_count"], 1)
        self.assertEqual(len(ws["pool"]), 1)

    def test_partial_search_surfaces_diagnostics_in_result_and_workspace(self):
        ws = {}
        work = {"id": "https://doi.org/10.9999/kept", "doi": "10.9999/kept", "source": "crossref"}
        with mock.patch.object(openalex, "search", side_effect=RuntimeError("source offline")), \
             mock.patch.object(crossref, "search", return_value=[work]), \
             mock.patch.object(specialists, "demo_works") as demo:
            result = run_spec("literature", "research", workspace=ws, offline=False)
        self.assertTrue(result["ok"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["search_metadata"]["source_status"],
                         {"openalex": "failed", "crossref": "success"})
        self.assertEqual(result["search_metadata"], ws["search_metadata"])
        self.assertEqual(result["warnings"], ws["search_metadata"]["warnings"])
        self.assertEqual(result["source_type"], "retrieved_metadata")
        self.assertFalse(any(work.get("demo") for work in ws["pool"]))
        demo.assert_not_called()

    def test_successful_empty_online_search_does_not_create_demo_pool(self):
        ws = {}
        with mock.patch.object(openalex, "search", return_value=[]), \
             mock.patch.object(crossref, "search", return_value=[]), \
             mock.patch.object(specialists, "demo_works") as demo:
            result = run_spec("literature", "research", workspace=ws, offline=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["degraded"])
        self.assertEqual(ws["pool"], [])
        self.assertEqual(result["search_metadata"]["source_status"],
                         {"openalex": "success", "crossref": "success"})
        demo.assert_not_called()

    def test_control_exceptions_are_not_reported_as_search_failure(self):
        for error in (PermissionError("denied"), CancelledError(), PauseRequested()):
            with self.subTest(exception=type(error).__name__), \
                 mock.patch.object(literature, "literature_recursive_filter", side_effect=error), \
                 mock.patch.object(specialists, "demo_works") as demo:
                with self.assertRaises(type(error)):
                    run_spec("literature", "research", workspace={}, offline=False)
                demo.assert_not_called()

    def test_parallel_searches_keep_diagnostics_scoped_to_each_request(self):
        workspaces = {query: {} for query in ("partial", "healthy")}

        def oa(query, **kwargs):
            if query == "partial":
                raise RuntimeError("source offline")
            return [{"id": "https://openalex.org/W1", "source": "openalex"}]

        def cr(query, **kwargs):
            return [{"id": "crossref:" + query, "source": "crossref"}]

        with mock.patch.object(openalex, "search", side_effect=oa), \
             mock.patch.object(crossref, "search", side_effect=cr):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = dict(zip(workspaces, executor.map(
                    lambda query: run_spec("literature", query, workspace=workspaces[query], offline=False),
                    workspaces)))
        self.assertTrue(results["partial"]["degraded"])
        self.assertFalse(results["healthy"]["degraded"])
        self.assertEqual(results["healthy"]["warnings"], [])
        self.assertEqual(workspaces["partial"]["search_metadata"]["source_status"]["openalex"], "failed")
        self.assertEqual(workspaces["healthy"]["search_metadata"]["source_status"]["openalex"], "success")


class RuntimeSpecialistTest(unittest.TestCase):
    def test_explore_runs_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(session_root=Path(d))
            result = runtime.start("查看编制表", mode="agent", agent="explore")
            self.assertEqual(result["status"], "done")
            self.assertEqual(result["agent"], "explore")

    def test_review_mode_is_read_only_critic(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(session_root=Path(d))
            result = runtime.start("检查产物风险", mode="review")
            self.assertEqual(result["status"], "done")
            names = [e["payload"].get("name") for e in result["events"]
                     if e["type"] == "tool_call"]
            self.assertEqual(names, ["critic.run"])

    def test_literature_agent_cannot_call_research_export(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(session_root=Path(d))
            runtime._session_agent["sess"] = "literature"
            self.assertEqual(runtime._agent_gate("sess", "research.export", "write"),
                             "deny")
            self.assertEqual(runtime._agent_gate("sess", "literature.run", "network"),
                             "ask")

    def test_pipeline_completes_after_network_approval(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(session_root=Path(d))
            result = runtime.start("调研大模型智能体", mode="agent", agent="pipeline")
            self.assertEqual(result["status"], "waiting_approval")
            self.assertEqual(result["pending"]["tool"], "literature.run")
            result = runtime.approve(result["session_id"],
                                     result["pending"]["id"], "allow")
            self.assertEqual(result["status"], "done")
            snap = result["workspace"]
            self.assertGreater(snap["pool_count"], 0)
            self.assertGreater(snap["record_count"], 0)
            self.assertGreater(snap["citation_count"], 0)
            self.assertTrue(snap["has_digest"])
            self.assertTrue(snap["has_outline"])

    def test_spawn_child_is_visible_to_parent(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(
                session_root=Path(d),
                permissions={"subagent.spawn": "allow"})
            plan = runtime.plan("派发文献猎手", "plan", "research")
            # 直接执行 spawn 工具
            sid = "parentdemo01"
            runtime._session_agent[sid] = "research"
            runtime._workspaces[sid] = {"topic": "large language model agents"}
            out = runtime._subagent_spawn({
                "__session_id": sid,
                "agent": "literature",
                "request": "large language model agents",
            })
            self.assertTrue(out["ok"])
            self.assertIn("child_session", out)
            self.assertEqual(runtime._children[sid], [out["child_session"]])
            self.assertGreater(len(runtime._workspaces[sid].get("pool") or []), 0)
            self.assertTrue(plan["steps"])

    def test_fanout_merges_parallel_literature(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(session_root=Path(d),
                                   permissions={"subagent.fanout": "allow"})
            sid = "fanoutdemo001"
            runtime._session_agent[sid] = "research"
            runtime._workspaces[sid] = {}
            out = runtime._subagent_fanout({
                "__session_id": sid,
                "agents": ["literature", "literature"],
                "requests": ["large language model agents",
                             "multi agent collaboration"],
            })
            self.assertTrue(out["ok"])
            self.assertGreaterEqual(out["pool_count"], 1)
            self.assertEqual(out["children"], 2)

    def test_plan_prefix_selects_specialist(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(session_root=Path(d))
            plan = runtime.plan("@citation 把文献池排版", "plan")
            self.assertEqual(plan["agent"], "citation")
            self.assertEqual(plan["steps"][0]["tool"], "citation.run")


class SpecialistCliBridgeTest(unittest.TestCase):
    def test_cli_agents_command_and_agent_flag(self):
        args = parse_args(["agents"])
        self.assertEqual(args.command, "agents")
        args = parse_args(["agent", "调研大模型", "--agent", "literature",
                           "--mode", "plan"])
        self.assertEqual(args.agent, "literature")

    def test_bridge_agents_and_specialist_plan(self):
        roster = handle({"cmd": "agents"})
        self.assertTrue(roster["ok"])
        names = {row["name"] for row in roster["agents"]}
        self.assertIn("literature", names)
        plan = handle({"cmd": "agent_plan", "request": "调研大模型智能体",
                       "agent": "pipeline"})
        self.assertTrue(plan["ok"])
        tools = [s["tool"] for s in plan["plan"]["steps"]]
        self.assertEqual(tools[0], "literature.run")
        self.assertIn("critic.run", tools)

    def test_runtime_list_agents_matches_roster(self):
        self.assertEqual(len(AgentRuntime.list_agents()), len(list_agents()))
