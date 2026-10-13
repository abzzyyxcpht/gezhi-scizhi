"""专项智能体编制表、独立执行、流水线调度、并行 fan-out 与权限隔离。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agent_runtime import AgentRuntime
from src.agents.fixtures import demo_works
from src.agents.specialists import merge_pools, run as run_spec
from src.agents.specs import ROSTER, get_spec, list_agents
from src.bridge import handle
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
