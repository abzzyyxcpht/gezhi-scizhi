"""Shared command contracts; no model, plugin side effects or real settings writes."""
from __future__ import annotations

import unittest
from unittest import mock

from src import command_center, plugins


class CommandCenterContractTest(unittest.TestCase):
    def dispatch(self, text, expected):
        with mock.patch('src.bridge.handle', return_value={'ok': True}) as handle:
            result = command_center.execute(text)
        self.assertTrue(result['ok'], result)
        handle.assert_called_once_with(expected)

    def test_model_default_matches_tui(self):
        self.dispatch('/model default fixture-model',
                      {'cmd': 'settings_save', 'settings': {'llm_model': 'fixture-model'}})

    def test_model_clear_commands_match_tui(self):
        for command, field in [('clear-key', 'llm_api_key'), ('clear-endpoint', 'llm_base_url')]:
            with self.subTest(command=command):
                self.dispatch('/model ' + command,
                              {'cmd': 'settings_save', 'settings': {field: ''}})

    def test_model_missing_or_extra_arguments_do_not_save(self):
        for text in ['/model default', '/model clear-key unexpected', '/model key',
                     '/model endpoint one two', '/model budget 1.5']:
            with self.subTest(text=text), mock.patch('src.bridge.handle', return_value={'ok': True}) as handle:
                self.assertFalse(command_center.execute(text)['ok'])
                handle.assert_not_called()

    def test_route_no_argument_reads_registry(self):
        self.dispatch('/route', {'cmd': 'model_registry'})

    def test_reasoning_catalog_lists_all_eight_supported_efforts(self):
        item = next(row for row in command_center.COMMANDS if row['name'] == 'reasoning')
        self.assertIn('minimal', item['args'])

    def test_key_never_appears_in_success_or_failure_display(self):
        private = 'fixture-private-key'
        for response in ({'ok': True, 'settings': {'llm_api_key': private}},
                         {'ok': False, 'error': 'failed with ' + private}):
            with self.subTest(response=response), mock.patch('src.bridge.handle', return_value=response):
                result = command_center.execute('/model key ' + private)
                self.assertNotIn(private, result.get('text', '') + result.get('error', ''))

    def test_dynamic_plugin_dispatch_preserves_structured_failure_and_raw_arguments(self):
        rows = [{'name': 'fixture-plugin', 'plugin': 'fixture', 'help': '中文材料能力'}]
        with mock.patch.object(plugins, 'commands', return_value=rows), \
                mock.patch.object(plugins, 'run_command_result', return_value={
                    'ok': False, 'output': 'fixture failure', 'error': 'fixture failure'}) as run:
            result = command_center.execute('/fixture-plugin "quoted value"', 'task-a')
        run.assert_called_once_with('fixture-plugin', '"quoted value"', 'task-a')
        self.assertFalse(result['ok'])
        self.assertEqual(result['text'], 'fixture failure')

    def test_dynamic_catalog_includes_enabled_plugin_and_help_then_disappears(self):
        row = {'name': 'fixture-plugin', 'plugin': 'fixture', 'help': '中文材料能力'}
        with mock.patch.object(plugins, 'commands', return_value=[row]):
            catalog = command_center.catalog()
            listed = next(item for item in catalog if item['name'] == 'fixture-plugin')
            self.assertEqual(listed['description'], row['help'])
            with mock.patch('src.bridge.handle') as handle:
                help_result = command_center.execute('/help')
            handle.assert_not_called()
            self.assertIn('/fixture-plugin', help_result['text'])
        with mock.patch.object(plugins, 'commands', return_value=[]):
            self.assertNotIn('fixture-plugin', {item['name'] for item in command_center.catalog()})

    def test_plugin_cannot_shadow_builtin_command(self):
        with mock.patch.object(plugins, 'commands', return_value=[
                {'name': 'model', 'plugin': 'fixture', 'help': 'Shadow'}]), \
                mock.patch.object(plugins, 'run_command_result') as run:
            self.dispatch('/model default fixture',
                          {'cmd': 'settings_save', 'settings': {'llm_model': 'fixture'}})
            rows = [row for row in command_center.catalog() if row['name'] == 'model']
        self.assertEqual(len(rows), 1)
        run.assert_not_called()

    def test_busy_and_remote_catalog_reports_actionable_disabled_commands(self):
        rows = {row['name']: row for row in command_center.catalog(context={'busy': True})}
        self.assertFalse(rows['plan']['available'])
        self.assertIn('仍在执行', rows['plan']['disabled_reason'])
        self.assertTrue(rows['mode']['available'])
        self.assertTrue(rows['cancel-agent']['busy_allowed'])
        self.assertEqual('Ctrl+G', rows['mode']['shortcut'])
        self.assertIn('新会话', rows['mode']['effect'])
        remote = {row['name']: row for row in command_center.catalog(context={'remote': True})}
        self.assertFalse(remote['mode']['available'])
        self.assertIn('主机本地', remote['mode']['disabled_reason'])
        self.assertTrue(remote['tasks']['available'])

    def test_remote_catalog_uses_the_actual_subcommand_capability(self):
        def available(name, args=''):
            rows = command_center.catalog(context={'remote': True, 'command': {'name': name, 'args': args}})
            return next(row['available'] for row in rows if row['name'] == name)
        for name in ('connections', 'models', 'route', 'commands', 'new'):
            self.assertTrue(available(name), name)
        self.assertTrue(available('model', 'capabilities'))
        for name in ('project', 'chat', 'notebook', 'todo', 'schedule', 'hooks'):
            self.assertTrue(available(name, 'list'), name)
        for name, args in [('route', 'planning profile-a'), ('project', 'open D:/project'),
                           ('chat', 'new 新会话'), ('notebook', 'save {}'),
                           ('todo', 'save {}'), ('schedule', 'run record-a'),
                           ('hooks', 'save {}'), ('run', 'template 请求')]:
            self.assertFalse(available(name, args), (name, args))


if __name__ == '__main__':
    unittest.main()
