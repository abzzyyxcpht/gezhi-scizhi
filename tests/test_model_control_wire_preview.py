"""The model dialog must preview the parameter that will really be sent.

A dropdown that only repeats a label lets the user believe everything is fine
while the adapter drops or rewrites the request. The catalog therefore carries
the controls produced by the real adapter, and the dialog shows them verbatim.
"""
from __future__ import annotations

from unittest import mock

import pytest


OPENAI_HIGH = {"id": "p1", "label": "研究模型", "model": "gpt-5",
               "connection_id": "c1", "protocol": "chat_completions",
               "reasoning_effort": "high", "thinking_style": "auto",
               "configured": True}

CLAUDE = {"id": "p2", "label": "证据模型", "model": "claude-sonnet-4-5",
          "connection_id": "c2", "protocol": "anthropic_messages",
          "reasoning_effort": "medium", "thinking_style": "anthropic_budget",
          "thinking_budget": 4096, "max_output_tokens": 8192,
          "configured": True}


def _efforts(profile):
    from src.model_controls import _item
    return {row["value"]: row for row in _item(profile["id"], profile)["efforts"]}


def test_catalog_reports_the_real_wire_key_for_a_supported_level():
    rows = _efforts(OPENAI_HIGH)
    high = rows["high"]
    assert high["enabled"] is True
    assert high["state"] == "sent"
    assert high["wire"] == [{"key": "reasoning_effort", "value": "high"}]


def test_catalog_reports_an_omitted_request_for_the_provider_default():
    rows = _efforts(OPENAI_HIGH)
    auto = rows["auto"]
    assert auto["enabled"] is True
    assert auto["state"] == "omitted"
    assert auto["wire"] == []


def test_catalog_marks_locally_rejected_levels_without_a_wire_parameter():
    rows = _efforts(OPENAI_HIGH)
    rejected = rows["max"]
    assert rejected["enabled"] is False
    assert rejected["state"] == "unsupported"
    assert rejected["wire"] == []
    assert "max" in rejected["reason"]


def test_budget_style_model_reports_a_budget_not_a_level_name():
    rows = _efforts(CLAUDE)
    medium = rows["medium"]
    assert medium["enabled"] is True
    assert medium["state"] == "budget"
    assert any("budget_tokens" in item["key"] for item in medium["wire"])


@pytest.fixture()
def panel():
    from src.ui.model_controls import ModelControlPanel
    page = mock.MagicMock()
    page.width, page.height = 1280, 900
    panel = ModelControlPanel(page, api=mock.Mock(), on_saved=mock.Mock())
    panel.snapshot = {"stage": "planning", "active_id": "p1", "items": [
        {"id": "p1", "label": "研究模型", "model": "gpt-5",
         "connection_id": "c1", "protocol": "chat_completions",
         "reasoning_effort": "high", "thinking_style": "auto", "configured": True,
         "efforts": [
             {"value": "auto", "enabled": True, "reason": "", "wire": [], "state": "omitted"},
             {"value": "high", "enabled": True, "reason": "",
              "wire": [{"key": "reasoning_effort", "value": "high"}], "state": "sent"},
             {"value": "max", "enabled": False, "reason": "OpenAI effort 不接受 max",
              "wire": [], "state": "unsupported"},
         ]}]}
    panel.profile.value = "p1"
    return panel


def test_preview_names_the_parameter_that_will_be_sent(panel):
    panel.effort.value = "high"
    panel.preview_effort()
    note = panel.preview_note.value
    assert "选择预览 · 深入" in note
    assert "将发送 reasoning_effort=high" in note
    assert "尚未保存" in note


def test_preview_says_the_request_will_carry_no_thinking_parameter(panel):
    panel.effort.value = "auto"
    panel.preview_effort()
    assert "请求不附带思考参数" in panel.preview_note.value


def test_preview_explains_a_level_that_will_not_be_sent(panel):
    panel.effort.value = "max"
    panel.preview_effort()
    note = panel.preview_note.value
    assert "不会被发送" in note
    assert "OpenAI effort 不接受 max" in note


def test_preview_never_saves_and_never_contacts_the_bridge(panel):
    panel.effort.value = "high"
    panel.api.assert_not_called() if hasattr(panel, "api") else None
    panel.preview_effort()
    panel.page.run_thread.assert_not_called()


def test_stored_level_also_shows_its_wire_parameter(panel):
    panel.select_profile(update=False)
    assert "已存档位 · 深入" in panel.preview_note.value
    assert "reasoning_effort=high" in panel.preview_note.value
