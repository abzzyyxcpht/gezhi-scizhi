"""Reasoning selection feedback uses real Flet controls and truthful settings."""
from unittest import mock

import flet as ft
import pytest

from src.model_registry import EFFORTS
from src.ui.control_tower import ReasoningControl
from src.ui.theme import palette


def make_control(value="auto", motion=True, save=None):
    page = mock.Mock()
    return ReasoningControl(page, value=value, save=save, motion=lambda: motion), page


def test_selection_uses_one_fixed_track_with_four_keyboard_buttons():
    control, _ = make_control("medium")
    assert isinstance(control.track, ft.Container)
    assert len(control.track.content.controls) == 4
    assert set(control.buttons) == {"auto", "low", "medium", "high"}
    assert all(isinstance(button, ft.TextButton) for button in control.buttons.values())
    assert len({button.width for button in control.buttons.values()}) == 1
    assert len({button.height for button in control.buttons.values()}) == 1
    assert all(button.on_click is not None for button in control.buttons.values())
    assert control.segments["medium"].bgcolor == palette.ACTIVE
    assert control.labels["medium"].weight == ft.FontWeight.W_600
    assert all(control.segments[key].bgcolor == "transparent"
               for key in control.CHOICES if key != "medium")


def test_confirmed_selection_animates_fill_inside_fixed_slots():
    save = mock.Mock(return_value={"ok": True})
    control, page = make_control("low", save=save)
    sizes = [(slot.width, slot.height) for slot in control.bar_slots]
    low = [bar.height for bar in control.bars]
    control.buttons["high"].on_click(None)
    save.assert_called_once_with({"llm_reasoning_effort": "high"})
    assert control.value == "high"
    assert [(slot.width, slot.height) for slot in control.bar_slots] == sizes == [(3, 24)] * 5
    assert max(bar.height for bar in control.bars) > max(low)
    assert all(slot.content is bar for slot, bar in zip(control.bar_slots, control.bars))
    assert all(bar.scale is None and bar.animate_scale is None for bar in control.bars)
    assert all(bar.animate.duration == 180 for bar in control.bars)
    assert all(bar.animate.curve == ft.AnimationCurve.EASE_OUT_CUBIC for bar in control.bars)
    assert control.segments["high"].animate.duration == 180
    assert "默认模型新配置" in control.note.value
    assert "深入" in control.note.value and "独立模型配置另设" in control.note.value
    page.update.assert_called_once()


def test_auto_is_a_dot_pattern_not_the_medium_waveform():
    control, _ = make_control("auto")
    assert all(bar.opacity == 0 for bar in control.bars)
    assert all(dot.opacity > 0 for dot in control.dots)
    assert all(dot.width == dot.height for dot in control.dots)
    control.refresh("medium")
    assert all(bar.opacity > 0 for bar in control.bars)
    assert all(dot.opacity == 0 for dot in control.dots)


def test_save_failure_does_not_move_selected_segment_or_waveform():
    control, _ = make_control("low", save=mock.Mock(return_value={"ok": False, "error": "保存失败"}))
    before = [(bar.height, bar.opacity) for bar in control.bars]
    control.choose("high")
    assert control.value == "low"
    assert [(bar.height, bar.opacity) for bar in control.bars] == before
    assert control.segments["low"].bgcolor == palette.ACTIVE
    assert control.segments["high"].bgcolor == "transparent"
    assert "保存失败" in control.note.value


def test_reduced_motion_removes_every_transition_while_preserving_selection():
    control, _ = make_control("low")
    control.motion = lambda: False
    control.choose("high")
    control.pulse(2, active=True)
    for glyph in control.bars + control.dots:
        assert glyph.animate is None
        assert glyph.animate_scale is None
        assert glyph.animate_opacity is None
    assert all(segment.animate is None for segment in control.segments.values())
    before = [bar.opacity for bar in control.bars]
    control.pulse(3, active=True)
    assert [bar.opacity for bar in control.bars] == before
    assert control.segments["high"].bgcolor == palette.ACTIVE


def test_external_extended_setting_is_retained_and_fits_fixed_graphic():
    control, _ = make_control("xhigh")
    assert control.value == "xhigh"
    assert "xhigh" in control.note.value
    assert all(bar.height <= slot.height <= control.glyph.height for bar, slot in zip(control.bars, control.bar_slots))
    assert all(segment.bgcolor == "transparent" for segment in control.segments.values())
    control.refresh("max")
    assert control.value == "max"
    assert all(bar.height <= slot.height <= control.glyph.height for bar, slot in zip(control.bars, control.bar_slots))


def test_busy_pulse_changes_only_glyph_opacity_and_never_saved_intensity():
    save = mock.Mock()
    control, _ = make_control("medium", save=save)
    shape = [bar.height for bar in control.bars]
    control.pulse(0, active=True)
    first = [bar.opacity for bar in control.bars]
    control.pulse(1, active=True)
    assert [bar.opacity for bar in control.bars] != first
    assert [bar.height for bar in control.bars] == shape
    assert control.value == "medium"
    save.assert_not_called()


def test_native_compatible_heights_distinguish_low_medium_high_without_scale():
    # Native 0.86.5 screenshots showed animate_scale + per-axis Scale painting
    # all five bars at 24px despite different Python scale_y values. Observe
    # actual Container dimensions here; fixed slots preserve the outer layout.
    control, _ = make_control("low")
    painted_heights = []
    for value in ("low", "medium", "high"):
        control.refresh(value)
        painted_heights.append([bar.height for bar in control.bars])
        assert all(bar.scale is None and bar.animate_scale is None for bar in control.bars)
        assert [(slot.width, slot.height) for slot in control.bar_slots] == [(3, 24)] * 5
        assert (control.glyph.width, control.glyph.height) == (34, 30)
    assert painted_heights == [[4, 8, 12, 8, 4], [6, 12, 18, 12, 6], [8, 16, 24, 16, 8]]


def test_unknown_external_setting_is_neutral_and_never_implies_medium():
    control, _ = make_control("future-effort")
    assert control.value == "future-effort"
    assert "未识别" in control.note.value
    assert "future-effort" in control.note.value
    assert all(bar.opacity == 0 for bar in control.bars)
    control.pulse(0, active=True)
    before = [dot.opacity for dot in control.dots]
    control.pulse(1, active=True)
    assert [dot.opacity for dot in control.dots] == before
    assert all(segment.bgcolor == "transparent" for segment in control.segments.values())


def test_theme_refresh_recolors_glyph_and_selected_segment_from_palette():
    original = palette.dark
    try:
        palette.set_dark(False)
        control, _ = make_control("high")
        palette.set_dark(True)
        control.refresh()
        assert all(bar.bgcolor == palette.ACCENT_TEXT for bar in control.bars)
        assert all(dot.bgcolor == palette.ACCENT_TEXT for dot in control.dots)
        assert control.track.bgcolor == palette.SURFACE_3
        assert control.segments["high"].bgcolor == palette.ACTIVE
    finally:
        palette.set_dark(original)


def test_complete_menu_exposes_saved_efforts_without_changing_the_setting():
    save = mock.Mock()
    control, _ = make_control("xhigh", save=save)
    assert set(control.advanced_items) == set(EFFORTS)
    assert control.advanced_items["xhigh"].checked
    assert sum(bool(item.checked) for item in control.advanced_items.values()) == 1
    assert "xhigh" in control.advanced_label.value
    save.assert_not_called()


@pytest.mark.parametrize("effort", ["off", "minimal", "xhigh", "max"])
def test_complete_menu_selection_saves_and_confirms_the_exact_effort(effort):
    save = mock.Mock(return_value={"ok": True})
    changed = mock.Mock()
    control, _ = make_control("medium", save=save)
    control.changed = changed
    control.advanced_items[effort].on_click(None)
    save.assert_called_once_with({"llm_reasoning_effort": effort})
    changed.assert_called_once_with(effort)
    assert control.value == effort
    assert control.advanced_items[effort].checked
    assert "已保存" in control.note.value
    assert effort in control.note.value
    assert "默认模型新配置" in control.note.value


def test_menu_save_failure_keeps_checked_value_and_does_not_notify_change():
    control, _ = make_control("xhigh", save=mock.Mock(return_value={"ok": False, "error": "磁盘不可写"}))
    control.changed = mock.Mock()
    control.advanced_items["off"].on_click(None)
    assert control.value == "xhigh"
    assert control.advanced_items["xhigh"].checked
    assert not control.advanced_items["off"].checked
    control.changed.assert_not_called()
    assert "磁盘不可写" in control.note.value


def test_closed_thinking_is_static_and_distinct_from_supplier_default():
    control, _ = make_control("off")
    assert all(bar.opacity == 0 for bar in control.bars)
    assert all(dot.opacity == 0 for dot in control.dots)
    assert control.off_mark.opacity == 1
    control.pulse(1, active=True)
    assert control.off_mark.opacity == 1
    control.refresh("auto")
    assert control.off_mark.opacity == 0
    assert any(dot.opacity > 0 for dot in control.dots)


def test_each_explicit_strength_has_a_distinct_static_graphic_and_use_hint():
    control, _ = make_control()
    shapes, hints = [], []
    for effort in ("minimal", "low", "medium", "high", "xhigh", "max"):
        control.refresh(effort)
        shapes.append(tuple(bar.height for bar in control.bars))
        hints.append(control.use_hint)
    assert len(set(shapes)) == len(shapes)
    assert len(set(hints)) == len(hints)


def test_invalid_effort_never_saves_or_changes_the_loaded_preference():
    save = mock.Mock()
    control, _ = make_control("medium", save=save)
    control.choose("future-effort")
    save.assert_not_called()
    assert control.value == "medium"


def test_environment_reduced_motion_keeps_confirmation_and_stops_activity(monkeypatch):
    monkeypatch.setenv("GEZHI_REDUCED_MOTION", "1")
    control, _ = make_control("low", save=mock.Mock(return_value={"ok": True}))
    control.choose("high")
    control.pulse(0, active=True)
    before = [bar.opacity for bar in control.bars]
    control.pulse(1, active=True)
    assert [bar.opacity for bar in control.bars] == before
    assert all(bar.animate is None and bar.animate_opacity is None for bar in control.bars)
    assert control.advanced.popup_animation_style.duration == ft.Duration()
    assert control.advanced.popup_animation_style.reverse_duration == ft.Duration()
    assert "已保存" in control.note.value
