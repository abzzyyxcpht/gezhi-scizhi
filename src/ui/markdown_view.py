"""Native Markdown prose with independently configurable fenced-code previews."""
from __future__ import annotations

import re

import flet as ft
from markdown_it import MarkdownIt

from .. import settings
from . import theme
from .code_preview import CodePreview

_MARKDOWN_OPTIONS = {
    'auto_follow_links', 'auto_follow_links_target', 'shrink_wrap', 'fit_content',
    'soft_line_break', 'image_error_content', 'code_theme', 'code_style_sheet',
    'md_style_sheet', 'latex_scale_factor', 'latex_style', 'on_tap_text',
    'on_selection_change',
}


def _segments(source: str) -> list[tuple[str, str, str]]:
    """Split only top-level fences; keep lists, quotes and HTML blocks intact."""
    environment: dict = {}
    tokens = MarkdownIt('commonmark').parse(source, environment)
    fences = [token for token in tokens if token.type == 'fence' and token.level == 0]
    if not fences:
        return [('markdown', source, '')]
    # CommonMark counts CR/LF lines; Unicode separators stay within a line.
    lines = re.findall(r'[^\r\n]*(?:\r\n|[\r\n]|$)', source)
    definitions = ''.join(
        ''.join(lines[reference['map'][0]:reference['map'][1]])
        for reference in environment.get('references', {}).values()
        if reference.get('map')
    )
    result, cursor = [], 0

    def prose(start: int, end: int) -> None:
        text = ''.join(lines[start:end])
        if text.strip():
            # Each native Markdown control resolves references separately.
            # Repeat only invisible definitions, never the surrounding prose.
            if definitions and definitions not in text:
                text += '\n\n' + definitions
            result.append(('markdown', text, ''))

    for token in fences:
        start, end = token.map
        prose(cursor, start)
        language = token.info.strip().split(maxsplit=1)[0] if token.info.strip() else 'text'
        result.append(('code', token.content, language))
        cursor = end
    prose(cursor, len(lines))
    return result


class MarkdownView(ft.Column):
    """Keep native Markdown for rich prose and nested blocks.

    Top-level fenced blocks use CodePreview so line numbers and wrapping share
    the file/diff preferences. ``value`` retains the full original document,
    including an unfinished streaming fence. Existing blocks keep their control
    identity when a stream appends text.
    """

    def __init__(self, value: str = '', selectable: bool = False,
                 extension_set=ft.MarkdownExtensionSet.NONE, on_tap_link=None,
                 options: dict | None = None, dark: bool | None = None, **kwargs):
        markdown_options = {name: kwargs.pop(name) for name in _MARKDOWN_OPTIONS if name in kwargs}
        kwargs.setdefault('spacing', 10)
        kwargs.setdefault('tight', True)
        kwargs.setdefault('horizontal_alignment', ft.CrossAxisAlignment.STRETCH)
        super().__init__(**kwargs)
        self._scizhi_markdown_view = True
        self._markdown_options = {**markdown_options, 'selectable': selectable,
                                  'extension_set': extension_set, 'on_tap_link': on_tap_link}
        self._appearance_options = dict(settings.load() if options is None else options)
        self._appearance_dark = theme.palette.dark if dark is None else dark
        self._value = None
        self.value = value

    @property
    def value(self) -> str:
        return self._value

    @value.setter
    def value(self, value: str) -> None:
        incoming = str(value or '')
        if incoming == self._value:
            return
        self._value = incoming
        previous, controls = self.controls, []
        for index, (kind, text, language) in enumerate(_segments(self._value)):
            old = previous[index] if index < len(previous) else None
            if kind == 'code':
                if isinstance(old, CodePreview):
                    control = old
                    control.source, control.language = text, language
                    control.refresh(self._appearance_options, self._appearance_dark)
                else:
                    control = CodePreview(text, language, self._appearance_options, self._appearance_dark)
                self._style_code(control)
            else:
                control = old if isinstance(old, ft.Markdown) else ft.Markdown(**self._markdown_options)
                control.value = text
                theme._style_markdown(control)
            controls.append(control)
        self.controls = controls

    @property
    def selectable(self) -> bool:
        return self._markdown_options['selectable']

    @selectable.setter
    def selectable(self, value: bool) -> None:
        self._markdown_options['selectable'] = bool(value)
        for control in self.controls:
            if isinstance(control, ft.Markdown):
                control.selectable = bool(value)
            elif isinstance(control, CodePreview):
                self._style_code(control)

    @property
    def on_tap_link(self):
        return self._markdown_options['on_tap_link']

    @on_tap_link.setter
    def on_tap_link(self, callback) -> None:
        self._markdown_options['on_tap_link'] = callback
        for control in self.controls:
            if isinstance(control, ft.Markdown):
                control.on_tap_link = callback

    def _style_code(self, control: CodePreview) -> None:
        for line in control.code_lines:
            line.selectable = self.selectable
            line.on_selection_change = self._markdown_options.get('on_selection_change')

    def refresh_appearance(self, options: dict | None = None, dark: bool | None = None) -> None:
        """Apply a live appearance snapshot without reparsing or replacing prose."""
        if options is not None:
            self._appearance_options = dict(options)
        self._appearance_dark = theme.palette.dark if dark is None else dark
        for control in self.controls:
            if isinstance(control, CodePreview):
                control.refresh(self._appearance_options, self._appearance_dark)
                self._style_code(control)
            else:
                theme._style_markdown(control)
