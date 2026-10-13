"""Real Flet callbacks persist profiles and change the actual LLM resolver offline."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import flet as ft

from src import config, llm, model_registry, settings


def controls(root):
    yield root
    for name in ("controls", "content", "actions"):
        children = getattr(root, name, None)
        if isinstance(children, (tuple, list)):
            for child in children:
                yield from controls(child)
        elif isinstance(children, ft.Control):
            yield from controls(children)


class ModelSettingsUiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(settings, "_PATH", Path(self.tmp.name) / "settings.json")
        patch.start()
        self.addCleanup(patch.stop)
        saved_config = {k: v for k, v in vars(config).items() if k.isupper()}
        self.addCleanup(lambda: vars(config).update(saved_config))
        self.page = mock.MagicMock(name="Page")
        self.saved = mock.Mock()
        self.no_chat = mock.patch.object(llm, "chat", side_effect=AssertionError("UI must not call a paid model"))
        self.no_models = mock.patch.object(llm, "list_models", side_effect=AssertionError("UI must not probe endpoints"))
        self.no_chat.start()
        self.no_models.start()
        self.addCleanup(self.no_chat.stop)
        self.addCleanup(self.no_models.stop)

    def panel(self):
        from src.ui.model_settings import ModelSettingsPanel
        return ModelSettingsPanel(self.page, on_saved=self.saved)

    def save_connection(self, panel, identifier="remote", key="private-key"):
        values = {"id": identifier, "label": "远程模型", "protocol": "responses",
                  "base_url": "https://unit.invalid/v1", "api_key": key, "auth_style": "bearer"}
        for name, value in values.items():
            panel.connection_fields[name].value = value
        panel.connection_save.on_click(None)

    def save_profile(self, panel, identifier="reader"):
        values = {"id": identifier, "label": "阅读模型", "connection_id": "remote", "model": "unit-reader",
                  "thinking_style": "openai_effort", "reasoning_effort": "high", "thinking_budget": "4096",
                  "max_output_tokens": "8192", "context_window": "32768"}
        for name, value in values.items():
            panel.profile_fields[name].value = value
        panel.profile_save.on_click(None)

    def test_constructor_uses_three_dropdown_views_without_tabs_or_network(self):
        panel = self.panel()
        self.assertIsInstance(panel.content, ft.Column)
        self.assertEqual({o.key for o in panel.view.options}, {"connections", "profiles", "routes"})
        self.assertFalse(any(isinstance(c, ft.Tabs) for c in controls(panel.content)))
        self.assertTrue(panel.connection_view.visible)
        panel.view.value = "profiles"
        panel.view.on_select(None)
        self.assertTrue(panel.profile_view.visible)
        self.assertFalse(panel.connection_view.visible)

    def test_control_saves_connection_profile_and_route_into_llm_resolver(self):
        panel = self.panel()
        self.save_connection(panel)
        self.save_profile(panel)
        panel.route_stage.value = "extraction"
        panel.route_stage.on_select(None)
        panel.route_profile.value = "reader"
        panel.route_save.on_click(None)
        resolved = llm._resolve_config("extraction")
        self.assertEqual(resolved["connection_id"], "remote")
        self.assertEqual(resolved["protocol"], "responses")
        self.assertEqual(resolved["model"], "unit-reader")
        self.assertEqual(resolved["api_key"], "private-key")
        self.assertEqual(resolved["reasoning_effort"], "high")
        self.assertEqual(resolved["max_output_tokens"], 8192)
        self.assertEqual(self.saved.call_count, 3)

    def test_edit_keeps_key_blank_and_preserves_secret_until_explicit_clear(self):
        panel = self.panel()
        self.save_connection(panel)
        self.assertEqual(panel.connection_fields["api_key"].value, "")
        self.assertTrue(panel.connection_fields["api_key"].password)
        self.assertTrue(panel.connection_fields["id"].read_only)
        panel.connection_fields["label"].value = "改名"
        panel.connection_save.on_click(None)
        self.assertEqual(settings.load()["model_connections"][0]["api_key"], "private-key")
        panel.clear_key.value = True
        panel.connection_save.on_click(None)
        self.assertEqual(settings.load()["model_connections"][0]["api_key"], "")
        self.assertNotIn("private-key", panel.summary.value)

    def test_whitespace_only_key_edit_preserves_saved_secret(self):
        panel = self.panel()
        self.save_connection(panel)
        panel.connection_fields["api_key"].value = "   "
        panel.connection_save.on_click(None)
        self.assertEqual(settings.load()["model_connections"][0]["api_key"], "private-key")

    def test_new_selection_releases_read_only_identifier_without_deleting_saved_item(self):
        panel = self.panel()
        self.save_connection(panel)
        panel.connection_selector.value = panel.connection_selector.options[0].key
        panel.connection_selector.on_select(None)
        self.assertFalse(panel.connection_fields["id"].read_only)
        self.assertTrue(panel.connection_delete.disabled)
        self.assertEqual(panel.connection_fields["id"].value, "")
        self.assertEqual(len(settings.load()["model_connections"]), 1)

    def test_saving_one_route_does_not_change_other_routes(self):
        panel = self.panel()
        self.save_connection(panel)
        self.save_profile(panel)
        model_registry.set_route("strong", "reader")
        model_registry.set_route("review", "reader")
        panel.refresh()
        panel.route_stage.value = "fast"
        panel.route_stage.on_select(None)
        panel.route_profile.value = "legacy"
        panel.route_save.on_click(None)
        self.assertEqual(settings.load()["model_routes"], {"strong": "reader", "review": "reader", "fast": "legacy"})

    def test_invalid_numeric_input_shows_human_error_without_partial_save(self):
        panel = self.panel()
        self.save_connection(panel)
        for name, value in {"id": "bad", "connection_id": "remote", "model": "unit", "thinking_budget": "oops"}.items():
            panel.profile_fields[name].value = value
        panel.profile_save.on_click(None)
        self.assertEqual(settings.load()["model_profiles"], [])
        dialog = self.page.show_dialog.call_args.args[0]
        self.assertIsInstance(dialog, ft.AlertDialog)
        self.assertIn("思考预算", dialog.content.value)
        self.assertIn("整数", dialog.content.value)

    def test_referenced_connection_cannot_be_deleted_and_error_is_visible(self):
        panel = self.panel()
        self.save_connection(panel)
        self.save_profile(panel)
        panel.connection_delete.on_click(None)
        self.assertEqual(len(settings.load()["model_connections"]), 1)
        dialog = self.page.show_dialog.call_args.args[0]
        self.assertIsInstance(dialog, ft.AlertDialog)
        self.assertIn("模型配置", dialog.content.value)

    def test_unreferenced_profile_and_connection_can_be_deleted(self):
        panel = self.panel()
        self.save_connection(panel)
        self.save_profile(panel)
        panel.profile_delete.on_click(None)
        self.assertEqual(settings.load()["model_profiles"], [])
        panel.connection_delete.on_click(None)
        self.assertEqual(settings.load()["model_connections"], [])

    def test_resize_and_refresh_keep_fields_readable_and_include_real_choices(self):
        from src.ui.theme import palette
        panel = self.panel()
        panel.resize(310)
        panel.refresh()
        fields = [c for c in controls(panel.content) if isinstance(c, (ft.TextField, ft.Dropdown))]
        self.assertGreater(len(fields), 15)
        for field in fields:
            self.assertTrue(field.filled)
            self.assertEqual(field.fill_color, palette.INPUT)
            self.assertLessEqual(field.width, 310)
        self.assertEqual({o.key for o in panel.connection_fields["protocol"].options}, set(model_registry.PROTOCOLS))
        self.assertIn("legacy", {o.key for o in panel.profile_fields["connection_id"].options})
        self.assertIn("max", {o.key for o in panel.profile_fields["reasoning_effort"].options})

    def test_unknown_connection_url_is_rejected_without_erasing_form(self):
        panel = self.panel()
        self.save_connection(panel, identifier="bad")
        panel.connection_fields["base_url"].value = "https://user:secret@unit.invalid"
        panel.connection_save.on_click(None)
        self.assertEqual(settings.load()["model_connections"][0]["base_url"], "https://unit.invalid/v1")
        self.assertEqual(panel.connection_fields["base_url"].value, "https://user:secret@unit.invalid")
        self.assertIsInstance(self.page.show_dialog.call_args.args[0], ft.AlertDialog)


if __name__ == "__main__":
    unittest.main()
