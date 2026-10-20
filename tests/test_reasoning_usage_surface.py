"""The reasoning trail has to reach the surfaces people actually read."""
from __future__ import annotations

import json

import pytest

from src import bridge, logging_util
from src import reasoning_receipt as rr


def _write(log_path, rows):
    log_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8")


def _row(requested, *, state="sent", wire=None, known=False, tokens=None):
    return {"node": "agent_step", "status": "success", "prompt_tokens": 10,
            "completion_tokens": 5, "usage_reported": True,
            "reasoning": {"requested": requested, "state": state,
                          "wire": wire or [], "delivered": state in {"sent", "budget"},
                          "settled": True,
                          "reported": {"usage_reported": True,
                                       "reasoning_tokens": tokens, "known": known}}}


@pytest.fixture()
def log(tmp_path, monkeypatch):
    path = tmp_path / "model_calls.jsonl"
    monkeypatch.setattr(logging_util, "_MODEL_LOG", path)
    return path


def test_usage_summary_carries_the_receipts(log):
    _write(log, [_row("high", wire=[{"key": "reasoning_effort", "value": "high"}],
                      known=True, tokens=800)])
    summary = logging_util.usage_summary()["reasoning"]
    assert summary["calls"] == 1
    assert summary["recorded"] == 1
    assert summary["receipts"][0]["requested"] == "high"


def test_usage_text_states_the_three_stages_and_never_guesses(log):
    _write(log, [
        _row("high", wire=[{"key": "reasoning_effort", "value": "high"}],
             known=True, tokens=800),
        _row("auto", state="omitted"),
    ])
    text = logging_util.format_usage_text()
    assert "思考强度回执：2/2" in text
    assert "深入（high）：1 次" in text
    assert "回报 800 tokens" in text
    assert "供应商默认（auto）：1 次" in text
    assert "未附带 1 次" in text


def test_usage_text_says_so_when_there_is_no_trail_at_all(log):
    text = logging_util.format_usage_text()
    assert "尚无模型调用" in text or "尚无回执记录" in text


def test_usage_text_distinguishes_silent_provider_from_reported_zero(log):
    _write(log, [_row("low", wire=[{"key": "reasoning_effort", "value": "low"}],
                      known=True, tokens=0),
                 _row("low", wire=[{"key": "reasoning_effort", "value": "low"}],
                      known=False)])
    text = logging_util.format_usage_text()
    assert "回报 0 tokens" in text
    assert "供应商未回报" in text


def test_bridge_usage_exposes_the_same_summary(log):
    _write(log, [_row("medium", wire=[{"key": "reasoning_effort", "value": "medium"}])])
    reply = bridge.handle({"cmd": "usage"})
    assert reply["ok"] is True
    assert reply["reasoning"]["recorded"] == 1
    assert "思考强度回执" in reply["text"]
    assert reply["reasoning"] == logging_util.usage_summary()["reasoning"]


def test_trail_is_not_written_when_the_caller_omits_it(log):
    logging_util.log_model(node="n", route="r", prompt_tokens=1, completion_tokens=1,
                           latency_ms=1, status="success")
    summary = logging_util.usage_summary()["reasoning"]
    assert summary["calls"] == 1
    assert summary["recorded"] == 0
    assert rr.rows(summary) == []
