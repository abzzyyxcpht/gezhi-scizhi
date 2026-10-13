# -*- coding: utf-8 -*-
"""桌面版回调冒烟：逐个页面切换，把每个页面上的事件回调都用 stub 事件跑一遍。

构建测试只能发现"构建期"错误；回调里的错误（服务调用签名、flet 对话 API、
控件属性）要等用户真的点按钮才炸——本测试覆盖那一步，且通过点击顶部导航按钮
遍历全部 6 个页面（新任务/运行/成果/数据/智能体/设置）。

副作用防护：设置写入、建任务、导出、删除、Agent 运行时全部打桩，
只验证回调代码路径本身能跑通。
"""
from __future__ import annotations

import inspect
import unittest
from unittest import mock

from src import service, settings as app_settings
from src.ui import desktop

HANDLER_ATTRS = ("on_click", "on_change", "on_select", "on_submit")


def collect(node, acc=None, depth=0):
    """深度遍历 flet 控件树，收集所有可调用回调。"""
    if acc is None:
        acc = []
    if node is None or depth > 40:
        return acc
    for attr in ("controls", "content", "actions", "leading", "trailing",
                 "title", "subtitle", "label", "rows", "columns"):
        val = getattr(node, attr, None)
        if val is None:
            continue
        items = val if isinstance(val, (list, tuple)) else [val]
        for it in items:
            if hasattr(it, "__dict__"):
                collect(it, acc, depth + 1)
    for h in HANDLER_ATTRS:
        fn = getattr(node, h, None)
        if callable(fn) and not isinstance(fn, mock.Mock):
            acc.append((type(node).__name__, h, fn))
    return acc


def fake_runtime() -> mock.MagicMock:
    """形状健全的 AgentRuntime 桩：避免 Mock 返回值被真实迭代时误报。"""
    rt = mock.MagicMock(name="AgentRuntime")
    rt.sessions.read.return_value = []
    rt.sessions.list.return_value = []
    rt.sessions.snapshot.return_value = {
        "session_id": "s1", "status": "done", "agent": "research",
        "mode": "agent", "workspace_mode": "research", "request": "x", "events": [],
    }
    rt.list_agents.return_value = []
    rt.plan.return_value = {"request": "x", "steps": [], "notes": []}
    rt.start.return_value = {"session_id": "s1", "status": "done", "events": []}
    rt.approve.return_value = {"status": "done"}
    return rt


class DesktopHandlerTest(unittest.TestCase):
    def setUp(self):
        """播种两个任务：历史卡片（含删除按钮回调）才会参与构建。

        这样本测试不依赖仓库 data/ 里恰好有任务档案——数据目录被隔离或
        换到干净机器上时，断言依然成立。
        """
        from src.orchestrator.engine import TaskContext
        self._seeded = []
        for i in range(2):
            ctx = TaskContext(f"handlertest{i}", "research_pipeline",
                              {"topic": f"测试课题 {i}"})
            ctx.status = "done"
            service._tasks[ctx.task_id] = ctx
            self._seeded.append(ctx.task_id)

    def tearDown(self):
        for tid in getattr(self, "_seeded", []):
            service._tasks.pop(tid, None)

    def _build(self):
        page = mock.MagicMock(name="Page")

        def run_task(fn, *args, **kwargs):
            """测试桩：真实 flet 会调度协程，这里调用并关闭，避免 never awaited 告警。"""
            if callable(fn):
                res = fn(*args, **kwargs)
                if inspect.iscoroutine(res):
                    res.close()

        page.run_task.side_effect = run_task
        page.run_task.scheduled = []
        _orig = run_task

        def run_task_recording(fn, *args, **kwargs):
            page.run_task.scheduled.append(fn)
            return _orig(fn, *args, **kwargs)

        page.run_task.side_effect = run_task_recording
        desktop.main(page)
        roots = [c.args[0] for c in page.add.call_args_list if c.args]
        roots += list(page.controls or [])
        return page, roots

    def test_every_page_handler_runs_without_error(self):
        with mock.patch.object(app_settings, "save"), \
                mock.patch("src.bridge._runtimes", {}), \
                mock.patch.object(service, "create_task", return_value="fake-task"), \
                mock.patch.object(service, "export_artifacts", return_value=[]), \
                mock.patch.object(service, "delete_task", return_value=True), \
                mock.patch("src.agent_runtime.AgentRuntime",
                           return_value=fake_runtime()):
            page, roots = self._build()
            event = mock.MagicMock(name="Event")

            all_handlers = []
            for r in roots:
                all_handlers += collect(r)
            # 结构性下限（导航 6 + 首页若干）：数据目录被隔离时归档为空，
            # 阈值不能再按"真实归档有 14 个任务卡片"来定，否则换个环境就假失败
            self.assertGreaterEqual(len(all_handlers), 10,
                                    f"只收集到 {len(all_handlers)} 个回调，遍历逻辑可能失效")
            kinds = {tname for tname, _attr, _fn in all_handlers}
            self.assertIn("Container", kinds)       # 左栏导航（DSH sidebar 席位）
            self.assertIn("IconButton", kinds)      # 种子任务的卡片删除按钮

            # 左栏导航：点它们切换页面（事件会驱动 body.content 换页）。
            # 导航按钮在 ZCode 对标轮由 Container 改为 TextButton（静态可点击感），
            # 两种容器类型的 on_click 都算可点击席位，逐个点一遍即全页面回调冒烟。
            nav = [fn for tname, attr, fn in all_handlers
                   if attr == "on_click" and tname in ("Container", "TextButton")]
            self.assertGreaterEqual(len(nav), 6, "未找到 6 个导航项")

            failures: list[str] = []
            seen: set[tuple[int, str]] = set()

            def run(fn, label):
                key = (fn.__code__.co_firstlineno, label)
                if key in seen:
                    return
                seen.add(key)
                try:
                    fn(event)
                except Exception as exc:  # noqa: BLE001 —— 收集全部失败后统一报告
                    failures.append(f"{label} (L{fn.__code__.co_firstlineno}) "
                                    f"→ {type(exc).__name__}: {exc}")

            # 切到每个页面后再收集该页回调（控件树是同一对象图，换页即换子树）
            for i, nav_fn in enumerate(nav):
                run(nav_fn, f"导航[{i}]")
                for tname, attr, fn in collect(roots[0]) if roots else []:
                    run(fn, f"{tname}.{attr}")

            self.assertEqual(failures, [],
                             "以下回调执行失败：\n  " + "\n  ".join(failures))
            # 至少有一个回调把协程函数交给了 run_task（覆盖 FilePicker 修复）
            coro_fns = [f for f in getattr(page.run_task, "scheduled", [])
                        if inspect.iscoroutinefunction(f)]
            self.assertTrue(coro_fns,
                            "没有任何回调通过 run_task 调度协程（FilePicker 修复可能回退）")
            # 覆盖度：至少应触达 5 个以上不同页面区段（按回调源码行号粗判）
            lines = {k[0] for k in seen if k[1] != "导航[0]"}
            self.assertGreaterEqual(len(lines), 15,
                                    f"仅触达 {len(lines)} 个回调，页面覆盖可能不足")


    def test_wrap_containers_never_hold_expanded_children(self):
        """Flutter Wrap 不容纳 Expanded 子件，wrap 行/列里放 expand 子件会让该
        子树布局中止，并拖垮同一列里的后续控件（2026-10-09 桌面侧栏只渲染第一
        个子控件的根因：模式开关 Row(wrap=True) 按钮 expand=True）。"""
        with mock.patch.object(app_settings, "save"),                 mock.patch("src.bridge._runtimes", {}),                 mock.patch("src.agent_runtime.AgentRuntime",
                           return_value=fake_runtime()):
            _page, roots = self._build()

        def walk(node, depth=0):
            if node is None or depth > 40:
                return
            yield node
            for attr in ("controls", "content", "actions", "leading", "trailing",
                         "title", "subtitle", "label", "rows", "columns"):
                val = getattr(node, attr, None)
                if val is None:
                    continue
                items = val if isinstance(val, (list, tuple)) else [val]
                for it in items:
                    if hasattr(it, "__dict__"):
                        yield from walk(it, depth + 1)

        offenders = []
        for root in roots:
            for node in walk(root):
                if getattr(node, "wrap", False) and type(node).__name__ in ("Row", "Column"):
                    for child in getattr(node, "controls", None) or []:
                        if getattr(child, "expand", None):
                            offenders.append(f"{type(node).__name__}(wrap=True) > "
                                             f"{type(child).__name__}(expand={child.expand})")
        self.assertEqual(offenders, [],
                         "wrap 容器内含 expand 子件（Flutter Wrap 不容纳 Expanded）："
                         + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()
