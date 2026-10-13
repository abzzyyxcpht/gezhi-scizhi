"""Native widget styling: neutral surfaces, readable type, restrained actions.

浅色为冷白工作面与蓝色操作色；深色采用同结构的中性灰色阶。
桌面代码应经 ``palette`` 取色或直接用 brand 常量——两者在浅色下等值，
深色切换后由 :func:`repaint` 按值重映射；构建期就处于深色时，
``palette`` 直接给出暗色值。
"""
from __future__ import annotations

import flet as ft

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


def configure_typography(options: dict | None) -> None:
    """Select fonts and scale from validated appearance options; invalid input is safe."""
    global _body_choice, _code_choice, _font_scale
    options = options if isinstance(options, dict) else {}
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
    # ZCode 中性深黑 + 蓝 accent（2026-10-09 对标重构：原青墨绿改中性灰蓝）
    "BG": "#161616",         # 外壳与导航（ZCode --color-background 同款）
    "SURFACE": "#1F1F1F",    # 工作面
    "SURFACE_2": "#262626",  # 次级分组
    "SURFACE_3": "#303030",  # 表头、轻量标签
    "INPUT": "#262626",      # 输入框底
    "BORDER": "#2E2E2E",
    "BORDER_2": "#4A4A4A",
    "HOVER": "#2A2A2A",
    "ACTIVE": "#1E293B",     # 深蓝选中态（ZCode accent 深色 #001d3d 提亮一档）
    "TEXT": "#FAFAFA",
    # 值不得与任一浅色角色相同：repaint 按值重映射，#F5F5F5 曾撞上浅色
    # SURFACE_2、#A3A3A3 曾撞上浅色 BORDER_2，深色控件被错映射成同级底色。
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

_COLOR_NAMES = tuple(DARK)

# 浅色下共享同一取值的字段：SURFACE/INPUT/ON_ACCENT 同为 #FFFFFF，
# accent 的填充与文字角色分别映射，保留各自的对比度。
# #FFFFFF 为背景/填充时落到 INPUT 暗色，为文字时保持白色。
_WHITE = "#FFFFFF"


class Palette:
    """运行时配色注册表：深色切换时原地改值，已构建控件经 repaint 重映射。

    桌面构建期经 ``palette.XXX`` 取色的控件在深色启动时直接拿到暗色值；
    仍读 brand 模块常量的遗留位置在切换时由 :func:`repaint` 按值补救。
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
    """深浅切换后，把控件树上等于旧配色的颜色原地重映射到新配色。

    处理构建期经 brand/palette 取色的通用属性（Container 底、文字、图标、
    边框、输入框填充、分隔线）与 Markdown 样式表（摘要稿/综述框架/证据审计
    等阅读区在构建期定死正文色，必须按新主题重建），以及按钮各状态配色。
    """
    if palette.dark:
        bg_map = {getattr(b, n): DARK[n] for n in _COLOR_NAMES}
        bg_map[_WHITE] = DARK["INPUT"]          # 白色底 → 输入框暗色（工作面同级）
        fg_map = dict(bg_map)
        bg_map[b.ACCENT] = DARK["ACCENT"]
        fg_map[b.ACCENT_TEXT] = DARK["ACCENT_TEXT"]
        fg_map[_WHITE] = _WHITE                 # 白色文字保持白色（accent 按钮白字）
    else:
        bg_map = {DARK[n]: palette._light[n] for n in _COLOR_NAMES}
        fg_map = dict(bg_map)
        fg_map[_WHITE] = _WHITE                 # 深色 INPUT 与工作面都回白，无歧义
    bg_attrs = ("bgcolor", "fill_color", "heading_row_color")
    fg_attrs = ("color", "icon_color", "border_color", "focused_border_color",
                "divider_color", "track_color", "cursor_color", "selection_color")

    def remap_state(value, mapping):
        if isinstance(value, dict):
            return {state: remap_state(color, mapping) for state, color in value.items()}
        if isinstance(value, str):
            return mapping.get(value, value)
        return value

    pending, seen = list(roots), set()
    while pending:
        control = pending.pop()
        if not isinstance(control, ft.Control) or id(control) in seen:
            continue
        seen.add(id(control))
        if isinstance(control, ft.Markdown):
            # 样式表在构建期按当时主题生成（markdown_styles 读 palette.dark），
            # 深浅切换后必须重建，否则深色底上保留浅色正文（对比度约 1.3:1）
            control.md_style_sheet = markdown_styles()
        style = getattr(control, "style", None)
        if isinstance(style, ft.ButtonStyle):
            for attr in ("bgcolor", "overlay_color"):
                setattr(style, attr, remap_state(getattr(style, attr, None), bg_map))
            for attr in ("color", "icon_color"):
                setattr(style, attr, remap_state(getattr(style, attr, None), fg_map))
        for attr in bg_attrs:
            value = getattr(control, attr, None)
            if isinstance(value, str) and value in bg_map:
                setattr(control, attr, bg_map[value])
        for attr in fg_attrs:
            value = getattr(control, attr, None)
            if isinstance(value, str) and value in fg_map:
                setattr(control, attr, fg_map[value])
        border = getattr(control, "border", None)
        if isinstance(border, ft.Border):
            for side_name in ("left", "top", "right", "bottom"):
                side = getattr(border, side_name, None)
                if isinstance(side, ft.BorderSide) and side.color in bg_map:
                    side.color = bg_map[side.color]
        for style_name in ("label_style", "hint_style"):
            style = getattr(control, style_name, None)
            if isinstance(style, ft.TextStyle) and isinstance(style.color, str) \
                    and style.color in fg_map:
                style.color = fg_map[style.color]
        for name in ("controls", "tabs", "actions", "items", "rows", "cells", "columns"):
            value = getattr(control, name, None)
            if isinstance(value, (list, tuple)):
                pending.extend(value)
        for name in ("content", "leading", "trailing", "title", "subtitle", "label"):
            child = getattr(control, name, None)
            if isinstance(child, ft.Control):
                pending.append(child)


def _c(dark: bool):
    """按主题取色：dark 时用 DARK 映射，否则用 brand 浅色常量。"""
    return (lambda name: DARK[name]) if dark else (lambda name: getattr(b, name))


def markdown_styles(dark: bool | None = None) -> ft.MarkdownStyleSheet:
    """A reading scale shared by chat, source previews and research reports."""
    if dark is None:
        dark = palette.dark
    c = _c(dark)
    body = ft.TextStyle(size=_scaled(15), height=1.6, color=c("TEXT"),
                        font_family=body_font_family(), font_family_fallback=_body_fallback())
    return ft.MarkdownStyleSheet(
        p_text_style=body, p_padding=0, block_spacing=10, list_indent=20,
        h1_text_style=ft.TextStyle(size=_scaled(28), weight=ft.FontWeight.W_600, color=c("TEXT"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        h2_text_style=ft.TextStyle(size=_scaled(21), weight=ft.FontWeight.W_600, color=c("TEXT"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        h3_text_style=ft.TextStyle(size=_scaled(17), weight=ft.FontWeight.W_600, color=c("TEXT"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        h1_padding=ft.Padding(0,4,0,6), h2_padding=ft.Padding(0,8,0,2),
        h3_padding=ft.Padding(0,6,0,2),
        a_text_style=ft.TextStyle(color=c("ACCENT_TEXT"), decoration=ft.TextDecoration.UNDERLINE,
                                  font_family=body_font_family(), font_family_fallback=_body_fallback()),
        list_bullet_text_style=body,
        blockquote_text_style=ft.TextStyle(size=_scaled(14.5), height=1.6, color=c("TEXT_2"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        blockquote_padding=ft.Padding(12,8,12,8),
        blockquote_decoration=ft.BoxDecoration(bgcolor=c("SURFACE_2"),
            border=ft.Border.only(left=ft.BorderSide(2, c("BORDER_2")))),
        code_text_style=ft.TextStyle(size=_scaled(13.5), color=c("TEXT"), font_family=code_font_family(), font_family_fallback=_code_fallback()),
        codeblock_padding=12, codeblock_decoration=ft.BoxDecoration(bgcolor=c("SURFACE_2"), border_radius=6),
        table_head_text_style=ft.TextStyle(size=_scaled(13.5), weight=ft.FontWeight.W_600, color=c("TEXT"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
        table_body_text_style=body, table_cells_padding=8)


def build_theme(dark: bool | None = None) -> ft.Theme:
    if dark is None:
        dark = palette.dark
    c = _c(dark)
    shape = ft.RoundedRectangleBorder(radius=6)
    text = ft.TextStyle(size=_scaled(15), height=1.6, color=c("TEXT"),
                        font_family=body_font_family(), font_family_fallback=_body_fallback())
    state_overlay = {
        ft.ControlState.HOVERED: "#12FFFFFF" if dark else "#12000000",
        ft.ControlState.PRESSED: "#18000000",
        ft.ControlState.FOCUSED: "#10FFFFFF" if dark else "#10000000",
    }
    button = ft.ButtonStyle(
        shape=shape, elevation=0, padding=ft.Padding(16, 11, 16, 11),
        text_style=ft.TextStyle(size=_scaled(15), weight=ft.FontWeight.W_500,
                                font_family=body_font_family(), font_family_fallback=_body_fallback()),
        icon_size=19, overlay_color="#10286349")
    return ft.Theme(
        font_family=body_font_family(), use_material3=True,
        color_scheme=ft.ColorScheme(
            primary=c("ACCENT"), on_primary=c("ON_ACCENT"),
            primary_container=c("ACCENT_BG"), on_primary_container=c("ACCENT_TEXT"),
            secondary=c("TEXT_2"), on_secondary=c("ON_ACCENT"),
            secondary_container=c("SURFACE_3"), on_secondary_container=c("TEXT"),
            tertiary=c("INFO"), on_tertiary=c("ON_ACCENT"),
            tertiary_container=c("INFO_BG"), on_tertiary_container=c("INFO"),
            surface=c("SURFACE"), on_surface=c("TEXT"), on_surface_variant=c("TEXT_2"),
            surface_tint="transparent", surface_bright=c("INPUT"),
            surface_dim=c("BG"), surface_container=c("SURFACE_2"),
            surface_container_low=c("SURFACE"), surface_container_lowest=c("INPUT"),
            surface_container_high=c("SURFACE_2"), surface_container_highest=c("SURFACE_3"),
            outline=c("BORDER_2"), outline_variant=c("BORDER"),
            error=c("DANGER"), on_error=c("ON_ACCENT"),
            error_container=c("DANGER_BG"), on_error_container=c("DANGER"),
            inverse_surface=c("TEXT"), on_inverse_surface=c("INPUT"),
            inverse_primary=c("ACCENT_BG"), shadow="#1023332B", scrim="#4023332B"),
        text_theme=ft.TextTheme(
            body_large=text, body_medium=text,
            body_small=ft.TextStyle(size=_scaled(13.5), color=c("TEXT_2"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
            label_large=ft.TextStyle(size=_scaled(15), color=c("TEXT"), weight=ft.FontWeight.W_500, font_family=body_font_family(), font_family_fallback=_body_fallback()),
            label_medium=ft.TextStyle(size=_scaled(14), color=c("TEXT_2"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
            label_small=ft.TextStyle(size=_scaled(12.5), color=c("TEXT_2"), font_family=body_font_family(), font_family_fallback=_body_fallback()),
            title_large=ft.TextStyle(size=_scaled(30), color=c("TEXT"), weight=ft.FontWeight.W_600, font_family=body_font_family(), font_family_fallback=_body_fallback()),
            title_medium=ft.TextStyle(size=_scaled(18), color=c("TEXT"), weight=ft.FontWeight.W_500, font_family=body_font_family(), font_family_fallback=_body_fallback()),
            title_small=ft.TextStyle(size=_scaled(15), color=c("TEXT"), weight=ft.FontWeight.W_500, font_family=body_font_family(), font_family_fallback=_body_fallback())),
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
            side=ft.BorderSide(1, c("BORDER_2")),
            padding=ft.Padding(16, 12, 16, 12), text_style=ft.TextStyle(size=15),
            icon_size=17, overlay_color="#10286349")),
        text_button_theme=ft.TextButtonTheme(style=ft.ButtonStyle(
            shape=shape, color=c("TEXT_2"), padding=ft.Padding(10, 8, 10, 8),
            text_style=ft.TextStyle(size=14), icon_size=18,
            overlay_color="#10286349")),
        dropdown_theme=ft.DropdownTheme(text_style=text, menu_style=ft.MenuStyle(
            bgcolor=c("INPUT"), elevation=2, shadow_color="#18242B30")),
        dialog_theme=ft.DialogTheme(
            bgcolor=c("INPUT"), elevation=3, shape=ft.RoundedRectangleBorder(radius=12),
            title_text_style=ft.TextStyle(size=22, weight=ft.FontWeight.W_600, color=c("TEXT")),
            content_text_style=text),
        snackbar_theme=ft.SnackBarTheme(
            bgcolor=c("TEXT"), content_text_style=ft.TextStyle(size=15, color=c("INPUT")),
            shape=shape, elevation=1),
        tab_bar_theme=ft.TabBarTheme(
            indicator_color=c("ACCENT_TEXT"), label_color=c("ACCENT_TEXT"),
            unselected_label_color=c("TEXT_2"), divider_color=c("BORDER"),
            label_text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_500),
            unselected_label_text_style=ft.TextStyle(size=15)),
        data_table_theme=ft.DataTableTheme(
            heading_row_color=c("SURFACE_3"), heading_row_height=44,
            heading_text_style=ft.TextStyle(size=13.5, color=c("TEXT_2")),
            data_text_style=text, data_row_min_height=60, data_row_max_height=84,
            divider_thickness=0.6, column_spacing=24, horizontal_margin=16),
        scrollbar_theme=ft.ScrollbarTheme(
            thickness=5, radius=4, thumb_color=c("BORDER_2"), track_visibility=False),
        divider_color=c("BORDER"), canvas_color=c("INPUT"), scaffold_bgcolor=c("SURFACE"),
        card_bgcolor=c("SURFACE_2"), hint_color=c("TEXT_3"), hover_color=c("HOVER"),
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
    """Give newly created text the same saved type scale as existing controls."""
    prop = ('size' if isinstance(control, ft.Text) else
            'text_size' if isinstance(control, (ft.TextField, ft.Dropdown)) else None)
    if prop and isinstance(getattr(control, prop, None), (int, float)):
        original = getattr(control, '_scizhi_original_size', getattr(control, prop))
        control._scizhi_original_size = original
        setattr(control, prop, _scaled(original))
    if isinstance(control, ft.Text):
        family = (control.font_family or '').casefold()
        code = any(name in family for name in ('consolas', 'cascadia', 'mono'))
        control.font_family = code_font_family() if code else body_font_family()
        control.font_family_fallback = _code_fallback() if code else _body_fallback()
    for name in ('label_style', 'hint_style', 'text_style'):
        style = getattr(control, name, None)
        if isinstance(style, ft.TextStyle):
            if isinstance(style.size, (int, float)):
                original = getattr(style, '_scizhi_original_size', style.size)
                style._scizhi_original_size = original
                style.size = _scaled(original)
            family = (style.font_family or '').casefold()
            code = any(value in family for value in ('consolas', 'cascadia', 'mono'))
            style.font_family = code_font_family() if code else body_font_family()
            style.font_family_fallback = _code_fallback() if code else _body_fallback()


def style_inputs(*roots) -> None:
    """Flet 0.86 has no InputDecorationTheme; style actual field instances."""
    pending, seen = list(roots), set()
    while pending:
        control = pending.pop()
        if control is None or id(control) in seen:
            continue
        seen.add(id(control))
        if isinstance(control, ft.Markdown) and control.md_style_sheet is None:
            control.md_style_sheet = markdown_styles()
        if isinstance(control, (ft.TextField, ft.Dropdown)):
            control.filled = True
            control.fill_color = palette.INPUT
            control.bgcolor = palette.INPUT
            control.color = palette.TEXT
            control.border_color = palette.BORDER_2
            control.focused_border_color = palette.ACCENT
            control.border_radius = 6
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
                ft.ControlState.DEFAULT: backgrounds or palette.ACCENT}
            control.style.color = dict(foregrounds) if isinstance(foregrounds, dict) else {
                ft.ControlState.DEFAULT: foregrounds or palette.ON_ACCENT}
            control.style.bgcolor[ft.ControlState.DISABLED] = palette.SURFACE_3
            control.style.color[ft.ControlState.DISABLED] = palette.TEXT_3
        for name in ("controls", "tabs", "actions", "items", "rows", "cells", "columns"):
            value = getattr(control, name, None)
            if isinstance(value, (list, tuple)):
                pending.extend(value)
        for name in ("content", "title", "subtitle", "leading", "trailing", "label"):
            child = getattr(control, name, None)
            if isinstance(child, ft.Control):
                pending.append(child)
    if palette.dark:
        # Newly opened sections may still construct labels with brand's light
        # constants; synchronize those labels as well as the actual inputs.
        repaint(*roots)
