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
        super().__init__(padding=12, border_radius=theme.radius('panel'))
        self.source, self.language = str(text), str(language)
        self._scizhi_code_preview = True
        self._preview_options, self._preview_dark = options, dark
        self.rows: list[ft.Row] = []
        self.code_lines: list[ft.Text] = []
        self._copy_pending = False
        self._copy_state = 'ready'
        self._displayed_source = self.source
        self.language_label = ft.Text(max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
        self.copy_button = ft.TextButton('复制代码', icon=ft.Icons.COPY_OUTLINED,
                                        height=32, on_click=self._copy)
        self.header = ft.Row([self.language_label, self.copy_button], spacing=8)
        self.refresh(options, dark)

    @property
    def value(self):
        return self.source

    @value.setter
    def value(self, text):
        self.source = str(text)
        self.refresh()

    def _copy_feedback(self, page=None):
        labels = {'ready': '复制代码', 'copying': '复制中…', 'copied': '已复制',
                  'stale': '重新复制', 'failed': '重试复制'}
        self.copy_button.content = labels[self._copy_state]
        self.copy_button.icon = ft.Icons.CHECK if self._copy_state == 'copied' else ft.Icons.COPY_OUTLINED
        self.copy_button.disabled = self._copy_pending or not self.source
        self.copy_button.tooltip = (
            '剪贴板不可用，请重试或选中文本复制' if self._copy_state == 'failed' else
            '已复制点击时的代码；内容已更新，可以重新复制' if self._copy_state == 'stale' else
            '复制原始代码，保留缩进与换行，不包含行号')
        if page is not None:
            try:
                page.update()
            except RuntimeError:
                # A clipboard acknowledgement may arrive after the page closes.
                pass

    async def _copy(self, event):
        if self._copy_pending or not self.source:
            return
        page = getattr(event, 'page', None) or self.page
        source = self.source
        self._copy_pending, self._copy_state = True, 'copying'
        self._copy_feedback(page)
        try:
            await page.clipboard.set(source)
            self._copy_state = 'copied' if self.source == source else 'stale'
        except Exception:
            self._copy_state = 'failed'
        finally:
            self._copy_pending = False
            self._copy_feedback(page)

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
        names = {'python': 'Python', 'py': 'Python', 'js': 'JavaScript',
                 'javascript': 'JavaScript', 'diff': 'Diff', 'text': '纯文本'}
        self.language_label.value = names.get(self.language, self.language[:32] or '代码')
        self.language_label.color = comment
        self.language_label.size = theme.ui_size('sm')
        self.language_label.font_family = theme.body_font_family()
        self.copy_button.style = ft.ButtonStyle(color=fg, icon_color=fg, icon_size=14,
            text_style=ft.TextStyle(size=theme.ui_size('sm'), font_family=theme.body_font_family()),
            padding=ft.Padding(6, 4, 6, 4), shape=ft.RoundedRectangleBorder(radius=theme.radius()))
        if self._displayed_source != self.source:
            self._displayed_source = self.source
            if not self._copy_pending:
                self._copy_state = 'ready'
        self._copy_feedback()
        signature = (self.source, self.language, selected, bool(dark), size, wrap,
                     family, values['code_show_line_numbers'])
        if getattr(self, '_render_signature', None) == signature:
            return
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
        self.code_body = column if wrap else ft.Row([column], scroll=ft.ScrollMode.AUTO)
        self.content = ft.Column([self.header, self.code_body], spacing=8, tight=True)
        self._render_signature = signature
