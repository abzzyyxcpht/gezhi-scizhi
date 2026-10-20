"""The effort dial must show the recorded trail, not just the saved preference.

The control next to the dial therefore renders the last receipt from the usage
log. It must never invent one: an empty log shows an explicit "no record" line,
and the copy never claims the model reasoned harder.
"""
from __future__ import annotations

from unittest import mock

import pytest

from src.ui.control_tower import ReasoningControl


@pytest.fixture()
def page():
    page = mock.MagicMock()
    page.width, page.height = 1280, 900
    page.run_task.return_value = mock.Mock(cancel=mock.Mock())
    return page


def _receipt(requested="high", state="sent", wire=None, known=False, tokens=None):
    return {"requested": requested, "state": state, "style": "auto", "protocol": "chat_completions",
            "wire": wire if wire is not None else [{"key": "reasoning_effort", "value": requested}],
            "delivered": state in {"sent", "budget"}, "reserved_tokens": 0,
            "total_limit": 512, "settled": True,
            "reason": "请求已附带 reasoning_effort",
            "reported": {"usage_reported": True, "known": known,
                         "reasoning_tokens": tokens}}


def test_control_renders_the_last_receipt_without_contacting_a_model(page):
    control = ReasoningControl(page, value="high")
    control.show_trail(_receipt(known=True, tokens=1234))
    text = control.trail.value
    assert "选择 深入" in text
    assert "reasoning_effort=high" in text
    assert "1,234" in text
    assert control.trail.visible is True


def test_control_says_plainly_when_there_is_no_record(page):
    control = ReasoningControl(page, value="auto")
    control.show_trail(None)
    assert "尚无记录" in control.trail.value


def test_trail_copy_never_claims_stronger_reasoning(page):
    control = ReasoningControl(page, value="max")
    control.show_trail(_receipt(requested="max", known=False))
    text = control.trail.value
    for forbidden in ("推理更强", "思考更深", "更强推理", "质量更高"):
        assert forbidden not in text
    assert "供应商未回报" in text


def test_trail_distinguishes_provider_silence_from_a_reported_zero(page):
    control = ReasoningControl(page, value="low")
    control.show_trail(_receipt(requested="low", known=True, tokens=0))
    assert "0 tokens" in control.trail.value
    control.show_trail(_receipt(requested="low", known=False))
    assert "供应商未回报" in control.trail.value


def test_failed_request_shows_the_local_refusal_reason(page):
    control = ReasoningControl(page, value="max")
    receipt = {**_receipt(requested="max"), "state": "unsupported",
               "wire": [], "delivered": False, "reason": "OpenAI effort 不接受 max"}
    control.show_trail(receipt)
    assert "未发送" in control.trail.value
    assert "OpenAI effort 不接受 max" in control.trail.value


def test_trail_is_part_of_the_control_body_so_it_is_actually_visible(page):
    control = ReasoningControl(page, value="medium")
    rendered = []

    def walk(node):
        rendered.append(node)
        for name in ("controls", "actions"):
            for child in getattr(node, name, []) or []:
                walk(child)
        child = getattr(node, "content", None)
        if child is not None and not isinstance(child, str):
            walk(child)

    walk(control.content)
    assert control.trail in rendered
    assert any(getattr(node, "value", None) == "思考强度" for node in rendered)


def test_show_trail_does_not_trigger_a_page_repaint_while_closed(page):
    control = ReasoningControl(page, value="high")
    page.update.reset_mock()
    control.show_trail(_receipt())
    page.update.assert_not_called()
