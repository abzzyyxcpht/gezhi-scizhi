import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
from src import config, settings, workspace_features, research_notebook
from src.ui.settings_center import SettingsCenter, CATEGORIES, apply_appearance, memory_manager
from src.ui import theme


def controls(root):
    yield root
    for name in ('controls', 'content'):
        child = getattr(root, name, None)
        for node in child if isinstance(child, (list, tuple)) else [child] if isinstance(child, ft.Control) else []:
            yield from controls(node)


class SettingsCenterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, name in [(settings, '_PATH'), (workspace_features, 'DATA_DIR'), (config, 'DATA_DIR')]:
            p = patch.object(target, name, Path(self.tmp.name) / 'settings.json' if name == '_PATH' else Path(self.tmp.name))
            p.start()
            self.addCleanup(p.stop)
        self.panel = SettingsCenter(MagicMock())

    def click(self, title):
        button = next(c for c in controls(self.panel.content) if isinstance(c, (ft.FilledButton, ft.OutlinedButton)) and c.content == title)
        button.on_click(None)

    def test_all_categories_construct_without_network_or_paid_model(self):
        with patch('src.llm.chat', side_effect=AssertionError('no model')), patch('src.mcp.Client', side_effect=AssertionError('no spawn')):
            for key, label, _ in CATEGORIES:
                if key in {'models', 'plugins', 'usage'}:
                    continue  # These are desktop-owned sections tested by desktop tests.
                self.panel.select(key)
                self.assertEqual(label, self.panel.title.value)
                texts = [c.value for c in controls(self.panel.content) if isinstance(c, ft.Text)]
                self.assertNotIn('读取此功能失败，请重试。', texts, key)

    def test_memory_form_persists_real_approved_memory(self):
        self.panel.select('memory')
        fields = self.panel.forms['memory']
        fields['title'].value, fields['content'].value = '实验偏好', '报告随机种子'
        fields['approved'].value = True
        self.click('保存记忆')
        self.assertIn('报告随机种子', workspace_features.memory_context())

    def test_memory_scope_payload_requires_registered_project_and_role(self):
        projects = [{'id': 'p1', 'name': '项目一'}]
        self.assertEqual({'scope': 'global'}, memory_manager(projects=projects))
        self.assertEqual({'scope': 'project', 'project_id': 'p1'},
                         memory_manager('project', 'p1', projects=projects))
        self.assertEqual({'scope': 'role', 'role': 'critic', 'project_id': None},
                         memory_manager('role', role='critic', projects=projects))
        self.assertEqual({'scope': 'role', 'role': 'critic', 'project_id': 'p1'},
                         memory_manager('role', 'p1', 'critic', projects))
        with self.assertRaises(ValueError):
            memory_manager('project', 'unknown', projects=projects)
        with self.assertRaises(ValueError):
            memory_manager('role', role='', projects=projects)

    def test_memory_editor_saves_project_scope_and_shows_it(self):
        project_root = Path(self.tmp.name) / 'project-a'
        project_root.mkdir()
        from src import project_workspace
        project_workspace.handle({'cmd': 'project_open', 'path': str(project_root), 'name': '项目一'})
        self.panel.select('memory')
        fields = self.panel.forms['memory']
        fields['title'].value, fields['content'].value = '项目偏好', '项目内约定'
        fields['scope'].value = 'project'
        fields['project_id'].value = project_workspace.handle({'cmd': 'project_list'})['active_project_id']
        fields['approved'].value = True
        self.click('保存记忆')
        row = workspace_features.handle({'cmd': 'memory_list'})['memories'][0]
        self.assertEqual('project', row['scope'])
        self.assertEqual(fields['project_id'].value, row['project_id'])
        self.assertTrue(any('项目' in str(c.value) for c in controls(self.panel.content)
                            if isinstance(c, ft.Text)))

    def test_invalid_numeric_input_reports_without_crashing(self):
        self.panel.select('automations')
        for c in controls(self.panel.content):
            if isinstance(c, ft.TextField) and c.label == '完成后间隔（秒）':
                c.value = 'not-a-number'
        self.click('创建并启动自动化')
        self.assertIn('整数', self.panel.message.value)

    def test_notebook_form_writes_user_record_and_rejects_empty_completed_result(self):
        self.panel.select('notebook')
        fields = self.panel.forms['notebook']
        fields['title'].value, fields['question'].value = '实验设计', '如何排除样本泄漏？'
        fields['status'].value = 'completed'
        self.click('保存科研记录')
        self.assertEqual([], research_notebook.handle({'cmd': 'notebook_list'})['records'])
        fields['status'].value = 'planned'
        self.click('保存科研记录')
        records = research_notebook.handle({'cmd': 'notebook_list'})['records']
        self.assertEqual(1, len(records))
        self.assertEqual('实验设计', records[0]['title'])
        self.assertEqual('user_recorded', records[0]['provenance']['origin'])

    def test_http_mcp_editor_saves_without_connecting_and_preserves_hidden_credentials(self):
        from src import mcp
        with patch.object(mcp, 'CONFIG_PATH', Path(self.tmp.name) / 'mcp_servers.json'), patch('httpx.Client', side_effect=AssertionError('no connect')):
            self.panel.select('mcp')
            fields = self.panel.forms['mcp']
            fields['name'].value, fields['transport'].value = 'papers', 'streamable_http'
            fields['url'].value = 'https://example.test/mcp'
            fields['headers'].value = '{"Authorization":"Bearer fixture-secret"}'
            self.click('保存服务')
            rows = mcp.handle({'cmd': 'mcp_config'})['servers']
            self.assertEqual(1, len(rows), self.panel.message.value)
            self.assertEqual('streamable_http', rows[0]['transport'])
            self.assertNotIn('fixture-secret', str(rows))
            edit = next(c for c in controls(self.panel.content) if isinstance(c, ft.TextButton) and c.content == '编辑')
            edit.on_click(None)
            self.assertEqual('', self.panel.forms['mcp']['headers'].value)
            self.click('保存服务')
            self.assertEqual('Bearer fixture-secret', mcp.load_servers()[0]['headers']['Authorization'])

    def test_appearance_scaling_idempotent_and_reversible(self):
        label = theme.mark_typography(ft.Text('研究记录', size=14), 'base')
        row = ft.Column([label], spacing=10)
        settings.save({'ui_font_size': 20, 'density': 'compact'})
        apply_appearance(row)
        apply_appearance(row)
        self.assertEqual(20, label.size)
        self.assertEqual(8, row.spacing)
        settings.save({'ui_font_size': 14, 'density': 'comfortable'})
        apply_appearance(row)
        self.assertEqual(14, label.size)
        self.assertEqual(10, row.spacing)

    def test_narrow_layout_uses_one_category_selector(self):
        self.panel.resize(650)
        self.assertFalse(self.panel.nav.visible)
        self.assertTrue(self.panel.selector.visible)
        self.assertLessEqual(self.panel.main.width, 650)

    def test_category_search_supports_english_chinese_clear_and_no_results(self):
        self.panel.search_input.value = 'appearance'
        self.panel._on_search()
        self.assertEqual(['appearance'], self.panel._filtered_keys)
        self.assertTrue(self.panel.nav_buttons['appearance'].visible)
        self.assertFalse(self.panel.nav_buttons['general'].visible)
        self.assertEqual(['appearance'], [option.key for option in self.panel.selector.options])
        self.assertTrue(all(isinstance(button, ft.TextButton) for button in self.panel.nav_buttons.values()))
        self.panel.search_input.value = '不存在的设置'
        self.panel._on_search()
        self.assertEqual([], self.panel._filtered_keys)
        self.assertTrue(self.panel.search_empty.visible)
        self.assertTrue(self.panel.selector.disabled)
        self.assertIn(self.panel.search_empty, self.panel.content.controls)
        self.panel._clear_search()
        self.assertEqual(len(CATEGORIES), len(self.panel._filtered_keys))
        self.assertFalse(self.panel.search_empty.visible)

    def test_search_keeps_unsaved_form_and_enter_selects_first_match(self):
        self.panel.select('general')
        field = next(c for c in controls(self.panel.content)
                     if isinstance(c, ft.Switch) and c.label == '默认使用离线演示材料')
        field.value = False
        self.panel.search_input.value = 'appearance'
        self.panel._on_search()
        self.assertIs(field, next(c for c in controls(self.panel.content)
                                  if isinstance(c, ft.Switch) and c.label == '默认使用离线演示材料'))
        self.assertFalse(field.value)
        self.panel._select_first_filtered()
        self.assertEqual('appearance', self.panel.section)

    def test_appearance_apply_is_explicit_and_only_writes_appearance_patch(self):
        self.panel.select('appearance')
        fields = self.panel.forms['appearance']
        fields['font_family'].value = 'yahei'
        fields['code_font'].value = 'consolas'
        fields['scale'].value = '1.1'
        captured = {}
        with patch.object(self.panel, '_save_settings', side_effect=lambda value: captured.update(value) or {}):
            self.panel._apply_appearance_settings(fields['font_family'], fields['code_font'], fields['scale'],
                                                  fields['density'], fields['dark'], fields['animation'], fields['inspector'])
        self.assertEqual('yahei', captured['appearance_font_family'])
        self.assertEqual('consolas', captured['appearance_code_font'])
        self.assertEqual(1.1, captured['appearance_font_scale'])
        self.assertNotIn('llm_api_key', captured)

    def test_apply_appearance_scales_dropdown_labels_and_button_style_once(self):
        dropdown = ft.Dropdown(text_size=13, label_style=ft.TextStyle(size=12))
        button = ft.OutlinedButton('应用', style=ft.ButtonStyle(text_style=ft.TextStyle(size=14)))
        settings.save({'ui_font_size': 20, 'density': 'comfortable'})
        apply_appearance(dropdown, button)
        apply_appearance(dropdown, button)
        self.assertEqual(19, dropdown.text_size)
        self.assertEqual(18, dropdown.label_style.size)
        self.assertEqual(20, button.style.text_style.size)

    def test_appearance_accepts_custom_section_without_native_preview(self):
        center = SettingsCenter(MagicMock(), sections={'appearance': []})
        center.select('appearance')
        center.resize(480)
        self.assertEqual('appearance', center.section)


if __name__ == '__main__':
    unittest.main()
