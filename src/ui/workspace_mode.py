"""A persisted workbench preference; never an execution or permission switch."""
from __future__ import annotations

import flet as ft

from ..workspace_modes import MODES
from ..workspace_features import DEFAULT_SHORTCUTS
from .theme import palette as p


class WorkspaceModeControl:
    """Compact two-mode switch used by the desktop workbench.

    The buttons deliberately use the native ``TextButton`` state machine.  It
    gives mouse hover/press feedback and a real keyboard focus ring while the
    style's 220 ms interpolation keeps the active pill from snapping between
    modes.  Persistence still happens on a worker thread; the switch never
    blocks the Flet event loop while the preference is being written.
    """

    TRANSITION_MS = 220
    COMPACT_WIDTH = 154

    def __init__(self, page, *, mode='research', on_changed=None, api=None):
        if api is None:
            from ..bridge import handle
            api = handle
        self.page, self.api, self.on_changed = page, api, on_changed
        from .. import settings
        self.revision = settings.load().get('workspace_mode_revision', 0)
        self.mode = mode if mode in MODES else 'research'
        self.saving = False
        self.compact = False
        self.buttons = {}
        self.shortcut = self._shortcut()
        for key, icon in [('general', ft.Icons.TERMINAL), ('research', ft.Icons.SCIENCE_OUTLINED)]:
            self.buttons[key] = ft.TextButton(
                MODES[key]['label'], icon=icon, expand=True,
                tooltip=f"{MODES[key]['description']} · {self.shortcut} 切换",
                on_click=lambda e, value=key: self.select(value))
        # 不用 wrap=True：Flutter Wrap 不容纳 Expanded 子件（expand=True 的按钮），
        # 该组合会使整个子树布局中止、拖垮同列后续控件（2026-10-10 侧栏只渲染
        # 第一个子控件的根因）。窄宽由 resize() 的 50px 图标模式覆盖，无需换行。
        self.mode_row = ft.Row(list(self.buttons.values()), spacing=2)
        self.notice = ft.Text(self._ready_notice(), size=10, color=p.TEXT_3,
                              max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.content = ft.Column([
            ft.Container(content=self.mode_row,
                         padding=3, border_radius=8, bgcolor=p.SURFACE_3,
                         border=ft.Border.all(1, p.BORDER)),
            self.notice,
        ], spacing=5)
        self._paint()

    @staticmethod
    def _shortcut() -> str:
        from .. import settings
        overrides = settings.load().get('keyboard_shortcuts', {})
        if not isinstance(overrides, dict):
            overrides = {}
        value = overrides.get('workspace_mode', DEFAULT_SHORTCUTS['workspace_mode'])
        return value if isinstance(value, str) and value else DEFAULT_SHORTCUTS['workspace_mode']

    def _ready_notice(self) -> str:
        return f'{self.shortcut} 切换 · 用于新会话'

    def refresh_shortcuts(self) -> None:
        """Refresh visible bindings after a settings save without changing mode."""
        old_ready = self._ready_notice()
        self.shortcut = self._shortcut()
        for key, button in self.buttons.items():
            button.tooltip = f"{MODES[key]['description']} · {self.shortcut} 切换"
        if not self.saving and self.notice.value == old_ready:
            self.notice.value = self._ready_notice()

    def _paint(self):
        for key, button in self.buttons.items():
            active = key == self.mode
            button.disabled = self.saving
            button.style = ft.ButtonStyle(
                # Every state gets an explicit value so the selected and
                # unselected buttons remain legible in both themes.  The
                # translucent overlay supplies a small press flash without
                # changing the selected color's contrast.
                color={
                    ft.ControlState.DEFAULT: p.ON_ACCENT if active else p.TEXT_2,
                    ft.ControlState.HOVERED: p.ON_ACCENT if active else p.TEXT,
                    ft.ControlState.PRESSED: p.ON_ACCENT if active else p.TEXT,
                    ft.ControlState.FOCUSED: p.ON_ACCENT if active else p.TEXT,
                    ft.ControlState.DISABLED: p.TEXT_3,
                },
                icon_color={
                    ft.ControlState.DEFAULT: p.ON_ACCENT if active else p.TEXT_2,
                    ft.ControlState.HOVERED: p.ON_ACCENT if active else p.TEXT,
                    ft.ControlState.PRESSED: p.ON_ACCENT if active else p.TEXT,
                    ft.ControlState.FOCUSED: p.ON_ACCENT if active else p.TEXT,
                    ft.ControlState.DISABLED: p.TEXT_3,
                },
                bgcolor={
                    ft.ControlState.DEFAULT: p.ACCENT if active else 'transparent',
                    ft.ControlState.HOVERED: p.ACCENT if active else p.HOVER,
                    ft.ControlState.PRESSED: p.ACCENT if active else p.ACCENT_BG,
                    ft.ControlState.FOCUSED: p.ACCENT if active else p.SURFACE_3,
                    ft.ControlState.DISABLED: p.SURFACE_3,
                },
                overlay_color={
                    ft.ControlState.HOVERED: '#12FFFFFF',
                    ft.ControlState.PRESSED: '#18000000',
                    ft.ControlState.FOCUSED: '#10FFFFFF',
                },
                side={
                    ft.ControlState.DEFAULT: ft.BorderSide(1, p.ACCENT if active else p.BORDER),
                    ft.ControlState.HOVERED: ft.BorderSide(1, p.ACCENT_TEXT),
                    ft.ControlState.PRESSED: ft.BorderSide(1, p.ACCENT_TEXT),
                    ft.ControlState.FOCUSED: ft.BorderSide(2, p.ACCENT_TEXT),
                    ft.ControlState.DISABLED: ft.BorderSide(1, p.BORDER),
                },
                padding=ft.Padding(4, 7, 4, 7), icon_size=15,
                shape=ft.RoundedRectangleBorder(radius=6),
                animation_duration=self.TRANSITION_MS,
                mouse_cursor={ft.ControlState.DEFAULT: ft.MouseCursor.CLICK,
                              ft.ControlState.DISABLED: ft.MouseCursor.FORBIDDEN})

    def resize(self, width: float | int | None) -> None:
        """Fit the switch inside a narrow sidebar without clipping labels.

        At the compact breakpoint the icon remains visible and the tooltip
        carries the full label/description.  Restoring the width brings the
        labels back; the mode and button callbacks are unchanged.
        """
        available = float(width) if isinstance(width, (int, float)) else 9999
        compact = available < self.COMPACT_WIDTH
        if compact == self.compact:
            return
        self.compact = compact
        for key, button in self.buttons.items():
            button.content = '' if compact else MODES[key]['label']
            button.expand = None if compact else True
            button.width = 50 if compact else None
        self._paint()

    def sync(self, mode):
        """Apply an authoritative preference without writing it a second time."""
        if self.saving or mode not in MODES:
            return
        from .. import settings
        self.revision = settings.load().get('workspace_mode_revision', self.revision)
        self.mode = mode
        self.refresh_shortcuts()
        self.notice.value = self._ready_notice()
        self.notice.color = p.TEXT_3
        self._paint()

    def toggle(self):
        self.select('general' if self.mode == 'research' else 'research')

    def select(self, mode):
        if self.saving or mode == self.mode or mode not in MODES:
            return
        self.saving = True
        self.notice.value = '正在保存模式…'
        self.notice.color = p.TEXT_3
        self._paint()
        self.page.update()

        def work():
            try:
                result = self.api({'cmd': 'workbench_mode', 'mode': mode,
                                   'expected_revision': self.revision})
                if not result.get('ok') or result.get('mode') != mode:
                    if result.get('code') == 'stale_revision' and type(result.get('revision')) is int:
                        self.revision = result['revision']
                    raise ValueError(result.get('error') or '模式保存回执不匹配')
                self.mode = mode
                if type(result.get('revision')) is int:
                    self.revision = result['revision']
                self.refresh_shortcuts()
                self.notice.value = self._ready_notice()
                self.notice.color = p.TEXT_3
                if self.on_changed:
                    self.on_changed(mode)
            except Exception as ex:
                self.notice.value = f'模式未切换：{ex}'
                self.notice.color = p.DANGER
            finally:
                self.saving = False
                self._paint()
                self.page.update()

        self.page.run_thread(work)
