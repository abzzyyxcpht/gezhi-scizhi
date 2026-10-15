"""Appearance edits exercise the real settings bridge and rendered controls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft

from src import settings
from src.ui.settings_center import SettingsCenter


class AppearanceControlsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(settings, '_PATH', Path(self.tmp.name) / 'settings.json')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.saved = MagicMock()
        self.panel = SettingsCenter(MagicMock(), on_saved=self.saved)
        self.panel.select('appearance')
        self.fields = self.panel.forms['appearance']

    def test_theme_sidebar_and_motion_changes_save_and_notify_immediately(self):
        for key, value, event in [('theme_mode', 'light', 'on_select'),
                                  ('sidebar_mode', 'overlay', 'on_select'),
                                  ('density', 'compact', 'on_select'),
                                  ('ui_animations', False, 'on_change'),
                                  ('inspector_visible', False, 'on_change')]:
            with self.subTest(key=key):
                control = self.fields[key]
                control.value = value
                getattr(control, event)(None)
                self.assertEqual(value, settings.load()[key])
        self.assertEqual(5, self.saved.call_count)

    def test_font_edit_previews_then_commits_once_on_enter_and_blur(self):
        field = self.fields['ui_font_size']
        original_code_size = self.panel.forms['appearance_preview']['code'].size
        field.value = '19'
        field.on_change(None)
        self.assertEqual(19, self.panel.forms['appearance_preview']['body'].size)
        self.assertEqual(original_code_size, self.panel.forms['appearance_preview']['code'].size)
        self.assertEqual(14, settings.load()['ui_font_size'])
        field.on_submit(None)
        field.on_blur(None)
        self.assertEqual(19, settings.load()['ui_font_size'])
        self.assertEqual(1, self.saved.call_count)

    def test_invalid_font_draft_keeps_previous_saved_value_and_preview(self):
        field = self.fields['code_font_size']
        before = self.panel.forms['appearance_preview']['code'].size
        for invalid in ('', 'eleven', '11', '21', '14.5'):
            with self.subTest(invalid=invalid):
                field.value = invalid
                field.on_change(None)
                field.on_submit(None)
                self.assertTrue(field.error_text)
                self.assertEqual(before, self.panel.forms['appearance_preview']['code'].size)
                self.assertEqual(14, settings.load()['code_font_size'])
        self.saved.assert_not_called()

    def test_code_and_terminal_sizes_are_independent(self):
        for key, size, preview in [('code_font_size', 18, 'code'),
                                   ('terminal_font_size', 16, 'terminal')]:
            field = self.fields[key]
            field.value = str(size)
            field.on_submit(None)
            self.assertEqual(size, settings.load()[key])
            self.assertEqual(size, self.panel.forms['appearance_preview'][preview].size)
        self.assertEqual(14, settings.load()['ui_font_size'])
        self.assertEqual(14, self.panel.forms['appearance_preview']['body'].size)

    def test_failed_save_can_be_retried_and_does_not_report_success(self):
        field = self.fields['code_font_size']
        field.value = '18'
        with patch('src.ui.settings_center.handle', return_value={'ok': False, 'error': '磁盘写入失败'}):
            field.on_submit(None)
        self.assertIn('磁盘写入失败', self.panel.message.value)
        self.assertEqual(14, settings.load()['code_font_size'])
        self.saved.assert_not_called()
        field.on_blur(None)
        self.assertEqual(18, settings.load()['code_font_size'])
        self.saved.assert_called_once()

    def test_display_preview_tracks_line_numbers_and_wrapping(self):
        for key in ('code_show_line_numbers', 'code_wrap_long_lines'):
            self.fields[key].value = False
            self.fields[key].on_change(None)
            self.assertFalse(settings.load()[key])
        preview = self.panel.forms['appearance_preview']
        self.assertFalse(preview['line_numbers'].visible)
        self.assertTrue(preview['code'].no_wrap)

    def test_setting_rows_stack_on_narrow_screen_without_changing_draft(self):
        self.fields['ui_font_size'].value = '18'
        self.panel.resize(1000)
        row = self.panel.appearance_rows['ui_font_size']
        self.assertIsInstance(row.content, ft.Row)
        self.panel.resize(480)
        self.assertIsInstance(row.content, ft.Column)
        self.assertEqual('18', self.fields['ui_font_size'].value)
        self.assertLessEqual(self.fields['ui_font_size'].width, 480)

    def test_setting_search_finds_font_and_line_number_terms(self):
        for query in ('字号', 'font', '行号', 'sidebar'):
            self.panel.search_input.value = query
            self.panel._on_search()
            self.assertIn('appearance', self.panel._filtered_keys, query)

    def test_syntax_previews_show_both_selected_themes_and_resize_together(self):
        self.panel.resize(1000)
        light = self.panel.light_code_preview
        dark = self.panel.dark_code_preview
        self.assertNotEqual(light.bgcolor, dark.bgcolor)
        before = light.code_lines[0].spans[0].style.color
        self.fields['code_light_theme'].value = 'atom-one-light'
        self.fields['code_light_theme'].on_select(None)
        self.assertNotEqual(before, light.code_lines[0].spans[0].style.color)
        self.assertIsInstance(self.panel.code_preview_layout.content, ft.Row)
        self.panel.resize(480)
        self.assertIsInstance(self.panel.code_preview_layout.content, ft.Column)

    def test_compact_spacing_survives_window_resize(self):
        self.fields['density'].value = 'compact'
        self.fields['density'].on_select(None)
        self.panel.resize(1000)
        self.assertEqual(19.2, self.panel.appearance_rows['ui_font_size'].content.spacing)
        self.panel.resize(480)
        self.assertEqual(6.4, self.panel.appearance_rows['ui_font_size'].content.spacing)

    def test_explicit_theme_selection_uses_current_persisted_value(self):
        self.assertEqual('dark', self.fields['theme_mode'].value)
        settings.save({'theme_mode': 'light'})  # Another renderer changed it.
        self.fields['theme_mode'].on_select(None)
        self.assertEqual('dark', settings.load()['theme_mode'])
        self.saved.assert_called_once()

    def test_sync_updates_clean_controls_without_destroying_drafts_or_errors(self):
        size = self.fields['ui_font_size']
        size.value = '19'
        size.on_change(None)
        invalid = self.fields['code_font_size']
        invalid.value = '1x'
        invalid.on_change(None)
        error = invalid.error_text
        settings.save({'theme_mode': 'light', 'density': 'compact',
                       'ui_font_size': 16, 'code_font_size': 18, 'terminal_font_size': 15})
        self.panel.sync_appearance_preferences(settings.load())
        self.assertEqual('light', self.fields['theme_mode'].value)
        self.assertEqual('compact', self.fields['density'].value)
        self.assertEqual('15', self.fields['terminal_font_size'].value)
        self.assertEqual('19', size.value)
        self.assertEqual('1x', invalid.value)
        self.assertEqual(error, invalid.error_text)
        self.assertEqual(19, self.panel.forms['appearance_preview']['body'].size)
        self.saved.assert_not_called()

    def test_apply_only_submits_local_edits_and_preserves_external_changes(self):
        self.fields['ui_font_size'].value = '19'
        self.fields['ui_font_size'].on_change(None)
        settings.save({'theme_mode': 'light', 'code_font_size': 18})
        self.panel._save_appearance_form()
        actual = settings.load()
        self.assertEqual(19, actual['ui_font_size'])
        self.assertEqual('light', actual['theme_mode'])
        self.assertEqual(18, actual['code_font_size'])
        self.assertEqual('light', self.fields['theme_mode'].value)
        self.assertEqual('18', self.fields['code_font_size'].value)

    def test_untouched_font_blur_does_not_revert_external_change(self):
        settings.save({'ui_font_size': 18})
        self.fields['ui_font_size'].on_blur(None)
        self.assertEqual(18, settings.load()['ui_font_size'])
        self.assertEqual('18', self.fields['ui_font_size'].value)
        self.saved.assert_not_called()

    def test_sync_callback_during_save_does_not_lose_other_pending_font_edit(self):
        self.panel.on_saved = lambda: self.panel.sync_appearance_preferences(settings.load())
        self.fields['ui_font_size'].value = '19'
        self.fields['ui_font_size'].on_change(None)
        self.fields['code_font_size'].value = '17'
        self.fields['code_font_size'].on_submit(None)
        self.assertEqual('19', self.fields['ui_font_size'].value)
        self.fields['ui_font_size'].on_blur(None)
        self.assertEqual(19, settings.load()['ui_font_size'])
        self.assertEqual(17, settings.load()['code_font_size'])


if __name__ == '__main__':
    unittest.main()
