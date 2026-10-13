import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import config, settings, llm
from src.agent_runtime import AgentRuntime, SessionStore
from tests.test_adaptive_driver import response


class SessionWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for obj, key, value in [(config, 'DATA_DIR', self.root), (settings, '_PATH', self.root/'settings.json')]:
            p = patch.object(obj, key, value)
            p.start()
            self.addCleanup(p.stop)
        from src import session_workspace
        self.svc = session_workspace

    def test_draft_archive_restore_and_title_are_durable(self):
        sid = self.svc.handle({'cmd': 'chat_new', 'title': '对照实验'})['session_id']
        self.assertEqual('draft', self.svc.handle({'cmd': 'chat_get', 'session_id': sid})['session']['status'])
        self.svc.handle({'cmd': 'chat_archive', 'session_id': sid, 'archived': True})
        self.assertEqual([], self.svc.handle({'cmd': 'chat_sessions'})['sessions'])
        self.svc.handle({'cmd': 'chat_archive', 'session_id': sid, 'archived': False})
        self.svc.handle({'cmd': 'chat_rename', 'session_id': sid, 'title': '新的问题'})
        self.assertEqual('新的问题', self.svc.handle({'cmd': 'chat_sessions'})['sessions'][0]['title'])

    def test_fork_copies_visible_context_not_permissions_or_actions(self):
        sid = self.svc.handle({'cmd': 'chat_new'})['session_id']
        store = SessionStore(self.root/'sessions')
        store.append(sid, 'user_message', {'text': '# 假设\n增加样本是否改善方差？'})
        store.append(sid, 'assistant_message', {'content': '需要独立验证。'})
        store.append(sid, 'permission_granted', {'tool': 'danger', 'scope': 'allow_session'})
        store.append(sid, 'completed', {'status': 'done'})
        fork = self.svc.handle({'cmd': 'chat_fork', 'session_id': sid})
        self.assertTrue(fork['ok'], fork)
        events = store.read(fork['session_id'])
        self.assertFalse(any(e['type'] in {'permission_granted', 'tool_call', 'user_message'} for e in events))
        context = self.svc.handle({'cmd': 'chat_get', 'session_id': fork['session_id']})['session']
        self.assertEqual(sid, context['parent_id'])
        self.assertIn('独立验证', context['fork_context'])
        self.assertFalse((self.root/'sessions'/f'{fork["session_id"]}.driver.json').exists())

    def test_share_escapes_html_and_never_exports_private_driver(self):
        sid = self.svc.handle({'cmd': 'chat_new', 'title': '<script>alert(1)</script>'})['session_id']
        store = SessionStore(self.root/'sessions')
        store.append(sid, 'assistant_message', {'content': '<img src=x onerror=alert(1)>'})
        (self.root/'sessions'/f'{sid}.driver.json').write_text('{"private":"opaque-signature"}')
        result = self.svc.handle({'cmd': 'chat_share', 'session_id': sid, 'format': 'html'})
        text = Path(result['path']).read_text(encoding='utf-8')
        self.assertNotIn('<script>', text)
        self.assertNotIn('<img src=x', text)
        self.assertNotIn('opaque-signature', text)
        self.assertIn('&lt;script&gt;', text)

    def test_running_session_cannot_be_archived(self):
        sid = self.svc.handle({'cmd': 'chat_new'})['session_id']
        SessionStore(self.root/'sessions').append(sid, 'user_message', {'text': 'still running'})
        result = self.svc.handle({'cmd': 'chat_archive', 'session_id': sid, 'archived': True})
        self.assertFalse(result['ok'])

    def test_markdown_attachments_are_reread_and_bound_to_project(self):
        from src import project_workspace
        project_dir = self.root/'project'
        project_dir.mkdir()
        (project_dir/'notes.md').write_text('# 原始测量\n数值 1', encoding='utf-8')
        (project_dir/'GEZHI.md').write_text('报告置信区间', encoding='utf-8')
        project = project_workspace.handle({'cmd':'project_open','path':str(project_dir)})['project']
        sid = self.svc.handle({'cmd':'chat_new','project_id':project['id']})['session_id']
        self.assertTrue(self.svc.handle({'cmd':'chat_attach','session_id':sid,'path':'notes.md'})['ok'])
        (project_dir/'notes.md').write_text('# 原始测量\n数值 2', encoding='utf-8')
        runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=self.root/'sessions')
        with patch('src.bridge._runtime', return_value=runtime), patch.object(llm,'available',return_value=True), patch.object(llm,'chat',return_value=response('已核对材料')) as chat:
            result = self.svc.handle({'cmd':'chat_turn','session_id':sid,'request':'检查该 Markdown','online':True})
        self.assertTrue(result['ok'], result)
        sent = chat.call_args.args[0]
        self.assertIn('数值 2', str(sent))
        self.assertNotIn('数值 1', str(sent))
        self.assertIn('报告置信区间', str(sent))
        self.assertEqual('检查该 Markdown', self.svc.transcript(sid)[0]['content'])
        outside = self.root/'outside.md'
        outside.write_text('outside',encoding='utf-8')
        self.assertFalse(self.svc.handle({'cmd':'chat_attach','session_id':sid,'path':str(outside)})['ok'])

    def test_native_pending_turn_cannot_be_replaced_or_inherit_new_role(self):
        sid = self.svc.handle({'cmd':'chat_new'})['session_id']
        runtime = AgentRuntime(offline=False,llm_enabled=True,session_root=self.root/'sessions')
        runtime._register('test.write','write','write',lambda _: {'ok':True},runtime._obj_schema({}))
        with patch.object(llm,'available',return_value=True), patch.object(llm,'chat',return_value=response(tool='test.write')):
            runtime.send(sid,'先授权')
            with self.assertRaises(ValueError):
                runtime.send(sid,'跳过授权')
        self.assertEqual('waiting_approval', runtime.sessions.snapshot(sid)['status'])

    def test_missing_attachment_can_be_removed_without_reading_it(self):
        from src import project_workspace
        folder = self.root/'research'
        folder.mkdir()
        file = folder/'notes.md'
        file.write_text('temporary material', encoding='utf-8')
        pid = project_workspace.handle({'cmd':'project_open','path':str(folder)})['project']['id']
        sid = self.svc.handle({'cmd':'chat_new','project_id':pid})['session_id']
        self.svc.handle({'cmd':'chat_attach','session_id':sid,'path':'notes.md'})
        file.unlink()
        result = self.svc.handle({'cmd':'chat_detach','session_id':sid,'path':'notes.md'})
        self.assertTrue(result['ok'], result)
        self.assertEqual([], result['session']['attachments'])

    def test_first_native_turn_after_offline_keeps_unchanged_material(self):
        from src import project_workspace
        folder = self.root/'research'
        folder.mkdir()
        (folder/'notes.md').write_text('研究材料：分层抽样', encoding='utf-8')
        pid = project_workspace.handle({'cmd':'project_open','path':str(folder)})['project']['id']
        sid = self.svc.handle({'cmd':'chat_new','project_id':pid})['session_id']
        self.svc.handle({'cmd':'chat_attach','session_id':sid,'path':'notes.md'})
        _, manifest = project_workspace.attachment_context(pid,['notes.md'])
        store = SessionStore(self.root/'sessions')
        store.append(sid, 'user_message', {'text':'离线计划'})
        store.append(sid, 'attachments_snapshot', {'files':manifest})
        store.append(sid, 'completed', {'status':'done'})
        runtime = AgentRuntime(offline=False,llm_enabled=True,session_root=self.root/'sessions')
        with patch('src.bridge._runtime', return_value=runtime), patch.object(llm,'available',return_value=True), patch.object(llm,'chat',return_value=response('方法已核对')) as chat:
            result = self.svc.handle({'cmd':'chat_turn','session_id':sid,'request':'核对方法','online':True})
        self.assertTrue(result['ok'], result)
        self.assertIn('分层抽样', str(chat.call_args.args[0]))

    def test_native_followup_keeps_prefix_and_approval_works_after_first_completion(self):
        sid = self.svc.handle({'cmd': 'chat_new'})['session_id']
        runtime = AgentRuntime(offline=False, llm_enabled=True, session_root=self.root/'sessions')
        runtime._register('test.write', 'write', 'write', lambda _: {'ok': True}, runtime._obj_schema({}))
        with patch.object(llm, 'available', return_value=True), patch.object(llm, 'chat', side_effect=[response('第一轮答复'), response(tool='test.write', call_id='second-call'), response('已完成')]) as chat:
            first = runtime.send(sid, '# 研究问题\n先讨论方法')
            prefix = json.loads((self.root/'sessions'/f'{sid}.driver.json').read_text(encoding='utf-8'))['messages']
            second = runtime.send(sid, '现在保存结果')
            self.assertEqual('waiting_approval', second['status'])
            sent = chat.call_args.args[0]
            self.assertEqual(prefix, sent[:len(prefix)])
            final = runtime.approve(sid, 'second-call', 'allow')
        self.assertEqual('done', first['status'])
        self.assertEqual('done', final['status'])
        self.assertEqual(2, len([e for e in runtime.sessions.read(sid) if e['type']=='user_message']))
        with self.assertRaises(ValueError):
            runtime.continue_session(sid)

    def test_fork_first_turn_mode_overrides_draft_metadata_for_followup(self):
        parent = self.svc.handle({'cmd': 'chat_new', 'workspace_mode': 'general',
                                  'agent': 'general', 'mode': 'plan'})['session_id']
        forked = self.svc.handle({'cmd': 'chat_fork', 'session_id': parent})
        sid = forked['session_id']
        runtime = AgentRuntime(offline=True, llm_enabled=False, session_root=self.root/'sessions')
        with patch('src.bridge._runtime', return_value=runtime):
            first = self.svc.handle({'cmd': 'chat_turn', 'session_id': sid,
                                     'request': '读取工作区', 'mode': 'agent'})
            self.assertTrue(first['ok'], first)
            saved = self.svc.handle({'cmd': 'chat_get', 'session_id': sid})['session']
            self.assertEqual('agent', saved['mode'])
            second = self.svc.handle({'cmd': 'chat_turn', 'session_id': sid,
                                      'request': '继续读取工作区'})
        self.assertTrue(second['ok'], second)


if __name__ == '__main__':
    unittest.main()
