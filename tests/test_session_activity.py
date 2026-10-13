"""Read-only, bounded activity follows journal facts rather than child metadata."""
from __future__ import annotations

import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.agent_runtime import SessionStore
from src.state_store import write_json


class SessionActivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(Path(self.tmp.name) / 'sessions')
        self.sid = 'activity01'
        self.store.append(self.sid, 'user_message', {'text': 'Research goal', 'agent': 'research'})

    def activity(self, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('src.session_activity'),
                             'The bounded activity API is not implemented')
        module = importlib.import_module('src.session_activity')
        return module.session_activity(kwargs.pop('sid', self.sid), store=self.store, **kwargs)

    def child(self, parent, sid, *, status='running', meta_status='done', agent='critic'):
        self.store.register_sidechain(sid, parent)
        write_json(self.store.root / parent / 'children' / f'{sid}.meta.json', {
            'schema_version': 1, 'child': sid, 'parent': parent, 'agent': agent,
            'request': 'Review evidence api_key=private-child-key', 'status': meta_status,
            'steps': 999, 'tokens': 999})
        self.store.append(sid, 'user_message', {
            'text': 'Review evidence api_key=private-child-key', 'agent': agent})
        if status == 'waiting_approval':
            self.store.append(sid, 'permission_requested', {'tool_call_id': 'approve1',
                'tool': 'file.write', 'arguments': {'password': 'private-approval'}})
        elif status != 'running':
            self.store.append(sid, 'completed', {'status': status})

    def bytes(self):
        return {str(path.relative_to(self.store.root)): path.read_bytes()
                for path in self.store.root.rglob('*') if path.is_file()}

    def test_tool_lifecycle_is_folded_and_arguments_remain_private(self):
        self.store.append(self.sid, 'tool_call', {'id': 'tool1', 'tool': 'file.read',
            'arguments': {'path': 'private-path', 'api_key': 'private-tool-key'}})
        self.store.append(self.sid, 'tool_result', {'tool_call_id': 'tool1', 'tool': 'file.read',
            'result': {'ok': False, 'error': 'timeout token=private-error-key'}})
        result = self.activity()
        tools = [item for item in result['items'] if item['type'] == 'tool']
        self.assertEqual(1, len(tools))
        self.assertEqual('error', tools[0]['status'])
        self.assertEqual('file.read', tools[0]['label'])
        self.assertEqual(tools[0]['id'], self.activity()['items'][0]['id'])
        self.assertEqual('error', result['status'])
        rendered = json.dumps(result)
        for secret in ('private-path', 'private-tool-key', 'private-error-key'):
            self.assertNotIn(secret, rendered)

    def test_pending_tool_and_stage_are_actual_logged_work(self):
        self.store.append(self.sid, 'driver_started', {'driver': 'adaptive'})
        self.store.append(self.sid, 'tool_call', {'id': 'a', 'tool': 'web.search'})
        self.store.append(self.sid, 'permission_requested', {'tool_call_id': 'b',
            'tool': 'file.write', 'arguments': {'secret': 'private'}})
        result = self.activity()
        tools = {item['label']: item for item in result['items'] if item['type'] == 'tool'}
        self.assertEqual('running', tools['web.search']['status'])
        self.assertEqual('waiting_approval', tools['file.write']['status'])
        self.assertTrue(any(item['type'] == 'stage' for item in result['items']))
        self.assertEqual('waiting_approval', result['status'])

    def test_cancel_and_partial_results_do_not_become_success(self):
        self.store.append(self.sid, 'tool_result', {'tool_call_id': 'a', 'tool': 'fanout',
            'result': {'ok': True, 'status': 'partial'}})
        self.store.append(self.sid, 'cancel_requested', {'message': 'Stop safely'})
        result = self.activity()
        self.assertEqual('partial', next(item for item in result['items'] if item['type'] == 'tool')['status'])
        self.assertEqual('cancelling', result['status'])

    def test_error_result_cannot_override_failure_with_done_status(self):
        self.store.append(self.sid, 'tool_result', {'tool_call_id': 'a', 'tool': 'file.read',
            'result': {'ok': False, 'status': 'done', 'error': 'Failed to archive'}})
        result = self.activity()
        self.assertEqual('error', result['items'][0]['status'])

    def test_calls_with_reused_ids_remain_separate_across_user_turns(self):
        self.store.append(self.sid, 'tool_call', {'id': 'reused', 'tool': 'file.read'})
        self.store.append(self.sid, 'tool_result', {'tool_call_id': 'reused',
            'result': {'ok': True}})
        self.store.append(self.sid, 'user_message', {'text': 'Next research turn'})
        self.store.append(self.sid, 'tool_call', {'id': 'reused', 'tool': 'file.write'})
        result = self.activity()
        tools = [item for item in result['items'] if item['type'] == 'tool']
        self.assertEqual(2, len(tools))
        self.assertEqual(2, len({item['id'] for item in tools}))

    def test_current_turn_tool_id_survives_its_turn_marker_leaving_tail(self):
        self.store.append(self.sid, 'turn_started', {'turn_id': 'stable-turn'})
        self.store.append(self.sid, 'tool_call', {'id': 'stable-call', 'tool': 'shell.run'})
        before = self.activity(limit=2)['items'][-1]['id']
        for index in range(40):
            self.store.append(self.sid, 'shell_output', {'tool_call_id': 'stable-call', 'text': str(index)})
        after = self.activity(limit=2)['items'][-1]['id']
        self.assertEqual(before, after)

    def test_default_missing_store_does_not_create_a_directory(self):
        missing = Path(self.tmp.name) / 'missing'
        self.assertIsNotNone(importlib.util.find_spec('src.session_activity'))
        module = importlib.import_module('src.session_activity')
        with mock.patch('src.config.DATA_DIR', missing), mock.patch('src.agent_runtime.DATA_DIR', missing):
            with self.assertRaises(ValueError):
                module.session_activity('does-not-exist')
        self.assertFalse(missing.exists())

    def test_linked_child_directory_is_not_followed(self):
        external = Path(self.tmp.name) / 'external'
        external.mkdir()
        write_json(external / 'leaked.meta.json', {
            'schema_version': 1, 'child': 'leaked', 'parent': self.sid,
            'request': 'private-linked-goal', 'status': 'done'})
        parent = self.store.root / self.sid
        parent.mkdir()
        try:
            (parent / 'children').symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest('This Windows account cannot create directory symlinks')
        result = self.activity()
        self.assertEqual([], result['children'])
        self.assertTrue(result['truncated'])
        self.assertNotIn('private-linked-goal', json.dumps(result))

    def test_reparse_child_directory_is_rejected_without_link_privilege(self):
        import stat
        from types import SimpleNamespace
        self.child(self.sid, 'child01', status='done')
        directory = self.store.root / self.sid / 'children'
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            if path == directory:
                return SimpleNamespace(st_mode=stat.S_IFDIR,
                                       st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'lstat', lstat):
            result = self.activity()
        self.assertEqual([], result['children'])
        self.assertTrue(result['truncated'])

    def test_terminal_session_does_not_leave_stage_rows_running(self):
        self.store.append(self.sid, 'driver_started', {'driver': 'adaptive'})
        self.store.append(self.sid, 'completed', {'status': 'cancelled'})
        stages = [item for item in self.activity()['items'] if item['type'] == 'stage']
        self.assertNotIn('running', [item['status'] for item in stages])
        self.assertNotIn('done', [item['status'] for item in stages])

    def test_recursive_children_follow_snapshot_not_metadata_and_read_is_pure(self):
        self.child(self.sid, 'child01', status='waiting_approval')
        self.child('child01', 'grandchild01', status='cancelled')
        before = self.bytes()
        result = self.activity()
        self.assertEqual(before, self.bytes())
        by_id = {row['session_id']: row for row in result['children']}
        self.assertEqual('waiting_approval', by_id['child01']['status'])
        self.assertEqual({'id': 'approve1', 'tool': 'file.write'}, by_id['child01']['pending'])
        self.assertEqual(1, by_id['child01']['depth'])
        self.assertEqual('child01', by_id['grandchild01']['parent_id'])
        self.assertEqual('cancelled', by_id['grandchild01']['status'])
        self.assertEqual(2, by_id['grandchild01']['depth'])
        self.assertIsNone(by_id['child01']['tokens'])
        self.assertNotIn('999', json.dumps(result))
        self.assertNotIn('private-child-key', json.dumps(result))
        self.assertNotIn('private-approval', json.dumps(result))

    def test_fresh_store_resolves_sidechains_without_registration_or_writes(self):
        self.child(self.sid, 'child01', status='error')
        before = self.bytes()
        self.store = SessionStore(self.store.root)
        with mock.patch.object(self.store, 'register_sidechain', side_effect=AssertionError('write')):
            result = self.activity()
        self.assertEqual('error', result['children'][0]['status'])
        self.assertEqual(before, self.bytes())

    def test_child_usage_unknown_stays_unknown_and_reported_is_counted(self):
        self.child(self.sid, 'child01')
        self.store.append('child01', 'assistant_message', {'step': 1, 'usage': {
            'prompt_tokens': None, 'completion_tokens': None, 'usage_reported': False}})
        self.assertIsNone(self.activity()['children'][0]['tokens'])
        self.child(self.sid, 'child02')
        self.store.append('child02', 'assistant_message', {'step': 1, 'usage': {
            'prompt_tokens': 12, 'completion_tokens': 8, 'usage_reported': True}})
        self.store.append('child02', 'context_summary_usage', {'usage': {
            'prompt_tokens': 4, 'completion_tokens': 6, 'usage_reported': True}})
        rows = {row['session_id']: row for row in self.activity()['children']}
        self.assertEqual(1, rows['child02']['steps'])
        self.assertEqual(30, rows['child02']['tokens'])

    def test_missing_or_corrupt_child_metadata_is_unknown(self):
        directory = self.store.root / self.sid / 'children'
        directory.mkdir(parents=True)
        (directory / 'lost01.meta.json').write_text('{"status":"done"}', encoding='utf-8')
        (directory / 'broken01.meta.json').write_text('password=private-broken', encoding='utf-8')
        result = self.activity()
        self.assertEqual({'unknown'}, {row['status'] for row in result['children']})
        self.assertEqual({'lost01', 'broken01'}, {row['session_id'] for row in result['children']})
        self.assertNotIn('private-broken', json.dumps(result))

    def test_corrupt_child_journal_cannot_become_metadata_success(self):
        self.child(self.sid, 'child01', status='done')
        self.store._path('child01').write_text('{"payload":"bad-record"}\n', encoding='utf-8')
        self.assertEqual('unknown', self.activity()['children'][0]['status'])

    def test_limit_and_depth_cycle_protection_are_explicit(self):
        for index in range(7):
            self.store.append(self.sid, 'tool_result', {'tool_call_id': f'call{index}',
                'tool': 'file.read', 'result': {'ok': True}})
            self.child(self.sid, f'child{index}')
        result = self.activity(limit=3)
        self.assertLessEqual(len(result['items']), 3)
        self.assertLessEqual(len(result['children']), 3)
        self.assertTrue(result['truncated'])
        parent = 'child0'
        for index in range(8):
            child = f'deep{index}'
            self.child(parent, child)
            parent = child
        self.assertLessEqual(max(row['depth'] for row in self.activity()['children']), 4)
        write_json(self.store.root / parent / 'children' / f'{self.sid}.meta.json', {
            'schema_version': 1, 'child': self.sid, 'parent': parent})
        self.assertNotIn(self.sid, [row['session_id'] for row in self.activity()['children']])

    def test_bounded_event_page_is_used_instead_of_execution_read(self):
        for index in range(250):
            self.store.append(self.sid, 'tool_result', {'tool_call_id': f'call{index}',
                'tool': 'file.read', 'result': {'ok': True}})
        with mock.patch.object(self.store, 'read', side_effect=AssertionError('full execution read')):
            with mock.patch.object(self.store, 'event_page', wraps=self.store.event_page) as page:
                result = self.activity(limit=2)
        self.assertLessEqual(len(result['items']), 2)
        self.assertTrue(result['truncated'])
        self.assertTrue(all(call.kwargs.get('tail') is True and call.kwargs['limit'] <= 500
                            for call in page.call_args_list))

    def test_ids_limits_and_poisoned_sidechain_parent_do_not_escape_root(self):
        for sid in ('../private', '', 'x' * 129):
            with self.assertRaises(ValueError):
                self.activity(sid=sid)
        for limit in (0, -1, True, 101, 1.5):
            with self.assertRaises(ValueError):
                self.activity(limit=limit)
        external = Path(self.tmp.name) / 'external'
        external.mkdir()
        (external / 'children').mkdir()
        (external / 'children' / 'bad01.jsonl').write_text('{}\n', encoding='utf-8')
        self.store._sidechain_parents['bad01'] = '../external'
        with self.assertRaises(ValueError):
            self.activity(sid='bad01')


if __name__ == '__main__':
    unittest.main()
