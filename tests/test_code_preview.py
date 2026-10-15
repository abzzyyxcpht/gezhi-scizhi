import flet as ft
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.ui.code_preview import CodePreview


def test_code_content_survives_theme_line_number_and_wrap_changes():
    source = 'def audit(value):\n    return "证据" + str(value)  # 保留来源\n'
    view = CodePreview(source, language='python', options={'code_font_size': 17}, dark=False)
    assert view.source == source
    assert all(row.controls[0].visible for row in view.rows)
    assert ''.join(s.text for s in view.code_lines[1].spans) == source.splitlines()[1]
    assert view.code_lines[0].size == 17
    before = [s.style.color for s in view.code_lines[0].spans]
    view.refresh({'code_font_size': 12, 'ui_font_size': 20,
                  'code_show_line_numbers': False, 'code_wrap_long_lines': False}, dark=True)
    assert view.source == source
    assert all(not row.controls[0].visible for row in view.rows)
    assert view.code_lines[0].size == 12
    assert view.code_lines[0].no_wrap
    assert view.code_body.scroll == ft.ScrollMode.AUTO
    assert before != [s.style.color for s in view.code_lines[0].spans]


def test_code_theme_choices_change_highlighting_without_changing_text():
    view = CodePreview('return 42', options={'code_light_theme': 'github'}, dark=False)
    before = [s.style.color for s in view.code_lines[0].spans]
    view.refresh({'code_light_theme': 'atom-one-light'}, dark=False)
    assert before != [s.style.color for s in view.code_lines[0].spans]
    assert ''.join(s.text for s in view.code_lines[0].spans) == 'return 42'


def test_unchanged_code_keeps_native_controls_during_appearance_refresh():
    view = CodePreview('return 42', language='python', options={'code_font_size': 14}, dark=False)
    original = view.code_lines[0]
    view.refresh({'code_font_size': 14, 'ui_font_size': 20}, dark=False)
    assert view.code_lines[0] is original
    view.refresh({'code_font_size': 18}, dark=False)
    assert view.code_lines[0] is not original
    assert view.code_lines[0].size == 18


def test_appending_prose_does_not_rebuild_completed_code():
    from src.ui.markdown_view import MarkdownView
    view = MarkdownView('```python\nreturn 42\n```\n\nAfter', options={}, dark=False)
    original = view.controls[0].code_lines[0]
    view.value += ' this code.'
    assert view.controls[0].code_lines[0] is original


def test_copy_uses_exact_source_without_line_numbers_or_display_wrapping():
    source = 'def audit():\r\n\treturn "研究"\r\n'
    view = CodePreview(source, 'python', options={'code_wrap_long_lines': True})
    page = SimpleNamespace(clipboard=SimpleNamespace(set=AsyncMock()), update=Mock())
    asyncio.run(view.copy_button.on_click(SimpleNamespace(page=page)))
    page.clipboard.set.assert_awaited_once_with(source)
    assert view.copy_button.content == '已复制'
    assert not view.copy_button.disabled


def test_copy_failure_is_retryable_without_false_success_or_error_details():
    view = CodePreview('print(1)', options={})
    page = SimpleNamespace(clipboard=SimpleNamespace(set=AsyncMock(side_effect=OSError('private details'))), update=Mock())
    asyncio.run(view.copy_button.on_click(SimpleNamespace(page=page)))
    assert view.copy_button.content == '重试复制'
    assert not view.copy_button.disabled
    assert 'private details' not in str(view.copy_button.tooltip)
    page.clipboard.set.side_effect = None
    asyncio.run(view.copy_button.on_click(SimpleNamespace(page=page)))
    assert view.copy_button.content == '已复制'


def test_copy_acknowledges_only_completed_snapshot_and_prevents_double_click():
    async def scenario():
        pending = asyncio.Event()
        view = CodePreview('old', options={})
        async def set_clipboard(value):
            await pending.wait()
        page = SimpleNamespace(clipboard=SimpleNamespace(set=AsyncMock(side_effect=set_clipboard)), update=Mock())
        event = SimpleNamespace(page=page)
        task = asyncio.create_task(view.copy_button.on_click(event))
        await asyncio.sleep(0)
        assert view.copy_button.disabled
        assert view.copy_button.content != '已复制'
        await view.copy_button.on_click(event)
        view.value = 'new'
        pending.set()
        await task
        page.clipboard.set.assert_awaited_once_with('old')
        assert view.copy_button.content == '重新复制'
        assert not view.copy_button.disabled
    asyncio.run(scenario())


def test_empty_code_cannot_replace_clipboard_contents():
    view = CodePreview('', options={})
    page = SimpleNamespace(clipboard=SimpleNamespace(set=AsyncMock()), update=Mock())
    assert view.copy_button.disabled
    asyncio.run(view.copy_button.on_click(SimpleNamespace(page=page)))
    page.clipboard.set.assert_not_called()
