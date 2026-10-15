# -*- coding: utf-8 -*-
"""并行文献子任务真实化：在线分支真实检索 + 审计事件 + 失败隔离，离线回归。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.agent_runtime import AgentRuntime
from src.agents import literature


def _work(wid: str, title: str) -> dict:
    return {"id": wid, "title": title, "authors": ["Fixture Author"],
            "year": 2024, "venue": "Fixture Journal", "doi": f"10.9999/{wid}",
            "url": "", "source": "fixture",
            "abstract": "We propose a method. Results show improvement."}


CATALOG = {
    "query one": [_work("A1", "Alpha agents"), _work("A2", "Beta agents")],
    "query two": [_work("B1", "Gamma collaboration"), _work("A1", "Alpha agents")],
    "query three": [],
}


def _fake_search(query: str, per_page: int, year_from, year_to, node: str, **kwargs):
    if query == "query boom":
        raise ConnectionError("fixture network failure")
    works = CATALOG.get(query, [])
    return list(works), {"openalex": len(works), "crossref": 0}


def _fake_score(query: str, candidates: list[dict], tau=None, **kw):
    pool = [{**w, "score": 0.9 - i * 0.1, "reason": "fixture"}
            for i, w in enumerate(candidates)]
    return {"pool": pool, "dropped": [], "summary": "fixture",
            "threshold": tau or 0.18}


class FanoutLiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime = AgentRuntime(session_root=Path(self._tmp.name),
                                    offline=False,
                                    llm_enabled=False,
                                    permissions={"subagent.fanout": "allow"})
        self.sid = "fanoutlive01"
        self.runtime._session_agent[self.sid] = "research"
        self.runtime._workspaces[self.sid] = {}

    def _run(self, requests: list[str]) -> dict:
        with mock.patch.object(literature, "_dual_source_search",
                               side_effect=_fake_search), \
             mock.patch.object(literature, "score_pool",
                               side_effect=_fake_score):
            return self.runtime._subagent_fanout({
                "__session_id": self.sid,
                "agents": ["literature"] * len(requests),
                "requests": requests,
            })

    def test_live_branches_search_score_and_merge(self):
        out = self._run(["query one", "query two", "query three"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["mode"], "live")
        self.assertEqual(out["children"], 3)
        self.assertEqual(len(out["branches"]), 3)
        # 合并池按 id 去重：A1 在 query one/two 重复，只保留更高分一份
        self.assertEqual(out["pool_count"], 3)
        merged = self.runtime._workspaces[self.sid]["pool"]
        self.assertEqual({w["id"] for w in merged}, {"A1", "A2", "B1"})
        self.assertEqual(merged[0]["score"], 0.9)  # 高分在前，A1 去重后仍 0.9
        # 每路明细 + workspace["fanout"] 记录
        by_req = {b["request"]: b for b in out["branches"]}
        self.assertEqual(by_req["query one"]["pool_count"], 2)
        self.assertEqual(by_req["query one"]["source_counts"],
                         {"openalex": 2, "crossref": 0})
        self.assertTrue(by_req["query one"]["ok"])
        self.assertFalse(by_req["query three"]["ok"] is False)  # 空池不算失败
        fanout_ws = self.runtime._workspaces[self.sid]["fanout"]
        self.assertEqual(len(fanout_ws), 3)
        self.assertEqual(set(fanout_ws[0]),
                         {"request", "pool_count", "source_counts", "ok"})
        # 子会话审计事件落盘：user_message 带 parent、completed 收尾
        for branch in out["branches"]:
            child = branch["child_session"]
            events = self.runtime.sessions.read(child)
            kinds = [e["type"] for e in events]
            self.assertEqual(kinds, ["user_message", "completed"])
            self.assertEqual(events[0]["payload"]["parent"], self.sid)
            self.assertEqual(events[1]["payload"]["status"], "done")
        self.assertEqual(self.runtime._children[self.sid],
                         [b["child_session"] for b in out["branches"]])
        parent_events = self.runtime.sessions.read(self.sid)
        self.assertEqual(sum(e["type"] == "subagent_completed"
                             for e in parent_events), 3)

    def test_failed_branch_degrades_without_hurting_others(self):
        out = self._run(["query one", "query boom", "query two"])
        self.assertTrue(out["ok"])
        by_req = {b["request"]: b for b in out["branches"]}
        self.assertFalse(by_req["query boom"]["ok"])
        self.assertEqual(by_req["query boom"]["pool_count"], 0)
        self.assertTrue(by_req["query one"]["ok"])
        self.assertTrue(by_req["query two"]["ok"])
        # 其他分支的文献仍然合并入池
        self.assertEqual(out["pool_count"], 3)
        child = by_req["query boom"]["child_session"]
        events = self.runtime.sessions.read(child)
        self.assertEqual(events[-1]["payload"]["status"], "error")
        self.assertIn("fixture network failure", events[-1]["payload"]["error"])

    def test_offline_fanout_keeps_specialist_behavior(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = AgentRuntime(session_root=Path(d),
                                   permissions={"subagent.fanout": "allow"})
            sid = "fanoutoff01"
            runtime._session_agent[sid] = "research"
            runtime._workspaces[sid] = {}
            out = runtime._subagent_fanout({
                "__session_id": sid,
                "agents": ["literature", "literature"],
                "requests": ["large language model agents",
                             "multi agent collaboration"],
            })
            self.assertTrue(out["ok"])
            self.assertEqual(out["mode"], "offline")
            self.assertEqual(out["children"], 2)
            self.assertGreaterEqual(out["pool_count"], 1)
            self.assertTrue(all(b["ok"] for b in out["branches"]))
            self.assertTrue(all(b["source_counts"] == {} for b in out["branches"]))
            # 离线分支同样落子会话审计
            for branch in out["branches"]:
                events = runtime.sessions.read(branch["child_session"])
                self.assertEqual(events[0]["payload"]["parent"], sid)
                self.assertEqual(events[-1]["type"], "completed")

    def test_source_degradation_reaches_branch_result_and_workspace(self):
        metadata = {'source_counts': {'openalex': 0, 'crossref': 1},
                    'source_status': {'openalex': 'failed', 'crossref': 'success'},
                    'degraded': True, 'warnings': ['OpenAlex unavailable']}
        def search(*args, diagnostics=None, **kwargs):
            if diagnostics is not None:
                diagnostics.update(metadata)
            return [_work('C1', '真实检索夹具')], metadata['source_counts']
        with mock.patch.object(literature, '_dual_source_search', side_effect=search), \
             mock.patch.object(literature, 'score_pool', side_effect=_fake_score):
            result = self.runtime._subagent_fanout({'__session_id': self.sid,
                'agents': ['literature'], 'requests': ['topic']})
        self.assertTrue(result['ok'])
        self.assertTrue(result['degraded'])
        self.assertTrue(result['warnings'])
        self.assertEqual(result['branches'][0]['search_metadata'], metadata)
        saved = self.runtime._workspaces[self.sid]['fanout'][0]
        self.assertEqual(saved['search_metadata'], metadata)

    def test_all_failed_searches_keep_existing_evidence_and_source_diagnostics(self):
        original = [_work('existing', '已确认材料')]
        self.runtime._workspaces[self.sid]['pool'] = original
        metadata = {'source_counts': {'openalex': 0, 'crossref': 0},
                    'source_status': {'openalex': 'failed', 'crossref': 'failed'},
                    'degraded': True, 'warnings': ['All sources unavailable']}
        with mock.patch.object(literature, '_dual_source_search',
                               side_effect=literature.LiteratureSearchError('all failed', metadata)):
            result = self.runtime._subagent_fanout({'__session_id': self.sid,
                'agents': ['literature'], 'requests': ['topic']})
        self.assertFalse(result['ok'])
        self.assertEqual(result['branches'][0]['search_metadata'], metadata)
        self.assertEqual(self.runtime._workspaces[self.sid]['pool'], original)


if __name__ == "__main__":
    unittest.main()
