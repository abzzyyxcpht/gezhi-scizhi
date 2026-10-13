# -*- coding: utf-8 -*-
"""终端文本排版单元测试：显示宽度裁切与补白（中文混排对齐）。"""
from __future__ import annotations

import unittest

from src import textfmt


class WidthTest(unittest.TestCase):
    def test_ascii_and_cjk_width(self):
        self.assertEqual(textfmt.width("DOI"), 3)
        self.assertEqual(textfmt.width("摘要"), 4)        # 两个全角字 = 4 格
        self.assertEqual(textfmt.width(""), 0)
        self.assertEqual(textfmt.width(None), 0)

    def test_mixed_width(self):
        self.assertEqual(textfmt.width("LLM 智能体"), 3 + 1 + 6)


class ClipTest(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(textfmt.clip("agents", 10), "agents")

    def test_long_text_gets_ellipsis(self):
        out = textfmt.clip("a" * 50, 10)
        self.assertEqual(textfmt.width(out), 10)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(out, "a" * 9 + "…")

    def test_cjk_clip_never_exceeds_limit(self):
        out = textfmt.clip("中文题名很长的文献标题", 10)
        self.assertLessEqual(textfmt.width(out), 10)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(out, "中文题名…")               # 4 全角字 8 格 + 省略号 2 格

    def test_wide_char_not_split(self):
        """预算只剩 1 格时不能再塞一个全角字（否则溢出）。"""
        out = textfmt.clip("ab中文", 5)
        self.assertLessEqual(textfmt.width(out), 5)

    def test_limit_smaller_than_mark(self):
        self.assertEqual(textfmt.clip("anything", 0), "…")


class PadTest(unittest.TestCase):
    def test_pad_pads_by_display_width(self):
        self.assertEqual(textfmt.pad("DOI", 4), "DOI ")
        self.assertEqual(textfmt.pad("摘要", 4), "摘要")
        self.assertEqual(textfmt.width(textfmt.pad("摘要", 8)), 8)

    def test_pad_clips_when_too_long(self):
        out = textfmt.pad("a" * 30, 10)
        self.assertEqual(textfmt.width(out), 10)
        self.assertTrue(out.endswith("…"))

    def test_columns_align(self):
        """同一列的中英混排必须落在同一显示宽度上（看板/台账对齐口径）。"""
        col = [textfmt.pad(t, 12) for t in ("DOI", "摘要", "研究热词", "token")]
        self.assertEqual({textfmt.width(c) for c in col}, {12})


if __name__ == "__main__":
    unittest.main()
