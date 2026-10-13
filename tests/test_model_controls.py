"""Model controls target the selected profile and reject obsolete previews."""
import json
from unittest import mock

import pytest

from src import model_registry, settings
from src.bridge import handle
from src.model_controls import model_control_apply, model_control_catalog


@pytest.fixture
def controls(tmp_path):
    with mock.patch.object(settings, "_PATH", tmp_path / "settings.json"):
        model_registry.save_connection({"id": "local", "protocol": "ollama", "base_url": "http://localhost:11434", "api_key": "private-secret"})
        model_registry.save_profile({"id": "reader", "connection_id": "local", "model": "research-reader", "thinking_style": "ollama_think"})
        model_registry.save_profile({"id": "critic", "connection_id": "local", "model": "research-critic", "thinking_style": "ollama_think"})
        yield


def test_catalog_is_read_only_and_contains_no_endpoint_or_key(controls):
    before = settings._PATH.read_bytes()
    catalog = model_control_catalog()
    assert catalog["stage"] == "planning"
    assert {row["id"] for row in catalog["items"]} == {"legacy", "reader", "critic"}
    assert "private-secret" not in json.dumps(catalog)
    assert "http://localhost" not in json.dumps(catalog)
    assert settings._PATH.read_bytes() == before


def test_apply_routes_and_changes_only_the_target_profile(controls):
    preview = model_control_catalog()
    saved = model_control_apply("planning", "reader", preview["revision"], effort="high")
    assert saved["active_id"] == "reader"
    assert model_registry.resolve("planning")["reasoning_effort"] == "high"
    assert next(p for p in settings.load()["model_profiles"] if p["id"] == "critic")["reasoning_effort"] == "auto"
    assert settings.load()["llm_reasoning_effort"] == "auto"
    assert saved["revision"] != preview["revision"]


def test_obsolete_catalog_does_not_overwrite_changed_route(controls):
    preview = model_control_catalog()
    model_registry.set_route("planning", "critic")
    with pytest.raises(ValueError, match="已变化"):
        model_control_apply("planning", "reader", preview["revision"])
    assert model_registry.resolve("planning")["profile_id"] == "critic"


@pytest.mark.parametrize("stage,profile,effort", [("other", "reader", None), ("planning", "absent", None), ("planning", "reader", "unsupported")])
def test_invalid_selection_never_writes(controls, stage, profile, effort):
    preview = model_control_catalog()
    before = settings._PATH.read_bytes()
    with pytest.raises(ValueError):
        model_control_apply(stage, profile, preview["revision"], effort=effort)
    assert settings._PATH.read_bytes() == before


def test_unknown_model_has_explanations_for_unavailable_efforts(controls):
    model_registry.save_profile({"id": "unknown", "connection_id": "legacy", "model": "unrecognized-model", "thinking_style": "auto"})
    row = next(row for row in model_control_catalog()["items"] if row["id"] == "unknown")
    assert next(e for e in row["efforts"] if e["value"] == "auto")["enabled"]
    assert all(e["reason"] for e in row["efforts"] if not e["enabled"])
    assert "适配器" in row["capability_note"]


def test_rejected_effort_never_saves_route_or_profile(controls):
    model_registry.save_profile({"id": "unknown", "connection_id": "legacy", "model": "unrecognized-model", "thinking_style": "auto"})
    preview = model_control_catalog()
    before = settings._PATH.read_bytes()
    with pytest.raises(ValueError):
        model_control_apply("planning", "unknown", preview["revision"], effort="high")
    assert settings._PATH.read_bytes() == before


def test_bridge_preserves_catalog_identity_and_failed_save(controls):
    preview = handle({"cmd": "model_control_catalog", "stage": "planning", "request_id": "catalog-1"})
    assert preview["ok"] and preview["request_id"] == "catalog-1"
    result = handle({"cmd": "model_control_apply", "stage": "planning", "profile_id": "reader", "expected_revision": preview["catalog"]["revision"]})
    assert result["ok"] and result["catalog"]["active_id"] == "reader"
    failed = handle({"cmd": "model_control_apply", "stage": "planning", "profile_id": "critic", "expected_revision": "obsolete"})
    assert not failed["ok"]


def test_catalog_retry_does_not_pair_old_route_with_new_revision(controls):
    original = model_registry.describe
    changed = False
    def racing_describe():
        nonlocal changed
        old = original()
        if not changed:
            changed = True
            model_registry.set_route("planning", "critic")
        return old
    with mock.patch.object(model_registry, "describe", side_effect=racing_describe):
        preview = model_control_catalog()
    assert preview["active_id"] == "critic"
    assert preview["revision"] == model_control_catalog()["revision"]
