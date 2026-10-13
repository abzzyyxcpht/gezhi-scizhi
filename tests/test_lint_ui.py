# -*- coding: utf-8 -*-
"""UI 静态校验器的回归测试：确认三类坑都能被抓到（否则校验器形同虚设）。"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lint_ui  # noqa: E402

try:
    import flet as _ft
except ImportError:  # 无 flet 时跳过需要 flet 的两项
    _ft = None


def _tree(code: str) -> ast.AST:
    return ast.parse(code)


class CustomKwargTest(unittest.TestCase):
    def test_catches_missing_kwarg(self):
        code = (
            "def card(content, padding=8, expand=None):\n"
            "    return (content, padding, expand)\n"
            "card('x', width=260)\n"
        )
        found = lint_ui.check_custom_kwargs(_tree(code), "t.py")
        self.assertEqual(len(found), 1)
        self.assertIn("width", found[0])

    def test_external_method_does_not_use_same_named_local_signature(self):
        code = "def plan():\n    return runtime.plan('x', mode='plan')\n"
        self.assertEqual(lint_ui.check_custom_kwargs(_tree(code), "t.py"), [])

    def test_allows_declared_kwarg(self):
        code = (
            "def card(content, width=None):\n"
            "    return (content, width)\n"
            "card('x', width=260)\n"
        )
        self.assertEqual(lint_ui.check_custom_kwargs(_tree(code), "t.py"), [])

    def test_allows_kwargs_sink(self):
        code = (
            "def card(content, **kw):\n"
            "    return (content, kw)\n"
            "card('x', whatever=1)\n"
        )
        self.assertEqual(lint_ui.check_custom_kwargs(_tree(code), "t.py"), [])


@unittest.skipIf(_ft is None, "未安装 flet")
class FletKwargTest(unittest.TestCase):
    def test_catches_bad_control_kwarg(self):
        code = "import flet as ft\nft.ProgressRing(stroke=2.5)\n"
        found = lint_ui.check_flet_kwargs(_tree(code), "t.py", _ft)
        self.assertEqual(len(found), 1)
        self.assertIn("stroke", found[0])

    def test_accepts_valid_kwarg(self):
        code = "import flet as ft\nft.ProgressRing(stroke_width=2.5)\n"
        self.assertEqual(lint_ui.check_flet_kwargs(_tree(code), "t.py", _ft), [])


@unittest.skipIf(_ft is None, "未安装 flet")
class FletConstantTest(unittest.TestCase):
    def test_catches_missing_icon(self):
        code = "import flet as ft\nft.Icons.PLAN\n"
        found = lint_ui.check_flet_constants(_tree(code), "t.py", _ft)
        self.assertEqual(len(found), 1)
        self.assertIn("PLAN", found[0])

    def test_accepts_existing_icon(self):
        code = "import flet as ft\nft.Icons.SCHEMA\n"
        self.assertEqual(lint_ui.check_flet_constants(_tree(code), "t.py", _ft), [])

    def test_catches_missing_color(self):
        code = "import flet as ft\nft.Colors.NOT_A_REAL_COLOR\n"
        self.assertEqual(len(lint_ui.check_flet_constants(_tree(code), "t.py", _ft)), 1)


class FletAsyncCallTest(unittest.TestCase):
    def test_catches_unscheduled_coroutine(self):
        code = ("import flet as ft\n"
                "p = ft.FilePicker()\n"
                "p.get_directory_path()\n")
        found = lint_ui.check_flet_async_calls(_tree(code), "t.py")
        self.assertEqual(len(found), 1)
        self.assertIn("get_directory_path", found[0])

    def test_allows_run_task_wrapping(self):
        code = ("import flet as ft\n"
                "page.run_task(picker.get_directory_path, dialog_title='x')\n")
        self.assertEqual(lint_ui.check_flet_async_calls(_tree(code), "t.py"), [])

    def test_allows_await(self):
        code = ("import flet as ft\n"
                "async def go():\n"
                "    await picker.pick_files()\n")
        self.assertEqual(lint_ui.check_flet_async_calls(_tree(code), "t.py"), [])

    def test_catches_unscheduled_pick_files(self):
        code = "picker.pick_files(allowed_extensions=['csv'])\n"
        self.assertEqual(len(lint_ui.check_flet_async_calls(_tree(code), "t.py")), 1)


class RealCodebaseTest(unittest.TestCase):
    def test_repo_ui_is_clean(self):
        """真代码库必须干净（这条失败说明 UI 里又有参数/常量写错了）。"""
        self.assertEqual(lint_ui.main(), 0, "src/ui 下存在 UI 静态问题")


if __name__ == "__main__":
    unittest.main()
