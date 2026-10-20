"""Wire-receipt tests: intent, actually-sent parameters, provider report.

The receipt must be derived from the real payload handed to httpx. These tests
build payloads through the production adapters so they cannot drift from them.
"""
from __future__ import annotations

import pytest

from src import model_protocols, reasoning_receipt as rr


def payload_for(profile, *, effort=None, style=None, max_tokens=256):
    profile = dict(profile)
    if effort is not None:
        profile["reasoning_effort"] = effort
    if style is not None:
        profile["thinking_style"] = style
    return model_protocols.build_request(profile, [{"role": "user", "content": "hi"}],
                                         0.2, max_tokens)["json"]


BASE = "http://127.0.0.1:9/v1"

OPENAI = {"protocol": "chat_completions", "model": "gpt-5", "thinking_style": "auto",
          "base_url": BASE}
DEEPSEEK = {"protocol": "chat_completions", "model": "deepseek-chat",
            "thinking_style": "deepseek_thinking", "base_url": BASE}
CLAUDE = {"protocol": "anthropic_messages", "model": "claude-sonnet-4-5",
          "thinking_style": "anthropic_budget", "thinking_budget": 4096,
          "base_url": BASE}
GEMINI = {"protocol": "gemini", "model": "gemini-3-pro", "thinking_style": "gemini_level",
          "base_url": BASE}
OLLAMA = {"protocol": "ollama", "model": "qwen3", "thinking_style": "ollama_think",
          "base_url": "http://127.0.0.1:9"}


def test_auto_sends_nothing_and_says_so():
    profile = {**OPENAI}
    receipt = rr.describe(profile, payload_for(profile, effort="auto"))
    assert receipt["state"] == "omitted"
    assert receipt["delivered"] is False
    assert receipt["wire"] == []
    assert "供应商默认" in receipt["reason"]


def test_off_is_reported_as_an_explicit_request_not_an_omission():
    profile = {**DEEPSEEK}
    payload = payload_for(profile, effort="off")
    receipt = rr.describe({**profile, "reasoning_effort": "off"}, payload)
    assert receipt["state"] == "omitted"
    assert "关闭" in receipt["reason"]
    assert isinstance(payload.get("thinking"), dict)


def test_high_effort_receipt_names_the_real_wire_key():
    profile = {**OPENAI, "reasoning_effort": "high"}
    payload = payload_for(profile)
    receipt = rr.describe(profile, payload)
    assert receipt["state"] == "sent"
    assert receipt["wire"] == [{"key": "reasoning_effort", "value": "high"}]
    assert payload["reasoning_effort"] == "high"


def test_claude_budget_is_described_as_a_budget_not_a_level():
    profile = {**CLAUDE, "reasoning_effort": "medium"}
    payload = payload_for(profile, max_tokens=256)
    receipt = rr.describe(profile, payload, reserved=2048,
                          total_limit=payload["max_tokens"])
    assert receipt["state"] == "budget"
    assert receipt["reserved_tokens"] == 2048
    assert "token 预算" in receipt["reason"]
    assert any(item["key"] == "thinking.budget_tokens" for item in receipt["wire"])


def test_gemini_adaptive_thinking_reports_the_nested_path():
    profile = {**GEMINI, "reasoning_effort": "high"}
    payload = payload_for(profile)
    receipt = rr.describe(profile, payload)
    assert receipt["state"] == "sent"
    assert any(item["key"].startswith("generationConfig.thinkingConfig")
               for item in receipt["wire"])


def test_ollama_think_off_reports_a_disabled_flag_without_claiming_omission():
    profile = {**OLLAMA, "reasoning_effort": "off"}
    payload = payload_for(profile)
    receipt = rr.describe(profile, payload)
    assert payload["think"] is False
    assert receipt["delivered"] is True
    assert receipt["state"] == "omitted"
    assert receipt["wire"] == [{"key": "think", "value": "false"}]


def test_receipt_never_claims_a_parameter_the_payload_omits():
    """A 'sent' receipt is impossible when the payload carries no control."""
    profile = {**OPENAI, "reasoning_effort": "high"}
    payload = payload_for(profile)
    receipt = rr.describe(profile, payload)
    stripped = {key: value for key, value in payload.items()
                if key not in {"reasoning_effort", "reasoning", "thinking", "think"}}
    honest = rr.describe(profile, stripped)
    assert receipt["state"] == "sent"
    assert honest["state"] == "omitted"
    assert honest["delivered"] is False
    assert honest["wire"] == []


def test_adapter_refuses_incoherent_style_instead_of_dropping_the_parameter():
    """Silently discarding a requested effort would make any receipt a lie."""
    profile = {**OPENAI, "reasoning_effort": "high", "thinking_style": "none"}
    with pytest.raises(ValueError):
        payload_for(profile)


def test_unknown_protocol_reports_honest_absence_rather_than_guessing():
    receipt = rr.describe({"protocol": "future_protocol", "reasoning_effort": "high"},
                          {"model": "x"})
    assert receipt["state"] == "omitted"
    assert receipt["wire"] == []


def test_provider_report_is_never_inferred_from_the_request():
    receipt = rr.describe({**OPENAI, "reasoning_effort": "high"},
                          payload_for({**OPENAI, "reasoning_effort": "high"}))
    attached = rr.attach_report(receipt, {"usage_reported": True,
                                           "reasoning_reported": True,
                                           "reasoning_tokens": 640})
    assert attached["reported"] == {"usage_reported": True,
                                    "reasoning_tokens": 640, "known": True}
    silent = rr.attach_report(receipt, {"usage_reported": True,
                                        "reasoning_reported": False,
                                        "reasoning_tokens": 0})
    assert silent["reported"]["known"] is False
    assert silent["reported"]["reasoning_tokens"] is None


def test_a_provider_reported_zero_is_kept_apart_from_silence():
    """0 is a fact; 'not reported' is a different fact."""
    base = rr.describe({**OPENAI, "reasoning_effort": "low"},
                       payload_for({**OPENAI, "reasoning_effort": "low"}))
    zero = rr.attach_report(base, {"usage_reported": True, "reasoning_reported": True,
                                   "reasoning_tokens": 0})
    assert zero["reported"]["known"] is True
    assert zero["reported"]["reasoning_tokens"] == 0
    assert "0 tokens" in rr.summary(zero)


def test_missing_usage_block_is_unknown_not_zero():
    attached = rr.attach_report({}, None)
    assert attached["reported"]["known"] is False
    assert attached["settled"] is True


def test_unsupported_records_the_local_refusal_and_sends_nothing():
    receipt = rr.describe({**OPENAI, "reasoning_effort": "max"},
                          payload_for({**OPENAI, "reasoning_effort": "medium"}))
    marked = rr.mark_unsupported(receipt, "OpenAI effort 不接受 max")
    assert marked["state"] == "unsupported"
    assert marked["delivered"] is False
    assert marked["wire"] == []
    assert marked["reason"] == "OpenAI effort 不接受 max"
    assert marked["reported"]["known"] is False


def test_summary_separates_request_from_delivery_and_report():
    profile = {**OPENAI, "reasoning_effort": "high"}
    receipt = rr.attach_report(rr.describe(profile, payload_for(profile)),
                               {"usage_reported": True, "reasoning_reported": True,
                                "reasoning_tokens": 1234})
    text = rr.summary(receipt)
    assert "选择 深入" in text
    assert "reasoning_effort=high" in text
    assert "1,234" in text


def test_summary_of_missing_receipt_is_explicit():
    assert "尚无记录" in rr.summary(None)
    assert "尚无记录" in rr.summary({})


def test_summary_says_when_the_provider_stayed_silent():
    profile = {**OPENAI, "reasoning_effort": "low"}
    receipt = rr.attach_report(rr.describe(profile, payload_for(profile)), {})
    assert "供应商未回报" in rr.summary(receipt)


def test_rows_group_by_requested_effort_in_documented_order():
    records = [
        {"requested": "high", "state": "sent", "reported": {"known": True, "reasoning_tokens": 100}},
        {"requested": "high", "state": "sent", "reported": {"known": False}},
        {"requested": "auto", "state": "omitted", "reported": {"known": False}},
        {"requested": "low", "state": "budget", "reported": {"known": True, "reasoning_tokens": 20}},
    ]
    rows = rr.rows({"receipts": records})
    assert [row["requested"] for row in rows] == ["auto", "low", "high"]
    high = next(row for row in rows if row["requested"] == "high")
    assert (high["calls"], high["sent"], high["reported"]) == (2, 2, 1)
    assert high["reasoning_tokens"] == 100


def test_rows_ignore_malformed_records():
    assert rr.rows({"receipts": [None, "x", 3, {"requested": "low", "state": "sent"}]}) == [
        {"requested": "low", "label": "轻量", "calls": 1, "sent": 1, "omitted": 0,
         "reported": 0, "silent": 1, "reasoning_tokens": 0}]


def test_labels_cover_documented_efforts_and_unknown_values():
    for effort in ("auto", "off", "minimal", "low", "medium", "high", "xhigh", "max"):
        assert rr.label(effort) != effort or effort in {"off"}
    assert rr.label("future") == "future"


@pytest.mark.parametrize("protocol,model,style", [
    ("chat_completions", "gpt-5", "auto"),
    ("responses", "gpt-5", "auto"),
    ("anthropic_messages", "claude-sonnet-4-5", "anthropic_budget"),
    ("gemini", "gemini-3-pro", "gemini_level"),
    ("ollama", "qwen3", "ollama_think"),
])
def test_every_adapter_produces_a_truthful_receipt(protocol, model, style):
    profile = {"protocol": protocol, "model": model, "thinking_style": style,
               "reasoning_effort": "low", "thinking_budget": 4096,
               "base_url": "http://127.0.0.1:9"}
    payload = payload_for(profile, max_tokens=128)
    receipt = rr.describe(profile, payload, reserved=0,
                          total_limit=payload.get("max_tokens"))
    assert receipt["state"] in {"sent", "budget", "omitted"}
    if receipt["state"] == "sent":
        assert receipt["wire"], "a 'sent' receipt must name what was sent"
