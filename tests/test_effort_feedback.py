"""Tests for the exhaustive-effort confirmation stroke.

The stroke is a bounded, one-shot acknowledgement of a saved preference. It must
never imply model progress, never run when motion is reduced, and never keep a
timer alive after the view detaches.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import flet as ft

from src.ui import effort_feedback
from src.ui.effort_feedback import EffortFeedback
from src.ui.theme import palette


def make(value="medium", motion=lambda: True):
    page = mock.Mock()
    page.run_task.return_value = mock.Mock(cancel=mock.Mock())
    return EffortFeedback(page, value=value, motion=motion), page


def test_geometry_stays_inside_the_fixed_slot_for_every_documented_effort():
    preview, _ = make()
    assert (preview.content.width, preview.content.height) == (34, 30)
    for effort in ("auto", "off", "minimal", "low", "medium", "high", "xhigh", "max", "unknown"):
        preview.refresh(effort)
        for bar, slot in zip(preview.bars, preview.bar_slots):
            assert 0 <= bar.height <= slot.height
            assert bar.width == 3


def test_shape_grows_monotonically_with_the_requested_intensity():
    preview, _ = make()
    order = ("minimal", "low", "medium", "high", "xhigh", "max")
    peaks = []
    for effort in order:
        preview.refresh(effort)
        peaks.append(max(bar.height for bar in preview.bars))
    assert peaks == sorted(peaks)
    assert peaks[0] < peaks[-1]


def test_unknown_and_disabled_values_show_a_neutral_glyph_without_bars():
    preview, _ = make()
    for effort in ("unknown", "off"):
        preview.refresh(effort)
        assert all(bar.opacity == 0 for bar in preview.bars)
    preview.refresh("off")
    assert preview.off_mark.opacity == 1
    preview.refresh("auto")
    assert preview.off_mark.opacity == 0
    # 自动档位不假装成某个具体强度：柱子隐藏，改用点阵表示"由服务决定"。
    assert all(bar.opacity == 0 for bar in preview.bars)
    assert all(dot.opacity > 0 for dot in preview.dots)


def test_a_concrete_effort_shows_bars_and_hides_the_automatic_dots():
    preview, _ = make()
    preview.refresh("high")
    assert all(bar.opacity > 0 for bar in preview.bars)
    assert all(dot.opacity == 0 for dot in preview.dots)


def test_maximum_only_uses_the_local_gradient():
    preview, _ = make()
    preview.refresh("max")
    assert all(bar.gradient is not None for bar in preview.bars)
    assert all(bar.gradient.colors == [palette.ACCENT_TEXT, palette.SUCCESS] for bar in preview.bars)
    preview.refresh("high")
    assert all(bar.gradient is None for bar in preview.bars)


def test_confirmation_requires_motion_and_a_known_value():
    preview, page = make(motion=lambda: False)
    preview.refresh("max")
    assert preview.confirm() is False
    page.run_task.assert_not_called()
    preview.refresh("unknown")
    preview.motion = lambda: True
    assert preview.confirm() is False


def test_confirmation_is_bounded_by_documented_motion_timings():
    preview, page = make()
    preview.refresh("max")
    assert preview.confirm() is True
    assert preview.confirmation.opacity == 1
    assert preview.confirmation.animate_opacity.duration <= 120
    handler, *args = page.run_task.call_args.args
    assert args[-1] <= 180


def test_a_second_confirmation_supersedes_the_first_hold():
    preview, page = make()
    preview.refresh("high")
    preview.confirm()
    first, *first_args = page.run_task.call_args.args
    preview.refresh("max")
    preview.confirm()
    second, *second_args = page.run_task.call_args.args
    with mock.patch.object(effort_feedback.asyncio, "sleep", new=mock.AsyncMock()):
        asyncio.run(first(*first_args))
        assert preview.confirmation.opacity == 1, "stale hold must not clear the newer stroke"
        asyncio.run(second(*second_args))
    assert preview.confirmation.opacity == 0


def test_clear_feedback_invalidates_a_pending_hold():
    preview, page = make()
    preview.refresh("max")
    preview.confirm()
    task = page.run_task.return_value
    preview.clear_feedback()
    assert task.cancel.called
    assert preview.confirmation.opacity == 0
    assert preview.confirmation.animate_opacity is None


def test_motion_off_removes_the_stroke_without_touching_the_value():
    preview, page = make()
    preview.refresh("max")
    preview.confirm()
    preview.motion = lambda: False
    preview.pulse(0, active=True)
    assert preview.confirmation.opacity == 0
    assert preview.confirmation.animate_opacity is None
    assert preview.value == "max"


def test_reduced_motion_environment_variable_disables_the_stroke(monkeypatch):
    preview, page = make()
    preview.refresh("high")
    monkeypatch.setenv("GEZHI_REDUCED_MOTION", "1")
    assert preview.motion_enabled() is False
    assert preview.confirm() is False
    page.run_task.assert_not_called()


def test_detached_content_does_not_call_update_after_the_hold():
    preview, page = make()
    preview.refresh("high")
    preview.confirm()
    handler, *args = page.run_task.call_args.args
    with mock.patch.object(effort_feedback.asyncio, "sleep", new=mock.AsyncMock()):
        asyncio.run(handler(*args))
    page.update.assert_not_called()
    assert preview.confirmation.opacity == 0


def test_refresh_rebuilds_gradient_and_baseline_from_the_active_palette():
    original = palette.dark
    try:
        palette.set_dark(True)
        preview, _ = make()
        preview.refresh("max")
        dark = list(preview.bars[0].gradient.colors)
        palette.set_dark(False)
        preview.refresh()
        assert preview.bars[0].gradient.colors == [palette.ACCENT_TEXT, palette.SUCCESS]
        assert preview.bars[0].gradient.colors != dark
        assert preview.baseline.bgcolor == palette.BORDER_2
        assert preview.confirmation.border.top.color == palette.ACCENT_TEXT
    finally:
        palette.set_dark(original)


def test_pulse_keeps_the_fixed_slot_while_running_activity():
    preview, _ = make()
    preview.refresh("medium")
    preview.pulse(0, active=True)
    assert all(0 <= bar.height <= slot.height for bar, slot in zip(preview.bars, preview.bar_slots))
    assert (preview.content.width, preview.content.height) == (34, 30)
    assert isinstance(preview.content, ft.Stack)
