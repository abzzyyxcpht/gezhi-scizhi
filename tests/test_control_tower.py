from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_runtime import SessionStore


class ControlTowerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root / 'sessions')

    def test_pending_approval_is_actionable_and_secrets_are_redacted(self):
        from src.control_tower import snapshot
        self.store.append('one', 'user_message', {'request': 'Review API_KEY=private-value'})
        self.store.append('one', 'permission_requested', {'tool_call_id': 'call1',
            'tool': 'workspace.write', 'arguments': {'api_key': 'private-value'}, 'capability': 'write'})
        with patch('src.service.list_tasks', return_value=[]), patch('src.service.list_swarms', return_value=[]):
            result = snapshot(self.store)
        self.assertEqual(result['summary']['waiting'], 1)
        self.assertEqual(result['pending'][0]['session_id'], 'one')
        self.assertEqual(result['pending'][0]['id'], 'call1')
        self.assertNotIn('private-value', json.dumps(result))

    def test_regular_agent_child_approvals_are_visible_in_tower(self):
        from src.control_tower import snapshot
        self.store.append('parent', 'user_message', {'text': 'Research'})
        self.store.append('parent', 'driver_paused', {'status': 'waiting_children'})
        self.store.register_sidechain('child', 'parent')
        meta = self.store.root / 'parent/children/child.meta.json'
        meta.write_text(json.dumps({'child': 'child', 'parent': 'parent'}), encoding='utf-8')
        self.store.append('child', 'user_message', {'text': 'A branch'})
        self.store.append('child', 'permission_requested', {'tool_call_id': 'child-call',
            'tool': 'workspace.write', 'arguments': {'path': 'result.md'}, 'capability': 'write'})
        with patch('src.service.list_tasks', return_value=[]), patch('src.service.list_swarms', return_value=[]):
            result = snapshot(self.store)
        self.assertEqual(result['summary']['waiting'], 1)
        self.assertEqual([(p['session_id'], p['id']) for p in result['pending']], [('child', 'child-call')])

    def test_swarm_parent_is_not_counted_twice(self):
        from src.control_tower import snapshot
        self.store.append('parent', 'user_message', {'request': 'Research'})
        self.store.append('parent', 'plan', {'mode': 'agent', 'steps': []})
        swarms = [{'plan_id': 'team', 'session_id': 'parent', 'request': 'Research',
                   'status': 'running', 'pending': []}]
        with patch('src.service.list_tasks', return_value=[]), patch('src.service.list_swarms', return_value=swarms):
            result = snapshot(self.store)
        self.assertEqual(result['summary']['running'], 1)
        self.assertEqual(result['swarms'][0]['id'], 'team')

    def test_usage_cache_refreshes_when_log_changes(self):
        from src import control_tower, logging_util
        model = self.root / 'model.jsonl'
        tool = self.root / 'tool.jsonl'
        with patch.object(logging_util, '_MODEL_LOG', model), patch.object(logging_util, '_TOOL_LOG', tool):
            control_tower.clear_usage_cache()
            self.assertEqual(control_tower.cached_usage()['model_calls'], 0)
            model.write_text(json.dumps({'status': 'success', 'prompt_tokens': 12,
                'completion_tokens': 7, 'usage_reported': True}) + '\n', encoding='utf-8')
            self.assertEqual(control_tower.cached_usage()['total_tokens'], 19)
            with patch.object(logging_util, 'usage_summary', side_effect=AssertionError('cache miss')):
                control_tower.cached_usage()['total_tokens'] = -1
                self.assertEqual(control_tower.cached_usage()['total_tokens'], 19)

    def test_commands_route_without_changing_request_text(self):
        from src.command_center import execute
        with patch('src.service.plan_swarm', return_value={'plan_id': 'p', 'status': 'planned', 'max_parallel': 4}) as plan:
            response = execute('/swarm plan 方法 A; 方法 B')
        self.assertTrue(response['ok'], response)
        self.assertEqual(plan.call_args.args[0], '方法 A; 方法 B')

    def test_unknown_swarm_action_is_rejected(self):
        from src.command_center import execute
        response = execute('/swarm launch-anything')
        self.assertFalse(response['ok'])


if __name__ == '__main__':
    unittest.main()
