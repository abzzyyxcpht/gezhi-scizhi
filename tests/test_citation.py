# -*- coding: utf-8 -*-
"""格式管家单元测试：GB/T 7714 / APA 排版、缺项兜底、校对清单。"""
from __future__ import annotations

import unittest

from src.citation.gbt7714 import check_entry, format_entry

FULL = {
    "title": "The rise and potential of large language model based agents",
    "authors": ["JieXi Zhang", "Wei Chen", "X H Guo", "More Author"],
    "year": 2025,
    "venue": "Science China Information Sciences",
    "volume": "68",
    "issue": "2",
    "pages": "1-20",
    "doi": "10.1007/s11432-024-4222-0",
}


class Gbt7714Test(unittest.TestCase):
    def test_full_entry(self):
        s = format_entry(FULL, "gbt7714")
        self.assertIn("et al.", s)          # >3 作者截断
        self.assertIn("[J]", s)             # 文献类型标识
        self.assertIn(", 2025, 68(2): 1-20.", s)
        self.assertIn("DOI: 10.1007/s11432-024-4222-0.", s)

    def test_author_name_inversion(self):
        s = format_entry({"title": "T", "authors": ["John M. Smith"], "year": 2024},
                         "gbt7714")
        self.assertIn("Smith J M", s)

    def test_missing_fields_fallback(self):
        s = format_entry({}, "gbt7714")
        self.assertIn("佚名", s)
        self.assertIn("未名文献", s)
        self.assertIn("出版年不详", s)
        self.assertFalse(s.rstrip().endswith(" "))  # 无悬空空格

    def test_apa_style(self):
        s = format_entry(FULL, "apa")
        self.assertIn("(2025)", s)
        self.assertIn("*Science China Information Sciences*", s)
        self.assertIn("https://doi.org/10.1007/s11432-024-4222-0", s)

    def test_unknown_style_raises(self):
        with self.assertRaises(ValueError):
            format_entry(FULL, "mla")


class CheckEntryTest(unittest.TestCase):
    def test_complete_work_no_issues(self):
        self.assertEqual(check_entry(FULL), [])

    def test_missing_fields_listed(self):
        issues = check_entry({"title": "T"})
        self.assertIn("缺作者", issues)
        self.assertIn("缺出版年", issues)
        self.assertIn("缺 DOI", issues)


if __name__ == "__main__":
    unittest.main()
