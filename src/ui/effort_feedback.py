"""A bounded visual indicator for a selected reasoning preference."""
from __future__ import annotations

import asyncio
import os

import flet as ft

from .theme import motion_duration, palette as p


class EffortFeedback:
    """Present an effort setting without saving it or implying model progress."""

    SHAPES = {
        "minimal": (2, 4, 6, 4, 2), "low": (4, 8, 12, 8, 4),
        "medium": (6, 12, 18, 12, 6), "high": (8, 16, 24, 16, 8),
        "xhigh": (12, 20, 24, 20, 12), "max": (20, 24, 24, 24, 20),
    }

    def __init__(self, page, value="auto", motion=lambda: True):
        self.page, self.value, self.motion = page, value, motion
        self._generation, self._feedback_task = 0, None
        self.bars = [ft.Container(width=3, height=8, border_radius=3) for _ in range(5)]
        self.bar_slots = [ft.Container(content=bar, width=3, height=24,
            alignment=ft.Alignment.CENTER) for bar in self.bars]
        self.dots = [ft.Container(width=3, height=3, border_radius=3) for _ in range(5)]
        self.off_mark = ft.Icon(ft.Icons.REMOVE_CIRCLE_OUTLINED, size=18)
        self.baseline = ft.Container(left=2, bottom=0, width=30, height=1)
        self.ticks = [ft.Container(left=4 + i * 6, bottom=0, width=1,
            height=3 if i == 2 else 2) for i in range(5)]
        self.confirmation = ft.Container(left=0, top=0, width=34, height=30,
            border_radius=5, opacity=0)
        self.content = ft.Stack([
            ft.Row(items, spacing=3, height=28, alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER)
            for items in (self.bar_slots, self.dots)
        ] + [
            ft.Container(self.off_mark, width=34, height=28, alignment=ft.Alignment.CENTER),
            self.baseline, *self.ticks, self.confirmation,
        ], width=34, height=30, clip_behavior=ft.ClipBehavior.HARD_EDGE)
        self.refresh(value)

    def motion_enabled(self):
        return bool(self.motion()) and os.environ.get("GEZHI_REDUCED_MOTION", "").lower() \
            not in {"1", "true", "yes", "on"}

    def clear_feedback(self):
        """Invalidate an outstanding confirmation and remove its transient paint."""
        self._generation += 1
        if self._feedback_task is not None:
            self._feedback_task.cancel()
            self._feedback_task = None
        self.confirmation.animate_opacity = None
        self.confirmation.opacity = 0

    def refresh(self, value=None, *, confirmed=False):
        previous = self.value
        if value is not None:
            self.value = value
        self.clear_feedback()
        self.baseline.bgcolor = p.BORDER_2
        for tick in self.ticks:
            tick.bgcolor = p.BORDER_2
        self.confirmation.border = ft.Border.all(1, p.ACCENT_TEXT)
        # Gradients are rebuilt explicitly because semantic repaint only handles
        # individual color properties, not colors nested inside a gradient.
        for bar in self.bars:
            bar.gradient = ft.LinearGradient(
                begin=ft.Alignment.BOTTOM_LEFT, end=ft.Alignment.TOP_RIGHT,
                colors=[p.ACCENT_TEXT, p.SUCCESS],
            ) if self.value == "max" else None
        self.pulse(0, active=False)
        if confirmed and previous != self.value:
            self.confirm()

    def confirm(self) -> bool:
        """Mark a successful save after a draft preview, without changing its value."""
        self.clear_feedback()
        if not self.motion_enabled() or self.value not in {"auto", "off", *self.SHAPES}:
            return False
        feedback_ms = min(120, motion_duration("feedback"))
        hold_ms = min(180, motion_duration("panel"))
        if not feedback_ms or not hold_ms:
            return False
        self.confirmation.animate_opacity = ft.Animation(
            feedback_ms, ft.AnimationCurve.EASE_OUT_CUBIC)
        self.confirmation.opacity = 1
        try:
            self._feedback_task = self.page.run_task(
                self._finish_confirmation, self._generation, hold_ms)
        except RuntimeError:
            # A closing page must not make an already saved preference
            # look unsuccessful merely because its highlight cannot run.
            self.clear_feedback()
            return False
        return True

    async def _finish_confirmation(self, generation, hold_ms):
        await asyncio.sleep(hold_ms / 1000)
        if generation != self._generation:
            return
        self._feedback_task = None
        self.confirmation.opacity = 0
        if not self.motion_enabled():
            self.confirmation.animate_opacity = None
        # A closed preview may have been detached while its confirmation ran.
        if self.content.parent is not None:
            try:
                self.content.update()
            except RuntimeError:
                pass

    def pulse(self, frame: int, active: bool):
        enabled = self.motion_enabled()
        if not enabled:
            self.clear_feedback()
        panel_ms = motion_duration("panel") if enabled else 0
        transition = ft.Animation(panel_ms, ft.AnimationCurve.EASE_OUT_CUBIC) if panel_ms else None
        known = self.value in {"auto", "off", *self.SHAPES}
        automatic, off = self.value == "auto", self.value == "off"
        self.off_mark.opacity, self.off_mark.color = (1 if off else 0), p.TEXT_3
        for i, bar in enumerate(self.bars):
            # Per-axis Scale rendered incorrectly in the native 0.86.5 client.
            # Changing the inner height preserves the fixed 3x24 outer slot.
            bar.animate = bar.animate_opacity = transition
            bar.height = self.SHAPES.get(self.value, (2, 2, 2, 2, 2))[i]
            bar.bgcolor = p.ACCENT_TEXT
            intensity = (1 if (frame + i) % 5 in (1, 2) else .45) \
                if active and enabled else .85
            bar.opacity = 0 if automatic or off or not known else intensity
            dot = self.dots[i]
            dot.animate_opacity = transition
            dot.bgcolor = p.ACCENT_TEXT
            dot.opacity = .45 if not known else intensity if automatic else 0
