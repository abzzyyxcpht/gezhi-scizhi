"""桌面导航回归：真实回调必须同步显示当前任务/会话，避免跨会话审批。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from src.agent_runtime import SessionStore
from src.ui import desktop


def event(kind: str, **payload) -> dict:
    return {"type": kind, "timestamp": "2026-09-16T10:00:00+0800",
            "payload": payload}


class DesktopNavigationTest(unittest.TestCase):
    def setUp(self):
        self.tasks = {
            tid: SimpleNamespace(params={"topic": topic}, artifacts={},
                                 node_durations={}, started_at=0,
                                 finished_at=0, workflow="research_pipeline",
                                 status="done", pointer=None)
            for tid, topic in (("task-a", "第一项研究"), ("task-b", "第二项研究"))
        }
        self.artifacts = {
            tid: {"status": "done", "status_text": "已完成", "pointer": None,
                  "pool": [], "rounds": [], "citations": [],
                  "digest_md": f"{ctx.params['topic']}的摘要", "outline_md": "",
                  "events": [{"ts": "10:00:00", "kind": "workflow",
                              "message": f"{ctx.params['topic']}完成"}]}
            for tid, ctx in self.tasks.items()
        }
        self.session_events = {
            "session-a": [event("user_message", text="会话甲"),
                          event("permission_requested", tool_call_id="call-a",
                                tool="research.search", capability="network",
                                arguments={"query": "会话甲"})],
            "session-b": [event("user_message", text="会话乙"),
                          event("permission_requested", tool_call_id="call-b",
                                tool="research.search", capability="network",
                                arguments={"query": "会话乙"}),
                          event("permission_granted", tool_call_id="call-b",
                                tool="research.search", scope="allow"),
                          event("completed", status="done")],
            "session-c": [event("user_message", text="会话丙"),
                          event("permission_requested", tool_call_id="call-c",
                                tool="research.search", capability="network",
                                arguments={"query": "会话丙"})],
        }
        self.session_root = tempfile.TemporaryDirectory(prefix="gezhi-ui-sessions-")
        self.addCleanup(self.session_root.cleanup)
        self.session_store = SessionStore(self.session_root.name)
        for sid, events in self.session_events.items():
            for saved_event in events:
                self.session_store.append(sid, saved_event["type"],
                                          saved_event["payload"])
        self.runtime = mock.MagicMock(name="AgentRuntime")
        self.runtime.tools = {}
        self.runtime.sessions = self.session_store
        self.runtime.approve.side_effect = lambda sid, *_: {
            "session_id": sid, "status": "done"}
        real_settings_load = desktop.app_settings.load
        for patcher in (
            mock.patch("src.bridge._runtimes", {}),
            mock.patch("src.research_repo.commit_task", return_value=None),
            mock.patch.object(desktop.service, "init"),
            mock.patch.object(desktop.service, "list_tasks", return_value=[]),
            mock.patch.object(desktop.service, "get", side_effect=self.tasks.get),
            mock.patch.object(desktop.service, "artifacts_of",
                              side_effect=self.artifacts.__getitem__),
            mock.patch.object(desktop.llm, "available", return_value=False),
            mock.patch("src.agent_runtime.AgentRuntime", return_value=self.runtime),
            # 夹具树锁定浅色：settings 默认深色（2026-10-09 起），而桌面主题
            # 回归测试验证的是白底输入的可读性与边界（深色由 test_brand_contrast
            # 的双板 bar 与 test_dynamic_theme 的取值断言覆盖）。
            mock.patch.object(desktop.app_settings, "load",
                              side_effect=lambda: {**real_settings_load(),
                                                   "appearance_dark_mode": False}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.page = mock.MagicMock(name="Page")
        self.page.width = 1440
        self.page.height = 900
        self.page.run_thread.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        # main 的局部对象就是生产回调使用的对象。捕获返回帧，不复制实现，
        # 也不为测试给生产代码增加公开状态或特殊分支。
        ui = {}
        previous_profile = sys.getprofile()

        def capture(frame, kind, arg):
            if kind == "return" and frame.f_code is desktop.main.__code__:
                ui.update(frame.f_locals)

        try:
            sys.setprofile(capture)
            desktop.main(self.page)
        finally:
            sys.setprofile(previous_profile)
        self.ui = SimpleNamespace(**ui)

    def test_general_settings_save_does_not_overwrite_model_credentials(self):
        with mock.patch.object(desktop.app_settings, 'save') as save, mock.patch('src.plugins.load'), mock.patch('src.mcp.register_actions'):
            self.ui.save_general_settings(None)
        saved = save.call_args.args[0]
        self.assertIn('agent_max_steps', saved)
        self.assertNotIn('llm_api_key', saved)
        self.assertNotIn('llm_base_url', saved)
        self.assertNotIn('llm_model', saved)

    def poll_once(self):
        class EndPoll(Exception):
            pass

        calls = 0

        async def sleep(_delay):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise EndPoll

        with mock.patch.object(desktop.asyncio, "sleep", side_effect=sleep):
            try:
                asyncio.run(self.ui.poller())
            except EndPoll:
                pass

    def open_session(self, sid: str):
        # 点击真实历史会话行，允许布局换容器，但保留会话身份的文本。
        def contains(node):
            if getattr(node, "value", None) == sid:
                return True
            return any(contains(child) for child in children(node))

        def children(node):
            for name in ("controls", "content", "title", "subtitle"):
                value = getattr(node, name, None)
                if isinstance(value, (list, tuple)):
                    yield from value
                elif value is not None and hasattr(value, "__dict__"):
                    yield value

        def find(node):
            handler = getattr(node, "on_click", None)
            if callable(handler) and contains(node):
                return handler
            for child in children(node):
                found = find(child)
                if found:
                    return found
            return None

        handler = find(self.ui.agent_sessions_col)
        self.assertIsNotNone(handler, f"未找到历史会话 {sid}")
        handler(None)

    def show_pending_a(self):
        self.ui.render_agent_result({
            "session_id": "session-a", "status": "waiting_approval",
            "pending": {"id": "call-a", "tool": "research.search",
                        "capability": "network", "arguments": {"query": "会话甲"}},
        })

    def test_results_navigation_loads_finished_task_without_another_poll(self):
        self.ui.open_task("task-a")
        self.poll_once()  # 任务终态已被轮询消费，后续心跳签名不再变化。
        self.ui.nav_to(2)
        self.assertEqual(self.ui.digest_md.value, "第一项研究的摘要",
                         "进入成果页必须直接读取当前任务，不能依赖下一次状态变化")

    def test_switching_equal_status_tasks_updates_monitor_immediately(self):
        self.ui.open_task("task-a")
        self.poll_once()
        self.ui.open_task("task-b")  # 与 A 的状态、节点数、事件数、指针相同。
        self.assertIn("第二项研究", self.ui.monitor_title.value,
                      "切换任务后标题必须立即对应选中任务")
        messages = " ".join(str(getattr(c, "value", ""))
                            for c in self.ui.log_col.controls)
        self.assertIn("第二项研究完成", messages,
                      "切换任务后应立即显示该任务日志")
        self.assertNotIn("第一项研究完成", messages)

    def test_opening_completed_session_clears_previous_approval(self):
        self.show_pending_a()
        self.open_session("session-b")
        self.assertEqual(self.ui.agent_state["session_id"], "session-b")
        self.assertFalse(self.ui.agent_approval_card.visible,
                         "历史会话已完成时不能显示其他会话的审批按钮")

    def test_opening_pending_session_approves_its_own_tool_call(self):
        self.show_pending_a()
        self.open_session("session-c")
        self.assertEqual(self.ui.agent_state["session_id"], "session-c")
        self.assertTrue(self.ui.agent_approval_card.visible)
        actions = self.ui.agent_approval_card.content.controls[-1].controls
        actions[0].on_click(None)
        self.runtime.approve.assert_called_once_with("session-c", "call-c", "allow")

    def test_approval_uses_original_session_runtime_when_default_model_changes(self):
        original = mock.MagicMock()
        original.approve.return_value = {"session_id": "session-a", "status": "done"}
        original.sessions = self.session_store
        with mock.patch.object(desktop.llm, "available", return_value=True), \
                mock.patch("src.bridge._session_runtime", return_value=original) as resolve:
            self.show_pending_a()
            self.ui.agent_approval_card.content.controls[-1].controls[0].on_click(None)
        resolve.assert_any_call("session-a")
        original.approve.assert_called_once_with("session-a", "call-a", "allow")
        self.runtime.approve.assert_not_called()

    def test_waiting_swarm_children_can_continue_and_mode_is_valid(self):
        sid = "swarm-parent"
        self.session_store.append(sid, "user_message", {"text": "协作研究", "mode": "swarm", "offline": True})
        self.session_store.append(sid, "driver_paused", {"status": "waiting_children"})
        self.ui.select_agent_session(sid)
        self.assertTrue(self.ui.agent_continue_btn.visible)
        self.assertEqual(self.ui.agent_mode.value, "agent")


if __name__ == "__main__":
    unittest.main()
