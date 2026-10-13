from __future__ import annotations

import tempfile
import os
import unittest
from pathlib import Path
from unittest import mock

from src import config, plugins, settings


class WorkbenchSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(settings, "_PATH", Path(self.tmp.name) / "settings.json")
        patch.start()
        self.addCleanup(patch.stop)
        for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_MODEL_FAST", "LLM_MODEL_STRONG"):
            patch = mock.patch.object(config, name, "")
            patch.start()
            self.addCleanup(patch.stop)

    def test_clearing_model_configuration_takes_effect(self):
        settings.save({"llm_base_url": "http://localhost:8080/v1", "llm_model": "model-a",
                       "llm_model_fast": "fast", "llm_api_key": "secret"})
        settings.save({"llm_base_url": "", "llm_model_fast": "", "llm_api_key": ""})
        self.assertEqual(config.LLM_BASE_URL, "")
        self.assertEqual(config.LLM_MODEL_FAST, "")
        self.assertEqual(config.LLM_API_KEY, "")

    def test_boolean_toggle_rejects_strings_before_writing(self):
        with self.assertRaises(ValueError):
            settings.save({"ui_animations": "false"})
        self.assertFalse(settings._PATH.exists())

    def test_public_settings_never_return_secret(self):
        settings.save({"llm_api_key": "my-private-token"})
        self.assertNotIn("my-private-token", str(settings.public_settings()))

    def test_startup_preserves_explicit_retrieval_environment(self):
        with mock.patch.dict(os.environ, {"GEZHI_LIT_TAU": "0.4", "GEZHI_LIT_CROSSREF": "0"}), \
                mock.patch.object(config, "LIT_SCORE_THRESHOLD", .4), \
                mock.patch.object(config, "LIT_ENABLE_CROSSREF", False):
            settings.apply_at_startup()
            self.assertEqual(config.LIT_SCORE_THRESHOLD, .4)
            self.assertFalse(config.LIT_ENABLE_CROSSREF)

    def test_unrelated_toggle_does_not_clear_active_environment_model(self):
        with mock.patch.object(config, "LLM_BASE_URL", "http://localhost:8080/v1"), \
                mock.patch.object(config, "LLM_MODEL", "env-model"):
            settings.save({"ui_animations": False})
            self.assertEqual(config.LLM_BASE_URL, "http://localhost:8080/v1")
            self.assertEqual(config.LLM_MODEL, "env-model")

    def test_disabling_plugin_removes_commands_and_actions(self):
        plugins.load(force=True)
        self.addCleanup(lambda: plugins.load(force=True))
        plugins.set_enabled("bibtex", False)
        self.assertNotIn("bib", {c["name"] for c in plugins.commands()})
        self.assertNotIn("export_bibtex", plugins.ACTIONS)
        self.assertFalse(next(p for p in plugins.summary()["names"] if p["name"] == "bibtex")["enabled"])
        plugins.set_enabled("bibtex", True)
        self.assertIn("bib", {c["name"] for c in plugins.commands()})

    def test_global_plugins_toggle_unregisters_plugin_actions(self):
        plugins.load(force=True)
        self.addCleanup(lambda: plugins.load(force=True))
        settings.save({"plugins_enabled": False})
        self.assertEqual(plugins.commands(), [])
        self.assertNotIn("export_bibtex", plugins.ACTIONS)
