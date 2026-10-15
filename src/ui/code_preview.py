"""Selectable source preview with independent typography and horizontal scrolling."""
from __future__ import annotations

import re

import flet as ft

from .. import settings
from ..ui_preferences import normalize
from . import theme

_SYNTAX = {
    'github': ('#FFFFFF', '#24292E', '#D73A49', '#032F62', '#005CC5', '#6A737D'),
    'github-dark': ('#2B2B2B', '#E6EDF3', '#FF7B72', '#A5D6FF', '#79C0FF', '#9DA7B2'),
    'atom-one-light': ('#FAFAFA', '#383A42', '#A626A4', '#397300', '#986801', '#67717F'),
    'atom-one-dark': ('#282C34', '#D0D5DF', '#C678DD', '#98C379', '#D19A66', '#A1A7B3'),
}
_LEXEMES = re.compile(r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\#.*$|//.*$|\b\d+(?:\.\d+)?\b|\b(?:def|class|return|import|from|as|if|else|elif|for|while|in|try|except|with|async|await|const|let|function|export|true|false|null|True|False|None)\b)''')


class CodePreview(ft.Container):
    def __init__(self, text: str, language: str = 'text', options: dict | None = None,
                 dark: bool | None = None):
        super().__init__(padding=12, border_radius=8)
        self.source, self.language = str(text), str(language)
        self._scizhi_code_preview = True
        self._preview_options, self._preview_dark = options, dark
        self.rows: list[ft.Row] = []
        self.code_lines: list[ft.Text] = []
        self.refresh(options, dark)

    @property
    def value(self):
        return self.source

    @value.setter
    def value(self, text):
        self.source = str(text)
        self.refresh()

    def refresh(self, options: dict | None = None, dark: bool | None = None):
        raw = options if options is not None else self._preview_options
        values = normalize(settings.load() if raw is None else raw)
        chosen_dark = dark if dark is not None else self._preview_dark
        dark = theme.palette.dark if chosen_dark is None else chosen_dark
        selected = values['code_dark_theme' if dark else 'code_light_theme']
        bg, fg, keyword, string, number, comment = _SYNTAX[selected]
        self.bgcolor = bg
        size = values['code_font_size']
        wrap = values['code_wrap_long_lines']
        family = theme.code_font_family(values.get('appearance_code_font'))
        self.rows, self.code_lines = [], []
        lines = self.source.splitlines() or ['']
        width = max(28, len(str(len(lines))) * size * .65 + 8)
        for index, line in enumerate(lines, 1):
            diff_color = (('#98C379' if dark else '#246B35') if line.startswith('+') else
                          ('#FF7B72' if dark else '#B62324') if line.startswith('-') else None)
            spans = []
            for part in _LEXEMES.split(line):
                if not part:
                    continue
                color = fg
                if self.language not in {'text', 'txt', 'log'}:
                    if part.startswith(('#', '//')):
                        color = comment
                    elif part.startswith(('"', "'")):
                        color = string
                    elif part[0].isdigit():
                        color = number
                    elif _LEXEMES.fullmatch(part):
                        color = keyword
                if self.language == 'diff' and diff_color:
                    color = diff_color
                spans.append(ft.TextSpan(part, style=ft.TextStyle(color=color)))
            label = ft.Text(str(index), size=size, width=width, color=comment,
                            font_family=family, text_align=ft.TextAlign.RIGHT,
                            visible=values['code_show_line_numbers'])
            code = ft.Text(spans=spans, size=size, color=fg, no_wrap=not wrap,
                           font_family=family,
                           font_family_fallback=['Consolas', 'Microsoft YaHei UI', 'PingFang SC', 'Noto Sans CJK SC'],
                           selectable=True, expand=wrap)
            theme.mark_typography(label, 'code')
            theme.mark_typography(code, 'code')
            self.code_lines.append(code)
            self.rows.append(ft.Row([label, code], spacing=12,
                                   vertical_alignment=ft.CrossAxisAlignment.START))
        column = ft.Column(self.rows, spacing=2, tight=True)
        self.content = column if wrap else ft.Row([column], scroll=ft.ScrollMode.AUTO)
