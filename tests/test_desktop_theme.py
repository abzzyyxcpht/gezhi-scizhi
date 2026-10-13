"""Verify colors on the real desktop control tree, including inactive pages."""
from __future__ import annotations

import unittest

import flet as ft

from tests import test_desktop_navigation as navigation
from tests.test_brand_contrast import _lum, contrast


def walk(*roots):
    pending, seen = list(roots), set()
    while pending:
        control = pending.pop()
        if not isinstance(control, ft.Control) or id(control) in seen:
            continue
        seen.add(id(control))
        yield control
        for name in ("controls", "tabs", "actions", "rows", "cells", "columns",
                     "content", "leading", "trailing", "title", "subtitle", "label"):
            value = getattr(control, name, None)
            if isinstance(value, (list, tuple)):
                pending.extend(value)
            elif isinstance(value, ft.Control):
                pending.append(value)


def color_hex(value):
    return {"white": "#FFFFFF", "black": "#000000"}.get(value, value)


class DesktopThemeTest(unittest.TestCase):
    def setUp(self):
        fixture = navigation.DesktopNavigationTest("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        self.ui, self.page = fixture.ui, fixture.page
        # Categorized settings keep inactive sections outside the mounted body.
        settings_roots = [c for group in self.ui.settings_center.sections.values() for c in group]
        self.controls = list(walk(*self.ui.pages, self.ui.policy_col, *settings_roots))
        self.fields = [c for c in self.controls if isinstance(c, (ft.TextField, ft.Dropdown))]

    def button_colors(self, button, state=ft.ControlState.DEFAULT):
        def resolve(value):
            if isinstance(value, dict):
                return value.get(state, value.get(ft.ControlState.DEFAULT))
            return value

        def property_color(name):
            for style in (button.style, self.page.theme.filled_button_theme.style):
                color = resolve(getattr(style, name, None))
                if color is not None:
                    return color_hex(color)
            # The standard Material disabled treatment is used when the app
            # supplies no override for that state; the explicit overrides above
            # are what can accidentally force enabled colors onto disabled UI.
            if state == ft.ControlState.DISABLED:
                return None
            scheme = self.page.theme.color_scheme
            return color_hex(scheme.primary if name == "bgcolor" else scheme.on_primary)

        return property_color("color"), property_color("bgcolor")

    def test_all_forms_including_results_tabs_use_readable_light_inputs(self):
        self.assertEqual(self.page.theme_mode, ft.ThemeMode.LIGHT)
        self.assertIn(self.ui.cite_style_dd, self.fields,
                      "The citation dropdown inside Tabs must be included")
        self.assertIn(self.ui.tf_kw, self.fields,
                      "Collapsed advanced search fields must be included")
        self.assertIn(self.ui.set_key, self.fields,
                      "The inactive settings page must be included")
        self.assertGreaterEqual(len(self.fields), 14)
        for control in self.fields:
            with self.subTest(field=control.label or type(control).__name__):
                self.assertTrue(control.filled)
                self.assertIsNotNone(control.fill_color,
                                     "Dropdown.bgcolor does not color its input")
                self.assertGreater(_lum(color_hex(control.fill_color)), 0.8)
                self.assertGreaterEqual(contrast(color_hex(control.color),
                                                  color_hex(control.fill_color)), 4.5)
                self.assertGreaterEqual(contrast(color_hex(control.label_style.color),
                                                  color_hex(control.fill_color)), 4.5)
                self.assertGreaterEqual(contrast(color_hex(control.hint_style.color),
                                                  color_hex(control.fill_color)), 3)

    def test_input_boundaries_remain_identifiable_on_white_forms(self):
        for control in self.fields:
            with self.subTest(field=control.label or type(control).__name__):
                self.assertGreaterEqual(contrast(color_hex(control.border_color),
                                                  color_hex(control.fill_color)), 3,
                                        "An input on the same white surface needs a visible boundary")
                self.assertGreaterEqual(contrast(color_hex(control.focused_border_color),
                                                  color_hex(control.fill_color)), 3)

    def test_actual_status_labels_have_readable_foreground_background_pairs(self):
        for status, color in self.ui.STATUS_COLOR.items():
            with self.subTest(status=status):
                badge = self.ui.chip(status, color)
                self.assertGreaterEqual(contrast(color_hex(badge.content.color),
                                                  color_hex(badge.bgcolor)), 4.5)

    def test_real_filled_buttons_and_approval_actions_have_readable_text(self):
        self.fixture.show_pending_a()
        controls = walk(*self.ui.pages, self.ui.policy_col, self.ui.agent_approval_card)
        buttons = [c for c in controls if isinstance(c, ft.FilledButton)]
        self.assertGreaterEqual(len(buttons), 6)
        for button in buttons:
            with self.subTest(button=str(button.content)):
                foreground, background = self.button_colors(button)
                self.assertGreaterEqual(contrast(foreground, background), 4.5)

    def test_running_agent_has_a_visually_distinct_disabled_primary_action(self):
        # Hold the worker: inspect the actual state users see during execution.
        self.page.run_thread.side_effect = lambda *_args, **_kwargs: None
        self.ui.agent_request.value = "检查现有研究记录"
        enabled_colors = self.button_colors(self.ui.agent_run_btn)
        self.ui.agent_go("run")
        self.assertTrue(self.ui.agent_run_btn.disabled)
        disabled_colors = self.button_colors(self.ui.agent_run_btn, ft.ControlState.DISABLED)
        self.assertNotEqual(disabled_colors, enabled_colors,
                            "A scalar ButtonStyle must not preserve enabled colors while disabled")

    def test_submitted_approval_has_a_visually_distinct_disabled_action(self):
        self.fixture.show_pending_a()
        button = next(c for c in walk(self.ui.agent_approval_card)
                      if isinstance(c, ft.FilledButton))
        enabled_colors = self.button_colors(button)
        self.page.run_thread.side_effect = lambda *_args, **_kwargs: None
        button.on_click(None)
        self.assertTrue(self.ui.agent_approval_card.disabled)
        disabled_colors = self.button_colors(button, ft.ControlState.DISABLED)
        self.assertNotEqual(disabled_colors, enabled_colors,
                            "Approval controls created after main() must receive disabled styling too")


if __name__ == "__main__":
    unittest.main()
