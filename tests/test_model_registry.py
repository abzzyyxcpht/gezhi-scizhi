from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, settings


class ModelRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(settings, "_PATH", Path(self.tmp.name) / "settings.json")
        patch.start()
        self.addCleanup(patch.stop)

    def test_legacy_route_reads_live_config_and_stage_model(self):
        from src.model_registry import resolve
        with mock.patch.object(config, "LLM_BASE_URL", "http://localhost:9000/v1"), \
                mock.patch.object(config, "LLM_MODEL", "main"), \
                mock.patch.object(config, "LLM_MODEL_FAST", "small"):
            self.assertEqual(resolve("fast")["model"], "small")
            self.assertEqual(resolve()["protocol"], "chat_completions")

    def test_separate_connections_can_route_fast_and_strong(self):
        from src import model_registry as registry
        registry.save_connection({"id": "local", "protocol": "ollama", "base_url": "http://localhost:11434"})
        registry.save_connection({"id": "remote", "protocol": "anthropic_messages", "base_url": "https://model.example", "api_key": "private-key"})
        registry.save_profile({"id": "reader", "connection_id": "local", "model": "local-reader"})
        registry.save_profile({"id": "critic", "connection_id": "remote", "model": "remote-critic"})
        registry.set_route("fast", "reader")
        registry.set_route("strong", "critic")
        self.assertEqual(registry.resolve("fast")["protocol"], "ollama")
        self.assertEqual(registry.resolve("strong")["api_key"], "private-key")
        self.assertNotIn("private-key", str(registry.describe()))
        self.assertNotIn("private-key", str(settings.public_settings()))

    def test_edit_without_key_preserves_secret_and_explicit_clear_removes_it(self):
        from src import model_registry as registry
        registry.save_connection({"id": "c", "protocol": "responses", "base_url": "https://model.example/v1", "api_key": "secret"})
        registry.save_connection({"id": "c", "label": "renamed"})
        self.assertEqual(settings.load()["model_connections"][0]["api_key"], "secret")
        registry.save_connection({"id": "c", "api_key": ""})
        self.assertEqual(settings.load()["model_connections"][0]["api_key"], "")

    def test_delete_referenced_connection_is_rejected(self):
        from src import model_registry as registry
        registry.save_connection({"id": "c", "base_url": "https://model.example/v1"})
        registry.save_profile({"id": "m", "connection_id": "c", "model": "model"})
        with self.assertRaises(ValueError):
            registry.delete_connection("c")

    def test_invalid_protocol_and_unknown_route_are_not_silently_accepted(self):
        from src import model_registry as registry
        with self.assertRaises(ValueError):
            registry.save_connection({"id": "c", "protocol": "unknown", "base_url": "https://model.example"})
        with self.assertRaises(ValueError):
            registry.set_route("strong", "missing")

    def test_url_embedded_credentials_are_rejected(self):
        from src import model_registry as registry
        with self.assertRaises(ValueError):
            registry.save_connection({"id": "c", "base_url": "https://user:secret@model.example/v1"})
