"""Reference-layout contracts on real Flet controls, without starting models."""
from __future__ import annotations

import unittest
from unittest import mock

import flet as ft

from src import settings
from tests import test_desktop_navigation as navigation
from tests.test_desktop_theme import walk


class DesktopReferenceLayoutTests(unittest.TestCase):
    def setUp(self):
        fixture = navigation.DesktopNavigationTest("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.ui, self.page = fixture.ui, fixture.page

    def test_activity_precedes_a_docked_composer_with_reachable_actions(self):
        children = self.ui.agent_column.controls
        input_branch = next(c for c in children if self.ui.agent_request in list(walk(c)))
        self.assertGreater(children.index(input_branch), children.index(self.ui.agent_activity))
        self.assertIsNone(getattr(input_branch, "scroll", None))
        self.assertIn(self.ui.agent_run_btn, list(walk(input_branch)))
        self.assertIn(self.ui.agent_cancel_btn, list(walk(input_branch)))
        self.assertNotIn(self.ui.agent_request, list(walk(self.ui.agent_activity)))
        self.assertTrue(self.ui.agent_activity.expand)

    def test_chat_line_length_is_bounded_and_side_discussion_fits(self):
        self.assertFalse(self.ui.agent_column.expand,
                         'Expanded in the parent Row overrides the reading width')
        self.page.width = 1900
        self.ui.on_resized()
        self.assertLessEqual(self.ui.agent_column.width, 880)
        self.ui.composer.side.visible = True
        for width in (1080, 1440, 1900):
            with self.subTest(window=width):
                self.page.width = width
                self.ui.on_resized()
                self.assertLessEqual(self.ui.agent_column.width, 880)
                self.assertLessEqual(self.ui.agent_column.width + 334, self.ui.layout['page_w'])
                self.assertLessEqual(self.ui.agent_request.width, self.ui.agent_column.width - 24)

    def test_session_metadata_does_not_hide_the_empty_chat_invitation(self):
        self.ui.render_events_log([{'type': 'session_created', 'payload': {}, 'timestamp': ''}])
        self.assertTrue(self.ui.agent_empty.visible)
        self.ui.render_events_log([{'type': 'user_message', 'payload': {'text': '检查材料'}, 'timestamp': ''}])
        self.assertFalse(self.ui.agent_empty.visible)

    def test_sidebar_settings_are_outside_the_scroll_region_and_width_tracks_shell(self):
        sidebar_nodes = list(walk(self.ui.sidebar))
        scroll_regions = [c for c in sidebar_nodes if isinstance(c, ft.Column) and c.scroll]
        self.assertTrue(scroll_regions)
        self.assertFalse(self.ui.sidebar.content.scroll)
        for scroller in scroll_regions:
            self.assertNotIn(self.ui.nav_buttons[5], list(walk(scroller)))
        for width in (1080, 1440):
            self.page.width = width
            self.ui.on_resized()
            self.assertEqual(self.ui.sidebar.content.width, self.ui.layout['sidebar_w'] - 24)

    def test_settings_apply_reaches_sidebar_without_cumulative_scaling(self):
        label = self.ui.sidebar_subtitle
        original = label.size
        saved = settings.load()
        with mock.patch.object(settings, 'load', return_value={
                **saved, 'ui_font_size': 18}):
            self.ui.workspace_settings_saved()
            self.ui.workspace_settings_saved()
        self.assertEqual(label.size, original + 4)
        with mock.patch.object(settings, 'load', return_value={
                **saved, 'ui_font_size': 14}):
            self.ui.workspace_settings_saved()
        self.assertEqual(label.size, original)


if __name__ == '__main__':
    unittest.main()
