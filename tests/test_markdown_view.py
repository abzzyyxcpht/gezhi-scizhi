"""Markdown prose and streamed fenced code retain their display contracts."""
from unittest.mock import patch

import flet as ft
import pytest

from src.ui import theme
from src.ui.code_preview import CodePreview


@pytest.fixture(autouse=True)
def appearance():
    old_dark = theme.palette.dark
    old_options = dict(theme._preferences)
    old_canonical = theme._canonical
    theme.palette.set_dark(False)
    theme.configure_typography({'ui_font_size': 14, 'code_font_size': 14})
    with patch('src.ui.code_preview.settings.load', return_value={}):
        yield
    theme.configure_typography(old_options)
    theme._canonical = old_canonical
    theme.palette.set_dark(old_dark)


def view_class():
    from importlib.util import find_spec
    assert find_spec('src.ui.markdown_view') is not None, 'A Markdown view must apply code display preferences'
    from src.ui.markdown_view import MarkdownView
    return MarkdownView


def test_prose_and_top_level_code_preserve_text_and_selection():
    source = '# Evidence\n\nA **linked** [source](https://example.org).\n\n```python\nvalue = 42\nprint(value)\n```\n\nAfter code.\n'
    def callback(event):
        return None
    view = view_class()(source, selectable=True, on_tap_link=callback,
                        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)
    assert view.value == source
    assert [type(item) for item in view.controls] == [ft.Markdown, CodePreview, ft.Markdown]
    assert view.controls[0].value == source.split('```python')[0]
    assert view.controls[1].source == 'value = 42\nprint(value)\n'
    assert view.controls[1].language == 'python'
    assert view.controls[2].value == '\nAfter code.\n'
    assert view.controls[0].on_tap_link is callback
    assert all(item.selectable for item in view.controls if isinstance(item, ft.Markdown))


def test_streaming_incomplete_fence_and_later_prose_preserve_existing_controls():
    view = view_class()('Start\n\n```python\npri', selectable=True)
    code = view.controls[1]
    assert isinstance(code, CodePreview)
    assert code.source.rstrip('\n') == 'pri'
    view.value += 'nt("证据")\n```\n\nFinished.'
    assert view.controls[1] is code
    assert code.source == 'print("证据")\n'
    assert view.controls[2].value == '\nFinished.'
    view.value = ''
    assert view.value == ''
    assert len(view.controls) == 1 and isinstance(view.controls[0], ft.Markdown)


def test_nested_lists_quotes_and_indented_code_remain_native_markdown():
    source = ('1. First\n\n   ```python\n   print(1)\n   ```\n\n'
              '2. Second\n   - nested item\n\n> ```text\n> quoted\n> ```\n\n'
              '    indented code\n\n~~~python\nprint(2)\n~~~\n')
    view = view_class()(source)
    assert len(view.controls) == 2
    assert isinstance(view.controls[0], ft.Markdown)
    assert view.controls[0].value == source.split('~~~python')[0]
    assert view.controls[1].source == 'print(2)\n'


def test_long_fences_keep_shorter_delimiters_in_code():
    view = view_class()('````markdown\n```python\nprint(1)\n```\n````\n')
    assert len(view.controls) == 1
    assert view.controls[0].source == '```python\nprint(1)\n```\n'


def test_reference_links_work_across_code_segments():
    source = ('[Evidence][proof]\n\n```python\nprint(1)\n```\n\n'
              '[Again][proof]\n\n[proof]: https://example.org/evidence "Study"\n')
    view = view_class()(source)
    definition = '[proof]: https://example.org/evidence "Study"\n'
    assert definition in view.controls[0].value
    assert definition in view.controls[2].value
    assert view.value == source


def test_appearance_changes_reach_code_and_new_streamed_content():
    view = view_class()('Intro\n\n```python\nreturn 42\n```\n', selectable=True)
    code = view.controls[1]
    before = [span.style.color for span in code.code_lines[0].spans]
    theme.palette.set_dark(True)
    theme.configure_typography({'ui_font_size': 20, 'code_font_size': 17,
                                'code_show_line_numbers': False, 'code_wrap_long_lines': False})
    theme.style_inputs(view)
    assert code.code_lines[0].size == 17
    assert code.code_lines[0].no_wrap
    assert not code.rows[0].controls[0].visible
    assert code.content.scroll == ft.ScrollMode.AUTO
    assert before != [span.style.color for span in code.code_lines[0].spans]
    assert view.controls[0].md_style_sheet.p_text_style.size == 20
    view.value = 'Intro\n\n```python\nreturn 43\n```\n'
    assert view.controls[1].code_lines[0].size == 17
    assert not view.controls[1].rows[0].controls[0].visible
    theme.palette.set_dark(False)
    theme.configure_typography({'ui_font_size': 12, 'code_font_size': 12,
                                'code_show_line_numbers': True, 'code_wrap_long_lines': True})
    theme.repaint(view)
    assert code.code_lines[0].size == 12
    assert not code.code_lines[0].no_wrap
    assert code.rows[0].controls[0].visible
    assert view.controls[0].md_style_sheet.p_text_style.size == 12


def test_html_block_with_fence_text_is_not_split():
    source = '<pre>\n```python\nprint(1)\n```\n</pre>\n'
    view = view_class()(source)
    assert len(view.controls) == 1
    assert isinstance(view.controls[0], ft.Markdown)
    assert view.controls[0].value == source


def test_unicode_separators_do_not_change_markdown_source_line_maps():
    source = 'First\u2028still first\n\n```python\nprint(1)\n```\n\nLast\n'
    view = view_class()(source)
    assert view.controls[0].value == 'First\u2028still first\n\n'
    assert view.controls[1].source == 'print(1)\n'
    assert view.controls[2].value == '\nLast\n'


def test_selection_and_link_callbacks_remain_editable_after_creation():
    view = view_class()('[source](https://example.org)\n\n```python\nreturn 1\n```\n')
    def follow(event):
        return None
    view.on_tap_link = follow
    view.selectable = True
    assert view.controls[0].on_tap_link is follow
    assert view.controls[0].selectable
    assert view.controls[1].code_lines[0].selectable
    view.selectable = False
    assert not view.controls[1].code_lines[0].selectable
