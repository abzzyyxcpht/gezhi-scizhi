"""Shared semantic typography, surfaces and native interaction states."""
from __future__ import annotations

import flet as ft

from src.ui_preferences import DESIGN as _DESIGN, normalize
from . import brand as b

_BODY_FONTS = {
    "system": ("Segoe UI", ["Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC"]),
    "yahei": ("Microsoft YaHei UI", ["PingFang SC", "Noto Sans CJK SC"]),
}
_CODE_FONTS = {
    "cascadia": ("Cascadia Code", ["Consolas", "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC"]),
    "consolas": ("Consolas", ["Cascadia Code", "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC"]),
}
_body_choice = "system"
_code_choice = "cascadia"
_font_scale = 1.0
_canonical = False
_preferences: dict = {}


class _TypeSize(float):
    def __new__(cls, value, role='base'):
        result = super().__new__(cls, value)
        result.role = role
        return result


def _font_setting(key: str) -> float:
    spec = _DESIGN['settings'][key]
    value = _preferences.get(key, spec['default'])
    return float(value) if type(value) in (int, float) and spec['min'] <= value <= spec['max'] else float(spec['default'])


def ui_size(role: str = 'base') -> float:
    """Return a semantic size which remains identifiable after a later resize."""
    role = role.removeprefix('ui_')
    offset = _DESIGN['typography']['offsets'][role]
    return _TypeSize(_font_setting('ui_font_size') + offset, role)


def code_font_size() -> float:
    return _font_setting('code_font_size') if _canonical else _scaled(13.5)


def terminal_font_size() -> float:
    return _font_setting('terminal_font_size') if _canonical else _scaled(11)


def radius(name: str = 'control') -> int:
    return _DESIGN['radius'][name]


def motion_duration(kind: str = 'panel') -> int:
    """Milliseconds; a reduced-motion preference means no transition."""
    if _preferences.get('ui_animations', True) is False:
        return 0
    return _DESIGN['motion'][f'{kind}_ms']


def mark_typography(control: ft.Control, role: str = 'base') -> ft.Control:
    """Declare code/terminal/UI semantics before styling a dynamic control."""
    role = role.removeprefix('ui_')
    if role not in {*_DESIGN['typography']['offsets'], 'code', 'terminal'}:
        raise ValueError(f'Unknown typography role: {role}')
    control._scizhi_typography_role = role
    return control


def configure_typography(options: dict | None) -> None:
    """Select fonts and scale from validated appearance options; invalid input is safe."""
    global _body_choice, _code_choice, _font_scale, _canonical, _preferences
    options = options if isinstance(options, dict) else {}
    _canonical = any(key in options for key in _DESIGN['settings'])
    _preferences = normalize(options)
    body_choice = options.get("appearance_font_family", "system")
    code_choice = options.get("appearance_code_font", "cascadia")
    scale = options.get("appearance_font_scale", 1.0)
    _body_choice = body_choice if isinstance(body_choice, str) and body_choice in _BODY_FONTS else "system"
    _code_choice = code_choice if isinstance(code_choice, str) and code_choice in _CODE_FONTS else "cascadia"
    _font_scale = float(scale) if type(scale) in (int, float) and scale in (0.9, 1.0, 1.1, 1.2) else 1.0


def body_font_family(choice: str | None = None) -> str:
    """Resolve a preview choice without changing the saved global typography."""
    key = _body_choice if choice is None else (
        choice if isinstance(choice, str) and choice in _BODY_FONTS else 'system')
    return _BODY_FONTS[key][0]


def code_font_family(choice: str | None = None) -> str:
    key = _code_choice if choice is None else (
        choice if isinstance(choice, str) and choice in _CODE_FONTS else 'cascadia')
    return _CODE_FONTS[key][0]


def _body_fallback() -> list[str]:
    return list(_BODY_FONTS[_body_choice][1])


def _code_fallback() -> list[str]:
    return list(_CODE_FONTS[_code_choice][1])


def _scaled(size: float) -> float:
    return round(size * _font_scale, 2)

# ---- 深色配色映射（与浅色 brand 同名字段、同语义）----
DARK: dict[str, str] = {
    # 中性深色工作面与蓝色操作色。
    "BG": "#161616",         # 外壳与导航
    "SURFACE": "#1F1F1F",    # 工作面
    "SURFACE_2": "#262626",  # 次级分组
    "SURFACE_3": "#303030",  # 表头、轻量标签
    "INPUT": "#2B2B2B",      # 深色输入、卡片与浮层
    "CARD": "#2B2B2B",
    "MENU": "#2B2B2B",
    "POPOVER": "#2B2B2B",
    "SELECTED": "#363636",
    "PRIMARY": "#FFFFFF",
    "ON_PRIMARY": "#000000",
    "BORDER": "#2E2E2E",
    "BORDER_2": "#777777",  # 输入框边界与深色面保持至少 3:1
    "HOVER": "#2A2A2A",
    "ACTIVE": "#1E293B",     # 深蓝选中态
    "TEXT": "#FAFAFA",
    # 文本与面保持独立角色，重绘保留角色，不再猜测相同 hex 的用途。
    "TEXT_2": "#A8A8A8",
    # 提示文本须在最亮的次级面（SURFACE_3）上也 ≥3:1：#737373 仅 2.78，提亮一档
    "TEXT_3": "#7E7E7E",
    # 实心按钮白字 ≥4.5:1：#3B82F6 仅 3.68，回落 blue-600；暗底文字仍由
    # ACCENT_TEXT（#60A5FA，6.48:1）承担，填充与文字两级分工不变
    "ACCENT": "#2563EB",     # blue-600：白字 5.17:1
    "ACCENT_TEXT": "#60A5FA",
    "ON_ACCENT": "#FFFFFF",
    "SUCCESS": "#4ADE80",
    "WARN": "#FBBF24",
    "DANGER": "#F87171",
    "INFO": "#60A5FA",
    "ACCENT_BG": "#1E293B",
    "SUCCESS_BG": "#14291C",
    "WARN_BG": "#2B2313",
    "DANGER_BG": "#2D1717",
    "INFO_BG": "#17233A",
}
DARK = {name: b.SemanticColor(value, name) for name, value in DARK.items()}

_COLOR_NAMES = tuple(DARK)

# Only untagged legacy white strings need a background/foreground fallback.
_WHITE = "#FFFFFF"
_CHILD_COLLECTIONS = ('controls', 'tabs', 'actions', 'items', 'rows', 'cells',
                      'columns', 'destinations', 'options')
_CHILD_CONTROLS = ('content', 'leading', 'trailing', 'title', 'subtitle', 'label',
                   'icon', 'selected_icon', 'leading_icon', 'trailing_icon',
                   'selected_trailing_icon', 'selected_suffix')


class Palette:
    """运行时语义配色注册表，已构建控件经 repaint 解析至当前主题。

    桌面构建期经 ``palette.XXX`` 取色的控件在深色启动时直接拿到暗色值；
    brand 常量也携带角色，因此浅色构建的控件同样能正确切换。
    """

    def __init__(self) -> None:
        self._dark = False
        self._light = {name: getattr(b, name) for name in _COLOR_NAMES}
        self._values = dict(self._light)

    @property
    def dark(self) -> bool:
        return self._dark

    def set_dark(self, dark: bool) -> None:
        self._dark = bool(dark)
        self._values = dict(DARK if self._dark else self._light)

    def value(self, name: str) -> str:
        return self._values[name]

    def __getattr__(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name)


palette = Palette()


def repaint(*roots) -> None:
    """Resolve semantic colors to the selected theme, including dynamic controls.

    处理构建期经 brand/palette 取色的通用属性（Container 底、文字、图标、
    边框、输入框填充、分隔线）与 Markdown 样式表（摘要稿/综述框架/证据审计
    等阅读区按新主题重建），以及按钮各状态配色。裸字符串仅用于旧代码兜底。
    """
    # Only bare legacy strings need the fallback maps.  Colors obtained from
    # palette/brand retain their exact role even when multiple roles share hex.
    legacy_names = [n for n in _COLOR_NAMES if n not in
                    ('CARD', 'MENU', 'POPOVER', 'PRIMARY', 'ON_PRIMARY', 'SELECTED')]
    if palette.dark:
        bg_map = {str(getattr(b, n)): DARK[n] for n in legacy_names}
        bg_map[_WHITE] = DARK["INPUT"]          # 白色底 → 输入框暗色（工作面同级）
        fg_map = dict(bg_map)
        bg_map[b.ACCENT] = DARK["ACCENT"]
        fg_map[b.ACCENT_TEXT] = DARK["ACCENT_TEXT"]
        fg_map[_WHITE] = _WHITE                 # 白色文字保持白色（accent 按钮白字）
    else:
        bg_map = {str(DARK[n]): palette._light[n] for n in legacy_names}
        fg_map = dict(bg_map)
        fg_map[_WHITE] = _WHITE                 # 深色 INPUT 与工作面都回白，无歧义
    bg_attrs = ("bgcolor", "fill_color", "heading_row_color", "data_row_color",
                "indicator_color", "overlay_color", "hover_color", "focus_color",
                "selection_color", "track_color", "active_track_color",
                "inactive_track_color", "shadow_color")
    fg_attrs = ("color", "icon_color", "border_color", "focused_border_color",
                "divider_color", "cursor_color", "thumb_color", "active_color",
                "selected_color", "unselected_color", "label_color", "unselected_label_color")

    def remap_state(value, mapping):
        if isinstance(value, dict):
            return {state: remap_state(color, mapping) for state, color in value.items()}
        if isinstance(value, b.SemanticColor) and value.role in _COLOR_NAMES:
            return palette.value(value.role)
        if isinstance(value, ft.BorderSide):
            value.color = remap_state(value.color, mapping)
            return value
        if isinstance(value, str):
            return mapping.get(value, value)
        return value

    pending, seen = list(roots), set()
    while pending:
        control = pending.pop()
        if not isinstance(control, ft.Control) or id(control) in seen:
            continue
        seen.add(id(control))
        if getattr(control, '_scizhi_markdown_view', False):
            control.refresh_appearance(_preferences, palette.dark)
            continue
        if getattr(control, '_scizhi_code_preview', False):
            continue
        if isinstance(control, ft.Markdown):
            # 样式表在构建期按当时主题生成（markdown_styles 读 palette.dark），
            # 深浅切换后必须重建，否则深色底上保留浅色正文（对比度约 1.3:1）
            _style_markdown(control)
        style = getattr(control, "style", None)
        if isinstance(style, ft.ButtonStyle):
            for attr in ("bgcolor", "overlay_color", "shadow_color"):
                setattr(style, attr, remap_state(getattr(style, attr, None), bg_map))
            for attr in ("color", "icon_color"):
                setattr(style, attr, remap_state(getattr(style, attr, None), fg_map))
            style.side = remap_state(style.side, fg_map)
        for style_name in ('style', 'menu_style'):
            menu_style = getattr(control, style_name, None)
            if isinstance(menu_style, ft.MenuStyle):
                for attr in ('bgcolor', 'shadow_color'):
                    setattr(menu_style, attr, remap_state(getattr(menu_style, attr, None), bg_map))
                menu_style.side = remap_state(menu_style.side, fg_map)
        for attr in bg_attrs:
            value = getattr(control, attr, None)
            if isinstance(value, (str, dict)):
                setattr(control, attr, remap_state(value, bg_map))
        for attr in fg_attrs:
            value = getattr(control, attr, None)
            if isinstance(value, (str, dict)):
                setattr(control, attr, remap_state(value, fg_map))
        border = getattr(control, "border", None)
        if isinstance(border, ft.Border):
            for side_name in ("left", "top", "right", "bottom"):
                side = getattr(border, side_name, None)
                if isinstance(side, ft.BorderSide):
                    side.color = remap_state(side.color, bg_map)
        for style_name in ("label_style", "hint_style", "text_style", "style", "helper_style",
                           "error_style", "selected_label_text_style", "unselected_label_text_style"):
            style = getattr(control, style_name, None)
            if isinstance(style, ft.TextStyle):
                style.color = remap_state(style.color, fg_map)
        for name in _CHILD_COLLECTIONS:
            value = getattr(control, name, None)
            if isinstance(value, (list, tuple)):
                pending.extend(value)
        for name in _CHILD_CONTROLS:
            child = getattr(control, name, None)
            if isinstance(child, ft.Control):
                pending.append(child)


def _c(dark: bool):
    """按主题取色：dark 时用 DARK 映射，否则用 brand 浅色常量。"""
    return (lambda name: DARK[name]) if dark else (lambda name: getattr(b, name))


def _size(role: str, legacy: float) -> float:
    return ui_size(role) if _canonical else _scaled(legacy)


def markdown_code_theme(dark: bool | None = None):
    """Flet has no GitHub Dark enum; define its readable syntax palette."""
    dark = palette.dark if dark is None else dark
    name = _preferences.get('code_dark_theme' if dark else 'code_light_theme')
    if name == 'atom-one-dark':
        return ft.MarkdownCodeTheme.ATOM_ONE_DARK
    if name == 'atom-one-light':
        return ft.MarkdownCodeTheme.ATOM_ONE_LIGHT
    if not dark:
        return ft.MarkdownCodeTheme.GITHUB
    return ft.MarkdownCustomCodeTheme(
        root=ft.TextStyle(color='#E6EDF3'),
        comment=ft.TextStyle(color='#8B949E'),
        keyword=ft.TextStyle(color='#FF7B72'),
        string=ft.TextStyle(color='#A5D6FF'),
        number=ft.TextStyle(color='#79C0FF'),
        literal=ft.TextStyle(color='#79C0FF'),
        title=ft.TextStyle(color='#D2A8FF'),
        built_in=ft.TextStyle(color='#FFA657'),
        addition=ft.TextStyle(color='#7EE787'),
        deletion=ft.TextStyle(color='#FFA198'),
    )


def _style_markdown(control: ft.Markdown) -> None:
    control.md_style_sheet = markdown_styles()
    control.code_style_sheet = markdown_styles()
    control.code_theme = markdown_code_theme()


def markdown_styles(dark: bool | None = None) -> ft.MarkdownStyleSheet:
    """A reading scale shared by chat, source previews and research reports."""
    if dark is None:
        dark = palette.dark
    c = _c(dark)
    body = ft.TextStyle(size=_size('base', 15), height=1.6, color=c("TEXT"),
                        font_family=body_font_family(), font_family_fallback=_body_fallback())
    return ft.MarkdownStyleSheet(
        p_text_style=body, p_padding=0, block_spacing=10, list_indent=20,
        h1_text_style=ft.TextStyle(size=_size('xl', 28), weight=ft.FontWeight.W_600, color=c("TEXT"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        h2_text_style=ft.TextStyle(size=_size('lg', 21), weight=ft.FontWeight.W_600, color=c("TEXT"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        h3_text_style=ft.TextStyle(size=_size('base', 17), weight=ft.FontWeight.W_600, color=c("TEXT"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        h1_padding=ft.Padding(0,4,0,6), h2_padding=ft.Padding(0,8,0,2),
        h3_padding=ft.Padding(0,6,0,2),
        a_text_style=ft.TextStyle(color=c("ACCENT_TEXT"), decoration=ft.TextDecoration.UNDERLINE,
                                  font_family=body_font_family(), font_family_fallback=_body_fallback()),
        list_bullet_text_style=body,
        blockquote_text_style=ft.TextStyle(size=_size('base', 14.5), height=1.6, color=c("TEXT_2"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        blockquote_padding=ft.Padding(12,8,12,8),
        blockquote_decoration=ft.BoxDecoration(bgcolor=c("SURFACE_2"),
            border=ft.Border.only(left=ft.BorderSide(2, c("BORDER_2")))),
        code_text_style=ft.TextStyle(size=code_font_size(), color=c("TEXT"), font_family=code_font_family(), font_family_fallback=_code_fallback()),
        codeblock_padding=12, codeblock_decoration=ft.BoxDecoration(bgcolor=c("SURFACE_2"), border_radius=radius()),
        table_head_text_style=ft.TextStyle(size=_size('caption', 13.5), weight=ft.FontWeight.W_600, color=c("TEXT"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        table_body_text_style=body, table_cells_padding=8)


def build_theme(dark: bool | None = None) -> ft.Theme:
    if dark is None:
        dark = palette.dark
    c = _c(dark)
    shape = ft.RoundedRectangleBorder(radius=radius())
    text = ft.TextStyle(size=_size('base', 15), height=1.6, color=c("TEXT"),
                        font_family=body_font_family(), font_family_fallback=_body_fallback())
    def label(role='base', legacy=15, *, color=None, weight=ft.FontWeight.W_400):
        return ft.TextStyle(size=_size(role, legacy), color=color or c('TEXT'), weight=weight,
                            font_family=body_font_family(), font_family_fallback=_body_fallback())
    state_overlay = {
        ft.ControlState.HOVERED: "#12FFFFFF" if dark else "#12000000",
        ft.ControlState.PRESSED: "#18000000",
        ft.ControlState.FOCUSED: "#10FFFFFF" if dark else "#10000000",
    }
    button = ft.ButtonStyle(
        shape=shape, elevation=0, padding=ft.Padding(12, 8, 12, 8),
        bgcolor={ft.ControlState.DISABLED: c('SURFACE_3'), ft.ControlState.DEFAULT: c('PRIMARY')},
        color={ft.ControlState.DISABLED: c('TEXT_3'), ft.ControlState.DEFAULT: c('ON_PRIMARY')},
        text_style=label(weight=ft.FontWeight.W_500), icon_size=18,
        side={ft.ControlState.FOCUSED: ft.BorderSide(1.5, c('ACCENT_TEXT'))},
        overlay_color=state_overlay)
    return ft.Theme(
        font_family=body_font_family(), use_material3=True,
        visual_density=(ft.VisualDensity.COMPACT if _preferences.get('density') == 'compact'
                        else ft.VisualDensity.STANDARD),
        color_scheme=ft.ColorScheme(
            primary=c("PRIMARY"), on_primary=c("ON_PRIMARY"),
            primary_container=c("ACCENT_BG"), on_primary_container=c("ACCENT_TEXT"),
            secondary=c("TEXT_2"), on_secondary=c("ON_ACCENT"),
            secondary_container=c("SURFACE_3"), on_secondary_container=c("TEXT"),
            tertiary=c("INFO"), on_tertiary=c("ON_ACCENT"),
            tertiary_container=c("INFO_BG"), on_tertiary_container=c("INFO"),
            surface=c("SURFACE"), on_surface=c("TEXT"), on_surface_variant=c("TEXT_2"),
            surface_tint="transparent", surface_bright=c("CARD"),
            surface_dim=c("BG"), surface_container=c("SURFACE_2"),
            surface_container_low=c("SURFACE"), surface_container_lowest=c("INPUT"),
            surface_container_high=c("SURFACE_2"), surface_container_highest=c("SURFACE_3"),
            outline=c("BORDER_2"), outline_variant=c("BORDER"),
            error=c("DANGER"), on_error=c("ON_ACCENT"),
            error_container=c("DANGER_BG"), on_error_container=c("DANGER"),
            inverse_surface=c("TEXT"), on_inverse_surface=c("INPUT"),
            inverse_primary=c("ACCENT_BG"), shadow="#1023332B", scrim="#4023332B"),
        text_theme=ft.TextTheme(
            body_large=label('lg', 15), body_medium=text,
            body_small=label('caption', 13.5, color=c('TEXT_2')),
            label_large=label(weight=ft.FontWeight.W_500),
            label_medium=label('sm', 14, color=c('TEXT_2')),
            label_small=label('xs', 12.5, color=c('TEXT_2')),
            title_large=label('xl', 30, weight=ft.FontWeight.W_600),
            title_medium=label('lg', 18, weight=ft.FontWeight.W_500),
            title_small=label('base', 15, weight=ft.FontWeight.W_500)),
        switch_theme=ft.SwitchTheme(
            thumb_color={
                # Flet resolves state maps in insertion order.  Disabled must
                # precede selected so a disabled+selected switch keeps the
                # disabled affordance instead of falling through to accent.
                ft.ControlState.DISABLED: c("TEXT_3"),
                ft.ControlState.SELECTED: c("ON_ACCENT"),
                ft.ControlState.DEFAULT: c("TEXT_2"),
            },
            track_color={
                ft.ControlState.DISABLED: c("SURFACE_2"),
                ft.ControlState.SELECTED: c("ACCENT"),
                ft.ControlState.DEFAULT: c("SURFACE_3"),
            },
            track_outline_color={
                ft.ControlState.DISABLED: c("BORDER"),
                ft.ControlState.FOCUSED: c("ACCENT_TEXT"),
                ft.ControlState.DEFAULT: c("BORDER_2"),
            },
            overlay_color=state_overlay,
        ),
        icon_button_theme=ft.IconButtonTheme(style=ft.ButtonStyle(
            color={ft.ControlState.DEFAULT: c("TEXT_2"),
                   ft.ControlState.HOVERED: c("TEXT"),
                   ft.ControlState.FOCUSED: c("TEXT"),
                   ft.ControlState.DISABLED: c("TEXT_3")},
            icon_color={ft.ControlState.DEFAULT: c("TEXT_2"),
                        ft.ControlState.HOVERED: c("TEXT"),
                        ft.ControlState.FOCUSED: c("TEXT"),
                        ft.ControlState.DISABLED: c("TEXT_3")},
            bgcolor={ft.ControlState.DEFAULT: "transparent",
                     ft.ControlState.HOVERED: c("HOVER"),
                     ft.ControlState.FOCUSED: c("SURFACE_3"),
                     ft.ControlState.DISABLED: "transparent"},
            overlay_color=state_overlay,
            side={ft.ControlState.FOCUSED: ft.BorderSide(1, c("ACCENT_TEXT"))},
        )),
        filled_button_theme=ft.FilledButtonTheme(style=button),
        outlined_button_theme=ft.OutlinedButtonTheme(style=ft.ButtonStyle(
            shape=shape, elevation=0, color=c("TEXT_2"),
            side={ft.ControlState.FOCUSED: ft.BorderSide(1.5, c('ACCENT_TEXT')),
                  ft.ControlState.DEFAULT: ft.BorderSide(1, c('BORDER_2'))},
            padding=ft.Padding(12, 8, 12, 8), text_style=label(),
            icon_size=17, overlay_color=state_overlay)),
        text_button_theme=ft.TextButtonTheme(style=ft.ButtonStyle(
            shape=shape, color=c("TEXT_2"), padding=ft.Padding(10, 8, 10, 8),
            text_style=label('base', 14), icon_size=18,
            side={ft.ControlState.FOCUSED: ft.BorderSide(1.5, c('ACCENT_TEXT'))},
            overlay_color=state_overlay)),
        dropdown_theme=ft.DropdownTheme(text_style=text, menu_style=ft.MenuStyle(
            bgcolor=c("MENU"), shape=shape, side=ft.BorderSide(1, c('BORDER')),
            elevation=2, shadow_color="#18242B30")),
        popup_menu_theme=ft.PopupMenuTheme(
            color=c('MENU'), shape=shape, label_text_style=label(), elevation=2,
            icon_color=c('TEXT_2'), icon_size=16, menu_padding=4),
        card_theme=ft.CardTheme(color=c('CARD'), elevation=0,
                               shape=ft.RoundedRectangleBorder(radius=radius('panel'))),
        dialog_theme=ft.DialogTheme(
            bgcolor=c("POPOVER"), elevation=3, shape=ft.RoundedRectangleBorder(radius=radius('dialog')),
            title_text_style=label('xl', 22, weight=ft.FontWeight.W_600),
            content_text_style=text),
        snackbar_theme=ft.SnackBarTheme(
            bgcolor=c("TEXT"), content_text_style=label(color=c('INPUT')),
            shape=shape, elevation=1),
        tab_bar_theme=ft.TabBarTheme(
            indicator_color=c("ACCENT_TEXT"), label_color=c("ACCENT_TEXT"),
            unselected_label_color=c("TEXT_2"), divider_color=c("BORDER"),
            label_text_style=label(weight=ft.FontWeight.W_500),
            unselected_label_text_style=label()),
        data_table_theme=ft.DataTableTheme(
            heading_row_color=c("SURFACE_3"), heading_row_height=44,
            heading_text_style=label('caption', 13.5, color=c('TEXT_2')),
            data_text_style=text, data_row_min_height=60, data_row_max_height=84,
            divider_thickness=0.6, column_spacing=24, horizontal_margin=16),
        scrollbar_theme=ft.ScrollbarTheme(
            thickness=5, radius=4, thumb_color=c("BORDER_2"), track_visibility=False),
        divider_color=c("BORDER"), canvas_color=c("INPUT"), scaffold_bgcolor=c("SURFACE"),
        card_bgcolor=c("CARD"), hint_color=c("TEXT_3"), hover_color=c("HOVER"),
        focus_color=c('ACTIVE'),
        disabled_color=c("TEXT_3"), splash_color="transparent")


def hoverable(control: ft.Container, active_check=None) -> ft.Container:
    """给容器类按钮绑定悬停底色（MouseRegion 级反馈，与 ink 水波纹互补）。

    :param control: 目标容器（通常带 on_click）。
    :param active_check: 可选的无参回调，返回 True 表示处于选中态——
        选中时不覆盖底色，由调用方的选中着色逻辑负责。
    """
    def _on_hover(e) -> None:
        if active_check is not None and active_check():
            return
        control.bgcolor = palette.SURFACE_3 if str(getattr(e, "data", "")) == "true" else "transparent"
        control.update()
    control.on_hover = _on_hover
    return control


def style_typography(control: ft.Control) -> None:
    """Apply semantic type without changing geometry or accumulating offsets.

    Existing explicit sizes map once to the closest intended UI role; callers
    can specify ``ui_size(role)`` or ``mark_typography`` when intent is known.
    Direct callers passing only old appearance keys keep the old scale mode.
    """
    def role_for(target, original, family=''):
        marked = getattr(target, '_scizhi_typography_role', None)
        if marked:
            return marked
        if isinstance(original, _TypeSize):
            return original.role
        if any(name in (family or '').casefold() for name in ('consolas', 'cascadia', 'mono')):
            return 'code'
        if not isinstance(original, (int, float)):
            return 'base'
        if original >= 18:
            return 'xl'
        if original >= 16:
            return 'lg'
        if original >= 14:
            return 'base'
        if original >= 13:
            return 'caption'
        if original >= 11:
            return 'sm'
        return 'xs'

    def apply(target, prop, *, inherited_role=None):
        current = getattr(target, prop, None)
        original = getattr(target, '_scizhi_original_size', current)
        if not hasattr(target, '_scizhi_original_size'):
            target._scizhi_original_size = original
        family = getattr(target, 'font_family', '') or ''
        role = inherited_role or role_for(target, original, family)
        target._scizhi_typography_role = role
        if _canonical:
            size = (terminal_font_size() if role == 'terminal' else
                    code_font_size() if role == 'code' else ui_size(role))
            setattr(target, prop, size)
        elif isinstance(original, (int, float)):
            setattr(target, prop, _scaled(original))
        code = role in ('code', 'terminal')
        if hasattr(target, 'font_family'):
            target.font_family = code_font_family() if code else body_font_family()
        if hasattr(target, 'font_family_fallback'):
            target.font_family_fallback = _code_fallback() if code else _body_fallback()
        return role

    prop = ('size' if isinstance(control, ft.Text) else
            'text_size' if isinstance(control, (ft.TextField, ft.Dropdown)) else None)
    if prop:
        apply(control, prop)
    for name in ('label_style', 'hint_style', 'text_style', 'style'):
        style = getattr(control, name, None)
        if isinstance(style, ft.TextStyle):
            apply(style, 'size')
    button_style = getattr(control, 'style', None)
    if isinstance(button_style, ft.ButtonStyle) and isinstance(button_style.text_style, ft.TextStyle):
        apply(button_style.text_style, 'size')


def style_inputs(*roots) -> None:
    """Flet 0.86 has no InputDecorationTheme; style actual field instances."""
    pending, seen = list(roots), set()
    while pending:
        control = pending.pop()
        if control is None or id(control) in seen:
            continue
        seen.add(id(control))
        if getattr(control, '_scizhi_markdown_view', False):
            control.refresh_appearance(_preferences, palette.dark)
            continue
        if getattr(control, '_scizhi_code_preview', False):
            continue
        if isinstance(control, ft.Markdown):
            _style_markdown(control)
        if isinstance(control, (ft.TextField, ft.Dropdown)):
            control.filled = True
            control.fill_color = palette.INPUT
            control.bgcolor = palette.INPUT
            control.color = palette.TEXT
            control.border_color = palette.BORDER_2
            control.focused_border_color = palette.ACCENT
            control.border_radius = radius()
            control.border_width = 1
            control.focused_border_width = 1.5
            control.label_style = ft.TextStyle(size=12, color=palette.TEXT_2)
            control.hint_style = ft.TextStyle(size=13, color=palette.TEXT_3)
            if isinstance(control, ft.TextField):
                control.cursor_color = palette.ACCENT
                control.selection_color = palette.ACTIVE
                control.align_label_with_hint = bool(control.multiline)
        style_typography(control)
        if isinstance(control, ft.FilledButton) and control.style:
            # Explicit per-button styles must preserve the disabled state too.
            backgrounds = control.style.bgcolor
            foregrounds = control.style.color
            control.style.bgcolor = dict(backgrounds) if isinstance(backgrounds, dict) else {
                ft.ControlState.DEFAULT: backgrounds or palette.PRIMARY}
            control.style.color = dict(foregrounds) if isinstance(foregrounds, dict) else {
                ft.ControlState.DEFAULT: foregrounds or palette.ON_PRIMARY}
            control.style.bgcolor[ft.ControlState.DISABLED] = palette.SURFACE_3
            control.style.color[ft.ControlState.DISABLED] = palette.TEXT_3
        for name in _CHILD_COLLECTIONS:
            value = getattr(control, name, None)
            if isinstance(value, (list, tuple)):
                pending.extend(value)
        for name in _CHILD_CONTROLS:
            child = getattr(control, name, None)
            if isinstance(child, ft.Control):
                pending.append(child)
    if palette.dark:
        # Newly opened sections may still construct labels with brand's light
        # constants; synchronize those labels as well as the actual inputs.
        repaint(*roots)
