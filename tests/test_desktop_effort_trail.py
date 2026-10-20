"""Desktop wiring for the effort trail: it must reflect the log, not the dial.

The reasoning dial stores a preference. The trail line must only ever show what
the model log proves was sent, and it must not re-read or re-render when the log
has not changed.
"""
from __future__ import annotations

import json

import pytest

from src import logging_util
from src.ui.control_tower import ReasoningControl


def _row(requested, *, state="sent", known=False, tokens=None):
    return {"node": "agent_step", "status": "success", "prompt_tokens": 10,
            "completion_tokens": 5, "usage_reported": True,
            "reasoning": {"requested": requested, "state": state,
                          "wire": [{"key": "reasoning_effort", "value": requested}],
                          "delivered": True, "settled": True,
                          "reported": {"usage_reported": True, "known": known,
                                       "reasoning_tokens": tokens}}}


@pytest.fixture()
def log(tmp_path, monkeypatch):
    path = tmp_path / "model_calls.jsonl"
    monkeypatch.setattr(logging_util, "_MODEL_LOG", path)
    return path


def test_trail_shows_the_logged_receipt_after_a_call(log, monkeypatch):
    from src.ui import desktop  # noqa: F401  (import cost belongs to the module test)

    control = ReasoningControl(_page(), value="high")
    log.write_text(json.dumps(_row("high", known=True, tokens=512)) + "\n", encoding="utf-8")
    receipts = logging_util.usage_summary()["reasoning"]["receipts"]
    control.show_trail(receipts[-1])
    assert "选择 深入" in control.trail.value
    assert "512" in control.trail.value


def test_empty_log_produces_an_explicit_no_record_line(log):
    control = ReasoningControl(_page(), value="high")
    receipts = logging_util.usage_summary()["reasoning"]["receipts"]
    assert receipts == []
    control.show_trail(None)
    assert "尚无记录" in control.trail.value


def test_trail_hides_provider_silence_behind_a_clear_phrase(log):
    control = ReasoningControl(_page(), value="medium")
    log.write_text(json.dumps(_row("medium")) + "\n", encoding="utf-8")
    control.show_trail(logging_util.usage_summary()["reasoning"]["receipts"][-1])
    assert "供应商未回报" in control.trail.value


def test_trail_never_reuses_the_dial_value_as_evidence(log):
    control = ReasoningControl(_page(), value="max")
    log.write_text(json.dumps(_row("low")) + "\n", encoding="utf-8")
    control.show_trail(logging_util.usage_summary()["reasoning"]["receipts"][-1])
    assert "选择 轻量" in control.trail.value
    assert "选择 最高" not in control.trail.value


def _page():
    from unittest import mock
    page = mock.MagicMock()
    page.width, page.height = 1280, 900
    page.run_task.return_value = mock.Mock(cancel=mock.Mock())
    return page
