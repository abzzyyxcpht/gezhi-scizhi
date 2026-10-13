import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import settings, workspace_features as features


class WorkspaceFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(features, 'DATA_DIR', self.root),
                        patch.object(settings, '_PATH', self.root / 'settings.json')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_only_approved_active_memories_enter_bounded_stable_context(self):
        a = features.handle({'cmd': 'memory_save', 'memory': {'title': '引用偏好', 'content': '使用 APA', 'approved': True}})
        features.handle({'cmd': 'memory_save', 'memory': {'title': '未复核材料', 'content': 'DO NOT INJECT'}})
        first = features.memory_context(300)
        self.assertIn('使用 APA', first)
        self.assertNotIn('DO NOT INJECT', first)
        self.assertEqual(first, features.memory_context(300))
        self.assertEqual('', features.memory_context(1))
        features.handle({'cmd': 'memory_save', 'memory': {'id': a['memory']['id'], 'content': '改用国标'}})
        self.assertEqual('', features.memory_context(300), 'editing approved content revokes approval')

    def test_memory_disable_and_delete_are_effective(self):
        row = features.handle({'cmd': 'memory_save', 'memory': {'title': '设置', 'content': '偏好', 'approved': True}})['memory']
        settings.save({'memory_enabled': False})
        self.assertEqual('', features.memory_context(1000))
        features.handle({'cmd': 'memory_delete', 'id': row['id']})
        self.assertEqual([], features.handle({'cmd': 'memory_list'})['memories'])

    def test_agent_memory_search_only_returns_approved_active_records(self):
        from src.agent_runtime import AgentRuntime
        for title, approved, enabled in [('可用约定', True, True), ('未审核', False, True), ('已停用', True, False)]:
            features.handle({'cmd': 'memory_save', 'memory': {'title': title, 'content': '报告随机种子',
                             'approved': approved, 'enabled': enabled}})
        result = AgentRuntime._memory_search({'query': '随机种子'})
        self.assertEqual(['可用约定'], [r['title'] for r in result['memories']])
        settings.save({'memory_enabled': False})
        self.assertFalse(AgentRuntime._memory_search({})['ok'])

    def test_commands_only_expand_task_text_no_shell_and_no_recursive_dispatch(self):
        features.handle({'cmd': 'custom_command_save', 'command': {'name': 'review-paper', 'description': '审稿',
                         'template': '检查 {{input}} 的对照实验；字面值 $(whoami)', 'mode': 'review'}})
        value = features.expand_command('review-paper', '论文 A')
        self.assertEqual('review', value['mode'])
        self.assertIn('$(whoami)', value['request'])
        self.assertIn('论文 A', value['request'])
        with self.assertRaises(ValueError):
            features.handle({'cmd': 'custom_command_save', 'command': {'name': 'bad', 'template': 'x', 'mode': 'shell'}})

    def test_settings_invalid_shortcut_and_parallel_limit_rejected_atomically(self):
        with self.assertRaises(ValueError):
            settings.save({'subagent_max_parallel': 5})
        with self.assertRaises(ValueError):
            settings.save({'keyboard_shortcuts': {'commands': 'Ctrl+K', 'settings': 'Ctrl+K'}})
        settings.save({'keyboard_shortcuts': {'commands': 'Alt+K'}})
        self.assertEqual('commands', features.shortcut_action('k', alt=True))
        self.assertIsNone(features.shortcut_action('k', ctrl=True))

    def test_prompt_memory_is_bounded_and_separate_from_research_facts(self):
        from src import prompts
        features.handle({'cmd': 'memory_save', 'memory': {'title': '研究习惯', 'content': '优先比较消融实验', 'approved': True}})
        with patch('src.context_file.load_body', return_value=''), patch('src.skills.select_for_stage', return_value={
            'selected': [], 'deferred': [], 'missing': [], 'budget': {'used_chars': 0}}):
            text = prompts.build('课题', context_budget=500)[0]['content']
        self.assertIn('优先比较消融实验', text)
        self.assertIn('记忆不是科研证据', text)


if __name__ == '__main__':
    unittest.main()
