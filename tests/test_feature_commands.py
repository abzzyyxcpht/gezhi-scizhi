import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import settings, workspace_features, mcp, config, control_integrations
from src.command_center import execute


class FeatureCommandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for obj, key, value in [(settings, '_PATH', self.root / 'settings.json'),
                (workspace_features, 'DATA_DIR', self.root), (mcp, 'CONFIG_PATH', self.root / 'mcp.json'),
                (config, 'DATA_DIR', self.root), (control_integrations, 'CONFIG_PATH', self.root / 'controls.json')]:
            p = patch.object(obj, key, value)
            p.start()
            self.addCleanup(p.stop)

    def test_memory_command_preserves_spaces_and_quotes(self):
        row = {'title': '实验习惯', 'content': '原样保留 "test value" 和 C:\\experiment', 'approved': True}
        r = execute('/memory save ' + json.dumps(row, ensure_ascii=False))
        self.assertTrue(r['ok'], r)
        listed = execute('/memory list')
        self.assertEqual(row['content'], listed['memories'][0]['content'])

    def test_notebook_command_round_trip_and_fixed_export(self):
        r = execute('/notebook save ' + json.dumps({'title': '复现实验', 'question': '增加检索是否改变准确率？',
            'kind': 'experiment', 'status': 'planned'}))
        self.assertTrue(r['ok'], r)
        identifier = r['record']['id']
        result = execute('/notebook export ' + identifier + ' markdown')
        self.assertTrue(result['ok'], result)
        self.assertTrue(Path(result['path']).is_relative_to(self.root))
        self.assertIn('not_performed', execute('/notebook get ' + identifier)['record']['provenance']['scientific_verification'])

    def test_mcp_config_save_does_not_launch_server(self):
        with patch.object(mcp, 'Client', side_effect=AssertionError('unexpected launch')):
            r = execute('/mcp-config save ' + json.dumps({'name': 'browser', 'command': 'test-program', 'env': {'SECRET': 'private'}, 'enabled': False}))
            self.assertTrue(r['ok'], r)
            listed = execute('/mcp-config list')
        self.assertNotIn('private', json.dumps(listed))

    def test_bad_json_never_echoes_private_input(self):
        result = execute('/mcp-config save {"env": {"KEY": "private-token"}')
        self.assertFalse(result['ok'])
        self.assertNotIn('private-token', str(result))

    def test_prompt_command_enters_regular_agent_permission_path(self):
        execute('/command-save ' + json.dumps({'name': 'audit-method', 'template': '审核 {{input}}', 'mode': 'review'}))
        with patch('src.bridge._runtime') as rt:
            rt.return_value.start.return_value = {'session_id': 'session1', 'status': 'waiting_approval'}
            result = execute('/run audit-method 方法 A')
        self.assertTrue(result['ok'])
        # 双模式契约：workspace_mode 作为关键字参数传入（既有行为，2026-10-04 起）
        rt.return_value.start.assert_called_once_with('审核 方法 A', 'review', 'research', workspace_mode='research')


if __name__ == '__main__':
    unittest.main()
