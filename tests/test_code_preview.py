import flet as ft

from src.ui.code_preview import CodePreview


def test_code_content_survives_theme_line_number_and_wrap_changes():
    source = 'def audit(value):\n    return "证据" + str(value)  # 保留来源\n'
    view = CodePreview(source, language='python', options={'code_font_size': 17})
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
    assert view.content.scroll == ft.ScrollMode.AUTO
    assert before != [s.style.color for s in view.code_lines[0].spans]


def test_code_theme_choices_change_highlighting_without_changing_text():
    view = CodePreview('return 42', options={'code_light_theme': 'github'}, dark=False)
    before = [s.style.color for s in view.code_lines[0].spans]
    view.refresh({'code_light_theme': 'atom-one-light'}, dark=False)
    assert before != [s.style.color for s in view.code_lines[0].spans]
    assert ''.join(s.text for s in view.code_lines[0].spans) == 'return 42'
