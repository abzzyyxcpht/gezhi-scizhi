"""Driver 底座强化测试：上下文压缩、失败自愈、并行执行、中断恢复对账。

沿用 test_adaptive_hardening 的隔离模式：临时 session_root、mock 掉
llm.chat / model_registry.resolve / prompts.agent_system，不触碰真实数据目录。
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src import llm, model_registry, prompts, settings
from src.agent_driver import AdaptiveDriver
from src.agent_runtime import AgentRuntime


def response(text="", calls=()):
    calls = [{"id": cid, "name": name, "arguments": arguments} for cid, name, arguments in calls]
    assistant = {"role": "assistant", "content": text,
                 "tool_calls": [{"id": c["id"], "type": "function", "function": {
                     "name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in calls]}
    return {"content": text, "tool_calls": calls, "assistant_message": assistant,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "usage_reported": True}}


def driver_state(runtime: AgentRuntime, sid: str, messages: list[dict], **extra) -> dict:
    """按 start() 的形状构造 driver 状态，便于直接落盘做恢复对账测试。"""
    state = {"schema_version": 1, "session_id": sid, "agent": "research",
             "status": "running", "messages": messages,
             "model_identity": AdaptiveDriver(runtime)._identity(),
             "steps": 1, "tokens": 20, "usage_complete": True,
             "max_steps": 16, "token_budget": 100000,
             "queue": [], "pending": None, "active_call": None, "active_calls": [],
             "used_call_ids": [], "unresolved": {}, "content": "",
             "repair_rounds": 0, "compressions": 0}
    state.update(extra)
    return state


class DriverResilienceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = {"connection_id": "connection", "profile_id": "profile", "model": "model",
                        "protocol": "responses", "base_url": "https://first.example/v1",
                        "thinking_style": "auto", "reasoning_effort": "auto", "thinking_budget": 4096,
                        "context_window": 32768, "max_output_tokens": 8192, "api_key": "test-key"}
        for patch in (mock.patch.object(settings, "_PATH", self.root / "settings.json"),
                      mock.patch.object(llm, "available", return_value=True),
                      mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(self.profile)),
                      mock.patch.object(prompts, "agent_system", return_value="Test system.")):
            patch.start()
            self.addCleanup(patch.stop)

    def runtime(self, permissions=None):
        return AgentRuntime(offline=False, llm_enabled=True, session_root=self.root, permissions=permissions)

    @staticmethod
    def register(runtime, name, handler, capability="read"):
        runtime._register(name, capability, name, handler, runtime._obj_schema({}))

    # ---- 上下文压缩 ----

    def test_context_compression_triggers_before_hard_limit(self):
        runtime = self.runtime()
        self.profile["context_window"] = 400  # 硬限 400 token，首次触发线 280 token
        self.register(runtime, "test.read", lambda _: {"ok": True, "rows": 12})
        counter = {"n": 0}

        def chat_impl(messages, **kwargs):
            if kwargs.get("node") == "context_compress":
                return {"content": "在线摘要：已完成早期消息折叠。", "tool_calls": [],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
            counter["n"] += 1
            if counter["n"] > 4:
                return response(text="汇总完成", calls=[])
            # 每条助手消息带 120 CJK 填充（≈80 token），保证第 3 轮后越过 280 token 软线
            return response(text="填" * 120, calls=[(f"c{counter['n']}", "test.read", {})])

        with mock.patch.object(llm, "chat", side_effect=chat_impl):
            result = runtime.start("验证压缩")
        self.assertEqual(result["status"], "done")
        sid = result["session_id"]
        compressed = [e for e in runtime.sessions.read(sid) if e["type"] == "context_compressed"]
        self.assertTrue(compressed, "超过 70% 阈值应先触发压缩而非报错")
        payload = compressed[0]["payload"]
        self.assertIn("from_messages", payload)
        self.assertIn("to_messages", payload)
        self.assertGreater(payload["compressed_count"], 0)
        saved = json.loads(AdaptiveDriver(runtime).path(sid).read_text(encoding="utf-8"))
        self.assertGreaterEqual(saved.get("compressions", 0), 1)
        self.assertIn("在线摘要", saved.get("last_compression_summary", ""))
        messages = saved["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertTrue(any(m["role"] == "user" and "在线摘要" in str(m.get("content")) for m in messages))
        # 尾部 tool 配对合法：每个 tool 消息的 call id 都能在尾部找到对应 assistant tool_calls
        assistant_ids = {c.get("id") for m in messages if m.get("role") == "assistant"
                         for c in (m.get("tool_calls") or [])}
        for m in messages:
            if m.get("role") == "tool":
                self.assertIn(m.get("tool_call_id"), assistant_ids)
        self.assertLess(result["context"]["tokens"], 400)

    def test_offline_compression_uses_deterministic_summary(self):
        runtime = self.runtime()
        driver = AdaptiveDriver(runtime)
        messages = [{"role": "system", "content": "sys"},
                    {"role": "user", "content": "原始目标：验证离线摘要"}]
        for i in range(3):
            messages.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": "test.read", "arguments": "{}"}}]})
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "name": "test.read",
                             "content": json.dumps({"ok": i % 2 == 0})})
            messages.append({"role": "assistant", "content": "填" * 300})
        state = driver_state(runtime, "compresscc01", messages)
        with mock.patch.object(llm, "available", return_value=False):
            ok = driver._compress_context(state, dict(self.profile))
        self.assertTrue(ok)
        self.assertEqual(state["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(state["messages"][1]["content"], "原始目标：验证离线摘要")
        summary = state["messages"][2]["content"]
        self.assertIn("[上下文压缩]", summary)
        self.assertIn("test.read✓", summary)
        self.assertIn("test.read✗", summary)
        self.assertIn("原始目标", summary)
        self.assertTrue(any(e["type"] == "context_compressed" for e in runtime.sessions.read("compresscc01")))

    def test_model_summary_failure_falls_back_to_deterministic(self):
        runtime = self.runtime()
        driver = AdaptiveDriver(runtime)
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "目标X"}]
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "c0", "type": "function", "function": {"name": "test.read", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": "c0", "name": "test.read",
                         "content": json.dumps({"ok": True})})
        messages.extend([response(calls=[("c1", "test.read", {})])["assistant_message"],
                         {"role": "tool", "tool_call_id": "c1", "name": "test.read", "content": '{"ok": true}'}])
        state = driver_state(runtime, "compresscc02", messages)

        def chat_impl(messages_arg, **kwargs):
            if kwargs.get("node") == "context_compress":
                raise RuntimeError("summary endpoint down")
            return response("unused")

        with mock.patch.object(llm, "chat", side_effect=chat_impl):
            ok = driver._compress_context(state, dict(self.profile))
        self.assertTrue(ok)
        self.assertIn("[上下文压缩]", state["messages"][2]["content"])

    # ---- 工具失败自愈 ----

    def test_repair_loop_recovers_after_transient_failure(self):
        runtime = self.runtime()
        handler = mock.Mock(side_effect=[{"ok": False, "error": "temporary glitch"}, {"ok": True}])
        self.register(runtime, "test.flaky", handler)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("try-1", "test.flaky", {})]),
                response(calls=[("try-2", "test.flaky", {})]),
                response("修复后完成")]) as chat:
            result = runtime.start("重试瞬时失败")
        self.assertEqual(result["status"], "done")
        self.assertEqual(handler.call_count, 2)
        second = chat.call_args_list[1].args[0]
        self.assertTrue(any(m["role"] == "user" and "工具执行反馈" in str(m.get("content"))
                            and "temporary glitch" in str(m.get("content")) for m in second))

    def test_repair_loop_exhausts_and_finishes_with_reason(self):
        runtime = self.runtime()
        handler = mock.Mock(return_value={"ok": False, "error": "persistent"})
        self.register(runtime, "test.fail", handler)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("f1", "test.fail", {})]),
                response(calls=[("f2", "test.fail", {})]),
                response(calls=[("f3", "test.fail", {})])]):
            result = runtime.start("持续失败")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "unresolved_tool_errors")
        saved = json.loads(AdaptiveDriver(runtime).path(result["session_id"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["repair_rounds"], 2)
        self.assertTrue(any(m["role"] == "user" and "工具执行反馈" in str(m.get("content"))
                            for m in saved["messages"]))

    def test_invalid_arguments_feedback_cannot_complete_without_success(self):
        runtime = self.runtime()
        handler = mock.Mock(return_value={"ok": True})
        runtime._register("test.typed", "read", "typed", handler,
                          runtime._obj_schema({"path": {"type": "string"}}, ["path"]))
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("bad", "test.typed", {})]), response("done")]) as chat:
            result = runtime.start("参数缺失")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "unresolved_tool_errors")
        self.assertEqual(chat.call_count, 2)
        handler.assert_not_called()

    # ---- 单步并行只读执行 ----

    def test_parallel_read_batch_preserves_tool_message_order(self):
        runtime = self.runtime()
        calls: list[str] = []
        lock = threading.Lock()

        def handler(args):
            with lock:
                calls.append(args["__session_id"])
            return {"ok": True, "seq": len(calls)}

        self.register(runtime, "test.probe", handler)
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("one", "test.probe", {}), ("two", "test.probe", {})]),
                response("汇总完成")]) as chat:
            result = runtime.start("并行读取")
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(calls), 2)
        messages = chat.call_args_list[1].args[0]
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_msgs], ["one", "two"])

    def test_read_write_mix_falls_back_to_serial_approval(self):
        runtime = self.runtime()
        reader = mock.Mock(return_value={"ok": True})
        writer = mock.Mock(return_value={"ok": True})
        self.register(runtime, "test.read", reader)
        self.register(runtime, "test.write", writer, "write")
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("r1", "test.read", {}), ("w1", "test.write", {})]),
                response("done")]):
            result = runtime.start("先读后写")
        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(result["pending"]["id"], "w1")
        reader.assert_called_once()
        writer.assert_not_called()

    # ---- 中断恢复对账 ----

    def test_resume_reconciles_result_logged_before_crash(self):
        runtime = self.runtime()
        sid = "resumelogged1"
        runtime.sessions.append(sid, "tool_call", {"id": "c1", "name": "test.read", "arguments": {}})
        runtime.sessions.append(sid, "tool_result", {"id": "c1", "name": "test.read",
                                                     "recoverable": True, "result": {"ok": True, "found": 3}})
        messages = [{"role": "system", "content": "Test system."},
                    {"role": "user", "content": "恢复我"},
                    {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "test.read", "arguments": "{}"}}]}]
        driver = AdaptiveDriver(runtime)
        driver._save(driver_state(runtime, sid, messages,
                                  active_call={"id": "c1", "name": "test.read", "arguments": {}}))
        with mock.patch.object(llm, "chat", return_value=response("继续完成")) as chat:
            result = runtime.continue_session(sid)
        self.assertEqual(result["status"], "done")
        self.assertEqual(chat.call_count, 1)
        saved = json.loads(driver.path(sid).read_text(encoding="utf-8"))
        tool_msgs = [m for m in saved["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn('"found": 3', tool_msgs[0]["content"])
        self.assertFalse(any(e["type"] == "error" and e["payload"].get("reason") == "interrupted_tool"
                             for e in runtime.sessions.read(sid)))

    def test_resume_synthesizes_interrupted_and_self_heals(self):
        runtime = self.runtime()
        sid = "resumecrash1"
        runtime.sessions.append(sid, "tool_call", {"id": "c1", "name": "test.read", "arguments": {}})
        messages = [{"role": "system", "content": "Test system."},
                    {"role": "user", "content": "恢复我"},
                    {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "test.read", "arguments": "{}"}}]}]
        driver = AdaptiveDriver(runtime)
        driver._save(driver_state(runtime, sid, messages,
                                  active_call={"id": "c1", "name": "test.read", "arguments": {}}))
        restored = self.runtime()
        self.register(restored, "test.read", mock.Mock(return_value={"ok": True}))
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("fix-1", "test.read", {})]),
                response("恢复完成")]) as chat:
            result = restored.continue_session(sid)
        self.assertEqual(result["status"], "done")
        self.assertEqual(chat.call_count, 2)
        saved = json.loads(AdaptiveDriver(restored).path(sid).read_text(encoding="utf-8"))
        self.assertTrue(any(m["role"] == "user" and "interrupted" in str(m.get("content"))
                            for m in saved["messages"]))
        self.assertTrue(any(m["role"] == "user" and "工具执行反馈" in str(m.get("content"))
                            for m in saved["messages"]))

    def test_resume_reconciles_parallel_batch_partially_observed(self):
        runtime = self.runtime()
        self.register(runtime, "test.read", mock.Mock(return_value={"ok": True}))
        sid = "resumepartial"
        for cid in ("p1", "p2"):
            runtime.sessions.append(sid, "tool_call", {"id": cid, "name": "test.read", "arguments": {}})
        runtime.sessions.append(sid, "tool_result", {"id": "p1", "name": "test.read",
                                                     "recoverable": True, "result": {"ok": True}})
        messages = [{"role": "system", "content": "Test system."},
                    {"role": "user", "content": "恢复我"},
                    {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "p1", "type": "function", "function": {"name": "test.read", "arguments": "{}"}},
                        {"id": "p2", "type": "function", "function": {"name": "test.read", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "p1", "name": "test.read",
                     "content": json.dumps({"ok": True})}]
        driver = AdaptiveDriver(runtime)
        driver._save(driver_state(runtime, sid, messages, active_calls=["p1", "p2"]))
        with mock.patch.object(llm, "chat", side_effect=[
                response(calls=[("fix-2", "test.read", {})]),
                response("恢复完成")]):
            result = runtime.continue_session(sid)
        self.assertEqual(result["status"], "done")
        saved = json.loads(driver.path(sid).read_text(encoding="utf-8"))
        tool_msgs = [m for m in saved["messages"] if m.get("role") == "tool"]
        # p1 不重复记账；p2 合成 interrupted 后由自愈循环兜住（fix-2 为修复轮的新观察）
        self.assertEqual([m["tool_call_id"] for m in tool_msgs[:2]], ["p1", "p2"])
        self.assertIn("interrupted", tool_msgs[1]["content"])


if __name__ == "__main__":
    unittest.main()
