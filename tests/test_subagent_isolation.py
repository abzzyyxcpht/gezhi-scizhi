# -*- coding: utf-8 -*-
"""真子代理与诚实性修复（计划 0.4.0 任务 1.3 + 1.5）。

覆盖：子代理上下文隔离（sidechain transcript 不进父 messages）、spec.prompt
生效（LLM 子代理 system 前缀 + 离线产物抬头）、独立预算（子步数 = 父预算一半
且不计入父步数）、subagent_status 事件序列、sidechain 文件落盘、fanout 等长
校验与并行回收、离线路径行为不变、fixtures 显式水印、_split_queries 边界。
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from src import llm, settings
from src.agent_runtime import AgentRuntime
from src.agents.specialists import DEMO_WATERMARK, run as run_spec


def response(text: str = "", tool: str | None = None, call_id: str = "call-1",
             args: dict | None = None) -> dict:
    """构造 llm.chat 的伪造模型结果（与 test_adaptive_driver 同形）。"""
    calls = ([{"id": call_id, "name": tool, "arguments": args or {}}]
             if tool else [])
    return {"content": text, "tool_calls": calls,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "assistant_message": {"role": "assistant", "content": text,
                                  "tool_calls": [
                                      {"id": c["id"], "type": "function",
                                       "function": {"name": c["name"],
                                                    "arguments": "{}"}}
                                      for c in calls]}}


class SubagentIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.object(settings, "_PATH", self.root / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(llm, "available", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def runtime(self, **kwargs) -> AgentRuntime:
        kwargs.setdefault("offline", False)
        kwargs.setdefault("llm_enabled", True)
        kwargs.setdefault("session_root", self.root)
        return AgentRuntime(**kwargs)

    # ---- 工具方法 ----

    @staticmethod
    def _chat_router(child_tool: str = "explore.run"):
        """按 system 前缀区分父子代理的 llm.chat 伪造实现。

        子代理 system 含编制表角色提示词（只读探测智能体）；父代理第一步
        派发 subagent.spawn，第二步收尾汇报。
        """
        def chat(messages, **kwargs):
            system = str(messages[0].get("content") or "")
            has_tool = any(m.get("role") == "tool" for m in messages)
            if "只读探测智能体" in system:
                if not has_tool:
                    return response(tool=child_tool, call_id=uuid.uuid4().hex[:8],
                                    args={"request": "探测工作区"})
                return response("子代理完成报告")
            if not has_tool:
                return response(tool="subagent.spawn", call_id="spawn-1",
                                args={"agent": "explore", "request": "探测工作区"})
            return response("父代理总结")
        return chat

    def _parent_with_spawn(self, chat_side_effect) -> tuple[AgentRuntime, str, dict]:
        """父会话（adaptive driver）调用一次 subagent.spawn 后收尾。"""
        runtime = self.runtime(permissions={"subagent.spawn": "allow"})
        with mock.patch.object(llm, "chat", side_effect=chat_side_effect):
            result = runtime.start("派发探测子智能体")
        return runtime, result["session_id"], result

    def _child_ids(self, runtime: AgentRuntime, sid: str) -> list[str]:
        return list(runtime._children.get(sid) or [])

    # ---- 用例 ----

    def test_child_context_isolated_from_parent_messages(self) -> None:
        """子代理中间步骤只进 sidechain，父 messages 仅见 spawn 结果摘要。"""
        runtime, sid, result = self._parent_with_spawn(self._chat_router())
        self.assertEqual(result["status"], "done")
        children = self._child_ids(runtime, sid)
        self.assertEqual(len(children), 1)
        child = children[0]
        # 子代理有独立的 driver 状态与更多中间消息
        child_state = json.loads((self.root / f"{child}.driver.json")
                                 .read_text(encoding="utf-8"))
        child_roles = [m["role"] for m in child_state["messages"]]
        self.assertIn("tool", child_roles)  # 子代理自己执行了 explore.run
        # 父 messages 中没有子代理的工具调用与中间观察
        parent_state = json.loads((self.root / f"{sid}.driver.json")
                                  .read_text(encoding="utf-8"))
        parent_text = json.dumps(parent_state["messages"], ensure_ascii=False)
        self.assertNotIn("explore.run", parent_text)
        self.assertNotIn("exp-9", parent_text)
        tool_msgs = [m for m in parent_state["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "spawn-1")

    def test_spec_prompt_becomes_child_system_prefix(self) -> None:
        """spec.prompt 生效：LLM 子代理的 system 前缀包含编制表角色提示词。"""
        runtime, sid, _ = self._parent_with_spawn(self._chat_router())
        child = self._child_ids(runtime, sid)[0]
        child_state = json.loads((self.root / f"{child}.driver.json")
                                 .read_text(encoding="utf-8"))
        system = child_state["messages"][0]["content"]
        self.assertIn("只读探测智能体", system)  # explore spec.prompt
        self.assertIn("当前职责", system)  # 全局诚实性契约仍然附加

    def test_child_has_independent_step_budget(self) -> None:
        """子代理步数预算 = 父预算一半，且子步数不计入父会话。"""
        settings.save({"agent_max_steps": 4})
        runtime = self.runtime(permissions={"subagent.spawn": "allow"})
        parent = "budgetparent1"
        runtime._session_agent[parent] = "research"
        runtime._workspaces[parent] = {}
        counter = iter(range(100))

        def chat(*args, **kwargs):
            return response(tool="explore.run",
                            call_id=f"loop-{next(counter)}",
                            args={"request": "x"})

        with mock.patch.object(llm, "chat", side_effect=chat):
            out = runtime._subagent_spawn({
                "__session_id": parent, "agent": "explore", "request": "x"})
        self.assertFalse(out["ok"])
        self.assertIn("步骤上限", out["error"])
        child = out["child_session"]
        # 子代理在 2 步（4÷2）时被自己的预算截停
        child_state = json.loads((self.root / f"{child}.driver.json")
                                 .read_text(encoding="utf-8"))
        self.assertEqual(child_state["steps"], 2)
        meta = json.loads((self.root / parent / "children" / f"{child}.meta.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(meta["steps"], 2)
        self.assertEqual(meta["status"], "error")
        # 父会话没有模型步骤记录：子步数完全独立
        parent_events = runtime.sessions.read(parent)
        self.assertFalse(any(e["type"] == "assistant_message"
                             for e in parent_events))

    def test_subagent_status_event_sequence_in_parent_log(self) -> None:
        """父 JSONL 按 running→done 顺序记录 subagent_status 事件。"""
        runtime, sid, _ = self._parent_with_spawn(self._chat_router())
        child = self._child_ids(runtime, sid)[0]
        statuses = [(e["payload"]["status"], e["payload"]["child"])
                    for e in runtime.sessions.read(sid)
                    if e["type"] == "subagent_status"]
        self.assertEqual(statuses, [("running", child), ("done", child)])
        first = next(e for e in runtime.sessions.read(sid)
                     if e["type"] == "subagent_status")
        self.assertEqual(first["payload"]["agent"], "explore")
        self.assertIn("steps", first["payload"])
        self.assertIn("tokens", first["payload"])

    def test_sidechain_transcript_and_meta_written(self) -> None:
        """sidechain：children/<child>.jsonl + meta.json，且不进入会话列表。"""
        runtime, sid, _ = self._parent_with_spawn(self._chat_router())
        child = self._child_ids(runtime, sid)[0]
        transcript = self.root / sid / "children" / f"{child}.jsonl"
        meta_path = self.root / sid / "children" / f"{child}.meta.json"
        self.assertTrue(transcript.is_file())
        self.assertTrue(meta_path.is_file())
        events = [json.loads(line) for line in
                  transcript.read_text(encoding="utf-8").splitlines()]
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "user_message")
        self.assertEqual(kinds[-1], "completed")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["agent"], "explore")
        self.assertEqual(meta["status"], "done")
        self.assertEqual(meta["parent"], sid)
        # sidechain 不出现在顶层会话列表
        listed = {row["session_id"] for row in runtime.sessions.list()}
        self.assertIn(sid, listed)
        self.assertNotIn(child, listed)

    def test_permission_grants_inherited_from_parent(self) -> None:
        """父会话的 allow_session 授权被子会话继承。"""
        runtime = self.runtime(permissions={"subagent.spawn": "allow"})
        parent = "grantparent1"
        runtime._session_agent[parent] = "research"
        runtime._workspaces[parent] = {}
        runtime.policy.allow_for_session("workspace.read", parent)
        with mock.patch.object(llm, "chat", return_value=response("完成")):
            out = runtime._subagent_spawn({
                "__session_id": parent, "agent": "explore", "request": "x"})
        self.assertIn("workspace.read",
                      runtime.policy._session_grants[out["child_session"]])

    def test_fanout_validates_agent_request_pairing(self) -> None:
        """fanout 显式校验 agents/requests 等长，删除 agents[-1] 粗糙回退。"""
        runtime = self.runtime(permissions={"subagent.fanout": "allow"})
        sid = "fanoutcheck1"
        runtime._session_agent[sid] = "research"
        runtime._workspaces[sid] = {}
        out = runtime._subagent_fanout({
            "__session_id": sid,
            "agents": ["literature", "explore"],
            "requests": ["只有一路请求"],
        })
        self.assertFalse(out["ok"])
        self.assertIn("等长", out["error"])
        out = runtime._subagent_fanout({
            "__session_id": sid,
            "agents": ["不存在的专家"],
            "requests": ["query"],
        })
        self.assertFalse(out["ok"])
        self.assertIn("未知智能体", out["error"])

    def test_fanout_llm_branches_run_and_reap_in_parallel(self) -> None:
        """在线 fanout 每路走真子代理并行执行并全部回收。"""
        runtime = self.runtime(permissions={"subagent.fanout": "allow"})
        sid = "fanoutllm001"
        runtime._session_agent[sid] = "research"
        runtime._workspaces[sid] = {}
        local = threading.local()

        def chat(messages, **kwargs):
            count = getattr(local, "count", 0)
            local.count = count + 1
            if count == 0:
                return response(tool="explore.run", call_id=uuid.uuid4().hex[:8],
                                args={"request": "探测"})
            return response("子任务完成")

        with mock.patch.object(llm, "chat", side_effect=chat):
            out = runtime._subagent_fanout({
                "__session_id": sid,
                "agents": ["explore", "explore"],
                "requests": ["第一路探测", "第二路探测"],
            })
        self.assertTrue(out["ok"])
        self.assertEqual(out["mode"], "llm")
        self.assertEqual(out["children"], 2)
        self.assertTrue(all(b["ok"] for b in out["branches"]))
        self.assertEqual(len(self._child_ids(runtime, sid)), 2)
        # 每路 sidechain meta 都记录 done 与独立步数
        for child in self._child_ids(runtime, sid):
            meta = json.loads((self.root / sid / "children" / f"{child}.meta.json")
                              .read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "done")
            self.assertEqual(meta["mode"], "llm")
        statuses = sorted(e["payload"]["status"] for e in runtime.sessions.read(sid)
                          if e["type"] == "subagent_status")
        self.assertEqual(statuses, ["done", "done", "running", "running"])

    def test_offline_spawn_keeps_deterministic_behavior_with_sidechain(self) -> None:
        """离线路径：确定性 runner 行为不变，同时落 sidechain 与 status 事件。"""
        runtime = AgentRuntime(session_root=self.root,
                               permissions={"subagent.spawn": "allow"})
        sid = "offlinespawn1"
        runtime._session_agent[sid] = "research"
        runtime._workspaces[sid] = {"topic": "large language model agents"}
        out = runtime._subagent_spawn({
            "__session_id": sid, "agent": "literature",
            "request": "large language model agents"})
        self.assertTrue(out["ok"])
        self.assertGreater(out["pool_count"], 0)
        child = out["child_session"]
        self.assertEqual(runtime._children[sid], [child])
        self.assertGreater(len(runtime._workspaces[sid].get("pool") or []), 0)
        # 离线也写 sidechain 与 meta
        self.assertTrue((self.root / sid / "children" / f"{child}.jsonl").is_file())
        meta = json.loads((self.root / sid / "children" / f"{child}.meta.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "done")
        self.assertEqual(meta["mode"], "deterministic")
        statuses = [e["payload"]["status"] for e in runtime.sessions.read(sid)
                    if e["type"] == "subagent_status"]
        self.assertEqual(statuses, ["running", "done"])

    def test_fixture_watermark_present_in_demo_artifacts(self) -> None:
        """显式离线演示带水印；在线失败不产生演示文献。"""
        ws: dict = {}
        lit = run_spec("literature", "large language model agents",
                       workspace=ws, offline=True)
        self.assertTrue(lit["ok"])
        self.assertIn(DEMO_WATERMARK, lit["notice"])
        ext = run_spec("extraction", "large language model agents",
                       workspace=ws, offline=True)
        self.assertTrue(ext["ok"])
        digest = str(ws.get("digest_md") or "")
        self.assertTrue(digest.startswith(DEMO_WATERMARK))
        self.assertIn("执行专家", digest)  # spec.prompt 写入产物抬头
        ws2: dict = {}
        from src.agents import literature as literature_mod
        with mock.patch.object(literature_mod, "literature_recursive_filter",
                               side_effect=ConnectionError("no network")):
            degraded = run_spec("literature", "agent collaboration",
                                workspace=ws2, offline=False)
        self.assertFalse(degraded["ok"])
        self.assertIn("no network", degraded["error"])
        self.assertNotIn("pool", ws2)
        self.assertNotIn(DEMO_WATERMARK, degraded.get("notice", ""))


class SplitQueriesTest(unittest.TestCase):
    """_split_queries 中文分号边界（任务 1.5 之 2）。"""

    def test_basic_and_mixed_separators(self) -> None:
        split = AgentRuntime._split_queries
        self.assertEqual(split("检索A；检索B"), ["检索A", "检索B"])
        self.assertEqual(split("检索A;检索B"), ["检索A", "检索B"])
        self.assertEqual(split("检索A；检索B;检索C"), ["检索A", "检索B", "检索C"])

    def test_consecutive_and_edge_separators(self) -> None:
        split = AgentRuntime._split_queries
        self.assertEqual(split("检索A;;；；检索B"), ["检索A", "检索B"])
        self.assertEqual(split("；检索A；"), ["检索A"])
        self.assertEqual(split("检索A；"), ["检索A"])
        self.assertEqual(split("；；"), ["；；"])

    def test_whitespace_and_no_separator_unchanged(self) -> None:
        split = AgentRuntime._split_queries
        self.assertEqual(split("  保留原样的请求  "), ["  保留原样的请求  "])
        self.assertEqual(split(" 检索A ； 检索B "), ["检索A", "检索B"])
        self.assertEqual(split(""), [""])


if __name__ == "__main__":
    unittest.main()
