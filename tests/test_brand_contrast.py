# -*- coding: utf-8 -*-
"""桌面色板对比度：阅读正文、次级信息、控件与状态都应清晰。

用 WCAG 相对亮度算对比度，把「好不好看」里可量化的部分钉住：
正文 ≥7:1、次级 ≥4.5:1、占位提示 ≥3:1、实心按钮上的字 ≥4.5:1、
着色标签 ≥4.5:1。浅色（brand）与深色（theme.DARK，默认主题）两板同 bar。
"""
from __future__ import annotations

import unittest

import flet as ft

from src.ui import brand
from src.ui.theme import DARK, palette, repaint


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    parts = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def f(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(parts[0]) + 0.7152 * f(parts[1]) + 0.0722 * f(parts[2])


def contrast(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# 两套色板统一成同名 dict：浅色取 brand 常量，深色取 theme.DARK 映射。
# 新增语义色时必须同步 DARK，否则本测试以 KeyError 失败而不是静默漏测。
PALETTES = {
    "light": {name: getattr(brand, name) for name in DARK},
    "dark": dict(DARK),
}

# 着色标签（chip）实际使用的前景：填充级 accent 在各自底色上不足 4.5:1，
# 桌面 chip() 因此改铺文字级 ACCENT_TEXT——这里把该决定钉住。
TINTED_LABELS = (
    ("ACCENT_TEXT", "ACCENT_BG"),
    ("SUCCESS", "SUCCESS_BG"), ("WARN", "WARN_BG"),
    ("DANGER", "DANGER_BG"), ("INFO", "INFO_BG"),
)


class PaletteContrastTest(unittest.TestCase):
    def _run_shared_bars(self, label: str, p: dict[str, str]) -> None:
        where = f"[{label}]"
        surfaces = tuple(p[name] for name in
                         ("BG", "SURFACE", "SURFACE_2", "SURFACE_3", "CARD", "MENU", "POPOVER", "INPUT"))
        for bg in surfaces:
            self.assertGreaterEqual(contrast(p["TEXT"], bg), 7.0, f"{where} 主文本/ {bg}")
            self.assertGreaterEqual(contrast(p["TEXT_2"], bg), 4.5, f"{where} 次级文本/ {bg}")
            self.assertGreaterEqual(contrast(p["TEXT_3"], bg), 3.0, f"{where} 提示文本/ {bg}")
        self.assertGreaterEqual(contrast(p["ON_ACCENT"], p["ACCENT"]), 4.5,
                                f"{where} 实心按钮白字")
        self.assertGreaterEqual(contrast(p["ON_PRIMARY"], p["PRIMARY"]), 4.5,
                                f"{where} 主操作按钮")
        self.assertGreaterEqual(contrast(p["TEXT"], p["SELECTED"]), 7,
                                f"{where} 选中项正文")
        self.assertGreaterEqual(contrast(p["BORDER_2"], p["INPUT"]), 3,
                                f"{where} 输入边界")
        for name in ("SUCCESS", "WARN", "DANGER", "INFO", "ACCENT_TEXT"):
            self.assertGreaterEqual(contrast(p[name], p["SURFACE"]), 4.5,
                                    f"{where} 语义文字/ {name}")
        for fg_name, bg_name in TINTED_LABELS:
            self.assertGreaterEqual(contrast(p[fg_name], p[bg_name]), 4.5,
                                    f"{where} 着色标签 {fg_name}/{bg_name}")
        for name in ("SUCCESS", "WARN", "DANGER", "INFO"):
            self.assertGreaterEqual(contrast(p["BG"], p[name]), 4.5,
                                    f"{where} 语义填充文字/ {name}")
        # 发丝描边要能看见但别抢眼：1.1:1 ~ 2.2:1（两板都不得隐形）。
        hairline = contrast(p["BORDER"], p["SURFACE"])
        self.assertGreater(hairline, 1.1, f"{where} 描边太淡看不见")
        self.assertLess(hairline, 2.2, f"{where} 描边太重抢结构")
        # 面层次序（按板指定方向）：浅色由暗到亮 SURFACE_3→SURFACE_2→BG→SURFACE；
        # 深色由暗到亮 BG→SURFACE→SURFACE_2→SURFACE_3。两板都不得出现相邻两级
        # 亮度相同（阶梯必须可感知）。
        lums = {n: _lum(p[n]) for n in ("BG", "SURFACE", "SURFACE_2", "SURFACE_3")}
        order = (("BG", "SURFACE", "SURFACE_2", "SURFACE_3") if label == "dark"
                 else ("SURFACE_3", "SURFACE_2", "BG", "SURFACE"))
        for lower, higher in zip(order, order[1:]):
            self.assertLess(lums[lower], lums[higher],
                            f"{where} 面阶梯 {lower} 应暗于 {higher}")

    def test_light_palette_bars(self):
        self._run_shared_bars("light", PALETTES["light"])

    def test_dark_palette_bars(self):
        """深色是默认主题（settings 默认 appearance_dark_mode=True），必须同 bar。"""
        self._run_shared_bars("dark", PALETTES["dark"])

    def test_palettes_cover_the_same_names(self):
        self.assertEqual(set(PALETTES["light"]), set(PALETTES["dark"]))

    def test_every_color_role_survives_collisions_and_theme_round_trips(self):
        """Equal hex values cannot substitute for semantic roles during repaint."""
        original = palette.dark
        try:
            palette.set_dark(False)
            labels = {name: ft.Text(name, color=palette.value(name)) for name in DARK}
            surfaces = {name: ft.Container(bgcolor=palette.value(name)) for name in DARK}
            for dark in (True, True, False, True, False):
                palette.set_dark(dark)
                repaint(*labels.values(), *surfaces.values())
                for name in DARK:
                    self.assertEqual(labels[name].color, palette.value(name), name)
                    self.assertEqual(surfaces[name].bgcolor, palette.value(name), name)
        finally:
            palette.set_dark(original)

    def test_accent_fill_and_text_are_separate_grades(self):
        """填充级 ACCENT 供按钮底/描边，文字级 ACCENT_TEXT 供暗底文字，两级不混用。"""
        for label, p in PALETTES.items():
            self.assertGreaterEqual(contrast(p["ON_ACCENT"], p["ACCENT"]), 4.5,
                                    f"[{label}] 填充级 accent 须托得住白字")
            self.assertGreaterEqual(contrast(p["ACCENT_TEXT"], p["SURFACE"]), 4.5,
                                    f"[{label}] 文字级 accent 须读得清")


if __name__ == "__main__":
    unittest.main()
