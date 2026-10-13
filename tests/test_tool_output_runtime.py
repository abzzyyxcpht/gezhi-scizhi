"""Large tool observations stay readable without replaying their effects."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import llm, settings
from src.agent_runtime import AgentRuntime, ToolCall
from src.agent_driver import AdaptiveDriver
from tests.test_adaptive_driver import response


class ToolOutputRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for patch in [mock.patch.object(settings, '_PATH', self.root/'settings.json'),
                      mock.patch.object(llm, 'available', return_value=True)]:
            patch.start()
            self.addCleanup(patch.stop)
        self.runtime = AgentRuntime(session_root=self.root/'sessions', offline=False, llm_enabled=True)

    def test_large_output_logged_once_as_reference_and_model_reads_tail(self):
        original = 'x' * 100000 + '尾部证据'
        invoked = mock.Mock(return_value={'ok': True, 'text': original})
        self.runtime._register('fixture.large', 'read', 'large', invoked)
        seen = []
        def model(messages, **kwargs):
            if not seen:
                seen.append('large')
                return response(tool='fixture.large', call_id='large')
            if len(seen) == 1:
                observation = json.loads(messages[-1]['content'])
                self.assertTrue(observation['truncated'])
                ref = observation['output_ref']
                seen.append(ref)
                return response(tool='tool_output.search', call_id='search', args={'output_id': ref['output_id'], 'query': '尾部证据'})
            if len(seen) == 2:
                self.assertEqual('tool', messages[-1]['role'], messages[-1])
                found = json.loads(messages[-1]['content'])
                self.assertIn('尾部证据', found['matches'][0]['preview'])
                seen.append('read')
                return response(tool='tool_output.read', call_id='read', args={'output_id': seen[1]['output_id'], 'offset': found['matches'][0]['offset']})
            self.assertIn('尾部证据', json.loads(messages[-1]['content'])['text'])
            return response('已读取尾部，待核查真实来源')
        with mock.patch.object(llm, 'chat', side_effect=model):
            result = self.runtime.start('读取大工具输出并检查尾部')
        self.assertEqual('done', result['status'], result)
        invoked.assert_called_once()
        sid = result['session_id']
        event_file = self.runtime.sessions._path(sid)
        self.assertLess(event_file.stat().st_size, 24000)
        state = json.loads(AdaptiveDriver(self.runtime).path(sid).read_text('utf-8'))
        self.assertLess(len(json.dumps(state, ensure_ascii=False)), 40000)
        self.assertNotIn(original, event_file.read_text('utf-8'))

    def test_archive_failure_does_not_change_tool_success_or_reexecute(self):
        self.runtime._register('fixture.large', 'write', 'large', lambda _: {'ok': True, 'text': 'x'*20000})
        from src.tool_outputs import ToolOutputStore
        with mock.patch.object(ToolOutputStore, 'publish', side_effect=OSError('disk full')):
            result = self.runtime._execute_call('sid', ToolCall('c', 'fixture.large', {}, 'write'))
        self.assertTrue(result['ok'])
        self.assertTrue(result['output_unavailable'])
        self.assertNotIn('output_ref', result)
        self.assertLess(len(json.dumps(result)), 7000)

    def test_large_error_and_waiting_fields_remain_bounded_and_usable(self):
        self.runtime._register('fixture.failure', 'read', 'large error', lambda _: {'ok': False, 'error':'e'*20000})
        result = self.runtime._execute_call('sid', ToolCall('c', 'fixture.failure', {}, 'read'))
        self.assertFalse(result['ok'])
        self.assertLess(len(result['error']), 2000)
        self.runtime._register('fixture.wait', 'read', 'large wait', lambda _: {
            'ok':True,'status':'waiting_children','children_waiting':['child'], 'text':'x'*20000})
        waiting = self.runtime._execute_call('sid', ToolCall('c2','fixture.wait',{},'read'))
        self.assertEqual('waiting_children', waiting['status'])
        self.assertEqual(['child'], waiting['children_waiting'])

    def test_known_secrets_removed_before_storage_and_jsonl(self):
        secret = 'sk-' + 'sampleSecretValue'*3
        self.runtime._register('fixture.large','read','large',lambda _: {'ok':True,'api_key':secret,'text':'x'*20000+' Authorization: Bearer '+secret})
        result = self.runtime._execute_call('sid',ToolCall('c','fixture.large',{},'read'))
        self.assertIn('output_ref', result)
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(secret.encode(), path.read_bytes())

    def test_reader_can_be_denied_by_existing_permission_policy(self):
        self.assertIn('tool_output.read',self.runtime.tools)
        self.runtime.policy.decisions['tool_output.read']='deny'
        self.assertEqual('deny',self.runtime._agent_gate('sid','tool_output.read','read'))

    def test_shell_full_output_is_readable_by_opaque_reference(self):
        from src import tools_workspace
        from src.tool_outputs import ToolOutputStore
        import sys
        command = f'"{sys.executable}" -c "import os;os.write(1,b\'x\'*12000+b\'SHELL_TAIL\')"'
        with mock.patch.object(tools_workspace, 'DATA_DIR', self.root):
            result = self.runtime.tools['shell.run'].handler({'__session_id':'shell-session','command':command})
        ref = result['output_refs']['stdout']
        store = ToolOutputStore(self.runtime.sessions.root)
        found = store.search('shell-session', ref['output_id'], 'SHELL_TAIL')
        self.assertEqual(12000, found['matches'][0]['offset'])
        self.assertEqual('SHELL_TAIL',store.read('shell-session',ref['output_id'],offset=12000)['text'])

    def test_shell_timeout_retains_partial_diagnostics(self):
        from src import tools_workspace
        import sys
        command = f'"{sys.executable}" -u -c "import os,time;os.write(1,b\'partial stdout\');os.write(2,b\'partial stderr\');time.sleep(10)"'
        with mock.patch.object(tools_workspace,'DATA_DIR', self.root):
            result = self.runtime.tools['shell.run'].handler({'__session_id':'shell-session','command':command,'timeout_seconds':1})
        self.assertFalse(result['ok'])
        self.assertEqual('partial stdout',result['stdout'])
        self.assertEqual('partial stderr',result['stderr'])

    def test_legacy_saved_driver_receives_only_permitted_output_readers(self):
        self.runtime._register('fixture.wait','read','pause',lambda _: {'ok':True,'status':'waiting_task','task_id':'task'})
        with mock.patch.object(llm,'chat',return_value=response(tool='fixture.wait')):
            result = self.runtime.start('保存旧版本工具清单')
        sid = result['session_id']
        driver = AdaptiveDriver(self.runtime)
        state = json.loads(driver.path(sid).read_text('utf-8'))
        state['tool_schemas'] = [schema for schema in state['tool_schemas'] if not schema['function']['name'].startswith('tool_output.')]
        for schema in state['tool_schemas']:
            if schema['function']['name'] == 'file.read':
                schema['function']['parameters']['properties'].pop('byte_offset',None)
                schema['function']['parameters']['properties'].pop('byte_limit',None)
        driver._save(state)
        self.runtime.policy.decisions['tool_output.search'] = 'deny'
        migrated = driver._load(sid)
        names = {schema['function']['name'] for schema in migrated['tool_schemas']}
        self.assertIn('tool_output.read',names)
        self.assertIn('tool_output.list',names)
        self.assertNotIn('tool_output.search',names)
        file_schema = next(schema['function'] for schema in migrated['tool_schemas'] if schema['function']['name']=='file.read')
        self.assertIn('byte_offset',file_schema['parameters']['properties'])
        self.assertEqual(state['model_identity'],migrated['model_identity'])
