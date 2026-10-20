# -*- coding: utf-8 -*-
"""桌面版构建冒烟：用 stub Page 跑一遍 main()，把"点开才炸"的控件树错误前移到测试。

这类错误（辅助函数签名不匹配、flet 参数名写错）在 import 期发现不了，
只在构建到那一行时才抛——本测试正好覆盖那一步。
"""
from __future__ import annotations

import unittest
from unittest import mock

import flet as ft  # noqa: F401  （保证 flet 可用，与桌面版同环境）

from src.ui import brand, desktop


class DesktopBuildTest(unittest.TestCase):
    def test_main_builds_full_control_tree(self):
        page = mock.MagicMock(name="Page")
        with mock.patch.object(desktop, "ft", desktop.ft):
            desktop.main(page)          # 构建全部页面、控件与事件回调
        # 页面构建完成后应至少发生过一次更新调用
        self.assertTrue(page.update.called or page.add.called
                        or page.dialog is not None)

    def test_graph_tab_wired(self):
        """成果页必须有「证据图谱」页签，且视图数与页签数一致（防漏接）。"""
        real_tabs = desktop.ft.Tabs
        seen: dict = {}

        def spy(*a, **kw):
            seen.update(kw)
            return real_tabs(*a, **kw)

        page = mock.MagicMock(name="Page")
        with mock.patch.object(desktop.ft, "Tabs", spy):
            desktop.main(page)
        self.assertIn("content", seen, "成果页应构建 Tabs")
        tab_bar, tab_view = seen["content"].controls[0], seen["content"].controls[1]
        labels = [t.label for t in tab_bar.tabs]
        self.assertIn("证据图谱", labels)
        self.assertEqual(len(tab_view.controls), len(labels),
                         "页签数与视图数必须一致")
        self.assertEqual(seen.get("length"), len(labels))

    def test_main_registers_poller(self):
        page = mock.MagicMock(name="Page")
        desktop.main(page)
        self.assertTrue(page.run_task.called, "应注册后台轮询任务")


class BrandCopyHonestyTest(unittest.TestCase):
    """README 声明“没有虚构性能提升”：回执只报可核验计数，不折算节省时间。"""

    def test_pipeline_receipt_reports_verifiable_counts(self):
        text = brand.pipeline_receipt_text(12, 3, tool_calls=7, human_handoffs=2)
        for expected in ("12 篇", "3 轮", "工具调用 7 次", "人工接管 2 次"):
            self.assertIn(expected, text)
        for claim in ("省下", "节省", "小时", "分钟"):
            self.assertNotIn(claim, text)

    def test_pipeline_receipt_omits_counts_it_cannot_verify(self):
        text = brand.pipeline_receipt_text(5, 1)
        self.assertNotIn("工具调用", text)
        self.assertNotIn("人工接管", text)
        self.assertEqual("", brand.pipeline_receipt_text(0, 1))


if __name__ == "__main__":
    unittest.main()
