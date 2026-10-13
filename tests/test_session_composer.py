"""Native session actions against real project/session storage, without a model."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import flet as ft
from src import config, settings, project_workspace, session_workspace
from src.ui import session_composer as ui


class SessionComposerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for obj, name, value in [(config,'DATA_DIR',self.root), (settings,'_PATH',self.root/'settings.json')]:
            mock = patch.object(obj,name,value)
            mock.start()
            self.addCleanup(mock.stop)
        folder = self.root/'project'
        folder.mkdir()
        (folder/'notes.md').write_text('# 研究笔记\n检验反例。',encoding='utf-8')
        self.pid = project_workspace.handle({'cmd':'project_open','path':str(folder)})['project']['id']
        self.sid = None
        self.work = []
        self.dialogs = []
        page = SimpleNamespace(update=lambda:None, show_dialog=self.dialogs.append, services=[],
                               run_thread=self.work.append, pop_dialog=lambda:None)
        self.request = ft.TextField()
        self.ui = ui.SessionComposer(page,self.request,lambda:self.sid,lambda:self.pid,self.open,lambda:None)

    @staticmethod
    def event(kind, **payload):
        return {'type': kind, 'payload': payload}

    def test_picker_is_registered_once_on_real_page_contract(self):
        self.assertEqual([self.ui.file_picker], self.ui.page.services)

    def test_paged_refresh_uses_metadata_and_only_the_tail_page(self):
        self.ui.new()
        events = [self.event('user_message', text=f'最近问题 {i}') for i in range(200)]
        provider = Mock(return_value={'events': events, 'cursor': 'tail-200', 'reset': False, 'has_more': False})
        self.ui.event_page_provider = provider
        self.ui.events_provider = Mock(side_effect=AssertionError('must not read full history'))
        changed = Mock()
        self.ui.events_changed = changed
        metadata = {'ok': True, 'session': {'title': '长研究', 'archived': False, 'attachments': []}}
        with patch.object(ui, 'handle', return_value=metadata) as backend:
            self.ui.refresh()
        backend.assert_called_once_with({'cmd': 'chat_get', 'session_id': self.sid, 'metadata_only': True})
        provider.assert_called_once_with(self.sid, cursor=None, limit=200, tail=True)
        self.assertEqual(200, len(self.ui.transcript.controls))
        self.ui.events_provider.assert_not_called()
        self.assertEqual(events, changed.call_args.args[0])

    def test_paged_idle_poll_does_not_rebuild_or_update_the_page(self):
        self.ui.new()
        self.ui.event_page_provider = Mock(side_effect=[
            {'events': [self.event('user_message', text='问题')], 'cursor': 'one', 'reset': False, 'has_more': False},
            {'events': [], 'cursor': 'one', 'reset': False, 'has_more': False},
        ])
        self.ui.refresh()
        controls = self.ui.transcript.controls[:]
        with patch.object(self.ui.page, 'update') as update:
            self.ui.consume_events()
        update.assert_not_called()
        self.assertEqual(controls, self.ui.transcript.controls)

    def test_paged_reset_replaces_old_controls_and_bounded_backlog_reads_once(self):
        self.ui.new()
        self.ui.event_page_provider = Mock(side_effect=[
            {'events': [self.event('user_message', text='旧问题')], 'cursor': 'old', 'reset': False, 'has_more': False},
            {'events': [self.event('user_message', text='新问题')], 'cursor': 'new', 'reset': True, 'has_more': True},
            {'events': [self.event('assistant_message', content='新回答')], 'cursor': 'newer', 'reset': False, 'has_more': False},
        ])
        self.ui.refresh()
        self.ui.consume_events()
        self.assertEqual(2, self.ui.event_page_provider.call_count)
        self.assertTrue(self.ui.has_more_events)
        self.assertEqual(['新问题'], [c.content.controls[-1].value for c in self.ui.transcript.controls])
        self.ui.consume_events()
        self.assertFalse(self.ui.has_more_events)
        self.assertEqual(['新问题', '新回答'], [c.content.controls[-1].value for c in self.ui.transcript.controls])

    def test_pure_operation_event_updates_shared_log_without_a_message_bubble(self):
        self.ui.new()
        self.ui.event_page_provider = Mock(side_effect=[
            {'events': [], 'cursor': 'first', 'reset': False, 'has_more': False},
            {'events': [self.event('permission_requested', tool='file.write')],
             'cursor': 'second', 'reset': False, 'has_more': False},
        ])
        self.ui.events_changed = Mock()
        self.ui.refresh()
        self.ui.events_changed.reset_mock()
        with patch.object(self.ui.page, 'update') as update:
            self.ui.consume_events()
        self.ui.events_changed.assert_called_once()
        update.assert_called_once()
        self.assertEqual([], self.ui.transcript.controls)

    def test_same_session_refresh_discards_older_page_response(self):
        self.ui.new()
        self.ui.event_page_provider = Mock(return_value={
            'events': [], 'cursor': 'first', 'reset': False, 'has_more': False})
        self.ui.refresh()
        def late_page(*args, **kwargs):
            self.ui.event_page_provider = Mock(return_value={
                'events': [self.event('user_message', text='新视图')], 'cursor': 'fresh', 'reset': True, 'has_more': False})
            self.ui.refresh()
            return {'events': [self.event('user_message', text='迟到内容')], 'cursor': 'late', 'reset': False, 'has_more': False}
        self.ui.event_page_provider = late_page
        self.ui.consume_events()
        self.assertEqual(['新视图'], [c.content.controls[-1].value for c in self.ui.transcript.controls])

    def test_failed_metadata_for_another_session_clears_previous_operation_log(self):
        self.ui.new()
        self.ui.event_page_provider = Mock(return_value={
            'events': [self.event('user_message', text='会话甲')], 'cursor': 'one', 'reset': False, 'has_more': False})
        self.ui.events_changed = Mock()
        self.ui.refresh()
        self.sid = 'deleted-session-b'
        with patch.object(ui, 'handle', return_value={'ok': False, 'error': '会话不存在'}):
            self.ui.refresh()
        self.assertEqual([], self.ui.recent_events)
        self.assertEqual([], self.ui.transcript.controls)
        self.assertEqual([], self.ui.events_changed.call_args.args[0])

    def test_refresh_read_error_in_same_session_keeps_last_good_view(self):
        self.ui.new()
        self.ui.event_page_provider = Mock(return_value={
            'events': [self.event('user_message', text='保留内容')], 'cursor': 'one', 'reset': False, 'has_more': False})
        self.ui.refresh()
        original = self.ui.transcript.controls[:]
        with patch.object(ui, 'handle', return_value={'ok': False, 'error': '暂不可读'}):
            self.ui.refresh()
        self.assertEqual(original, self.ui.transcript.controls)
        self.ui.event_page_provider.side_effect = OSError('temporary read failure')
        self.ui.refresh()
        self.assertEqual(original, self.ui.transcript.controls)

    def test_metadata_only_chat_get_does_not_read_or_project_session_history(self):
        self.ui.new()
        with patch.object(session_workspace, '_store') as store:
            store.return_value.read.side_effect = AssertionError('full history read')
            store.return_value.snapshot.side_effect = AssertionError('full projection')
            result = session_workspace.handle({'cmd': 'chat_get', 'session_id': self.sid, 'metadata_only': True})
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.sid, result['session']['session_id'])
        self.assertNotIn('events', result)
        self.assertNotIn('messages', result)

    def test_reused_step_number_keeps_each_turn_in_its_own_bubble(self):
        event = self.event
        self.ui._render_events([
            event('user_message', text='第一问'),
            event('assistant_delta', step=1, delta='第一'),
            event('assistant_message', step=1, content='第一份结论'),
            event('user_message', text='第二问'),
            event('assistant_delta', step=1, delta='第二'),
            event('assistant_message', step=1, content='第二份结论'),
        ])
        messages = [c.content.controls[-1].value for c in self.ui.transcript.controls]
        self.assertEqual(['第一问', '第一份结论', '第二问', '第二份结论'], messages)

    def test_shell_output_updates_tool_before_result_without_assistant_bubble(self):
        event = self.event
        self.ui._render_events([event('tool_call', id='one', name='shell.run', arguments={})])
        tile = self.ui.transcript.controls[0]
        title = tile.title.value
        changed = self.ui._render_events([event('shell_output', tool_call_id='one',
            stream='stdout', text='early output\n', partial=True)])
        self.assertTrue(changed)
        self.assertEqual([tile], self.ui.transcript.controls)
        self.assertIsInstance(tile.subtitle, ft.Text)
        self.assertIn('early output', tile.subtitle.value)
        self.assertTrue(tile.subtitle.visible)
        self.assertEqual(8, tile.subtitle.max_lines)
        self.assertEqual(title, tile.title.value, 'progress must preserve the running title')
        self.ui._render_events([event('shell_output', tool_call_id='one',
            stream='stderr', text='warning\n', partial=True)])
        self.assertIn('early output\nwarning', tile.subtitle.value)
        self.ui._render_events([event('tool_result', id='one', result={'ok': True, 'exit_code': 0})])
        self.assertTrue(tile.title.value.startswith('✔'))
        before = tile.subtitle.value
        self.assertFalse(self.ui._render_events([event('shell_output', tool_call_id='one',
            stream='stdout', text='late\n', partial=True)]))
        self.assertEqual(before, tile.subtitle.value)

    def test_shell_output_calls_and_reused_ids_in_new_turn_remain_separate(self):
        event = self.event
        self.ui._render_events([event('tool_call', id='one', name='shell.run'),
            event('tool_call', id='two', name='shell.run'),
            event('shell_output', tool_call_id='one', stream='stdout', text='one only\n'),
            event('shell_output', tool_call_id='two', stream='stdout', text='two only\n')])
        first, second = self.ui.transcript.controls
        self.assertEqual('one only\n', first.subtitle.value)
        self.assertEqual('two only\n', second.subtitle.value)
        self.ui._render_events([event('user_message', text='new turn'),
            event('shell_output', tool_call_id='one', stream='stdout', text='orphan\n'),
            event('tool_result', id='one', result={'ok': True})])
        self.assertEqual('one only\n', first.subtitle.value)
        self.assertTrue(first.title.value.startswith('⚙'))
        self.ui._render_events([event('tool_call', id='one', name='shell.run'),
            event('shell_output', tool_call_id='one', stream='stdout', text='new only\n')])
        self.assertEqual('new only\n', self.ui.transcript.controls[-1].subtitle.value)

    def test_shell_output_bounds_text_and_ignores_missing_tail_call(self):
        event = self.event
        self.assertFalse(self.ui._render_events([event('shell_output', tool_call_id='missing',
            stream='stdout', text='tail without call\n')]))
        self.assertEqual([], self.ui.transcript.controls)
        self.ui._render_events([event('tool_call', id='one', name='shell.run'),
            event('shell_output', tool_call_id='one', stream='stdout',
                  text='old prefix\n' + 'x' * 16000 + '\nretained tail\n')])
        output = self.ui.transcript.controls[0].subtitle
        self.assertLessEqual(len(output.value), 1000)
        self.assertNotIn('old prefix', output.value)
        self.assertIn('retained tail', output.value)
        self.ui._reset_event_view()
        self.assertFalse(self.ui._render_events([event('shell_output', tool_call_id='one',
            stream='stdout', text='stale after reset\n')]))
        self.assertEqual([], self.ui.transcript.controls)

    def test_shell_output_index_is_trimmed_with_transcript(self):
        event = self.event
        self.ui._render_events([event('tool_call', id='old', name='shell.run'),
            event('shell_output', tool_call_id='old', stream='stdout', text='old output\n')])
        self.ui._render_events([event('tool_call', id=f'call-{index}', name='shell.run')
                               for index in range(ui.TRANSCRIPT_LIMIT)])
        self.assertEqual(ui.TRANSCRIPT_LIMIT, len(self.ui.tool_rows))
        self.assertFalse(self.ui._render_events([event('shell_output', tool_call_id='old',
            stream='stdout', text='evicted row\n')]))

    def test_shell_nonzero_exit_does_not_render_as_success(self):
        event = self.event
        self.ui._render_events([event('tool_call', id='one', name='shell.run'),
            event('tool_result', id='one', result={'ok': True, 'exit_code': 7})])
        self.assertTrue(self.ui.transcript.controls[0].title.value.startswith('✘'))

    def test_paged_shell_progress_updates_same_control_without_operation_log_rows(self):
        event = self.event
        self.ui.new()
        self.ui.event_page_provider = Mock(side_effect=[
            {'events': [event('tool_call', id='one', name='shell.run')], 'cursor': 'call'},
            {'events': [event('shell_output', tool_call_id='one', stream='stdout',
                             text='early via poll\n', partial=True)], 'cursor': 'output'},
            {'events': [event('tool_result', id='one', result={'ok': True, 'exit_code': 0})],
             'cursor': 'result'},
        ])
        self.ui.events_changed = Mock()
        self.ui.refresh()
        tile = self.ui.transcript.controls[0]
        self.ui.events_changed.reset_mock()
        with patch.object(self.ui.page, 'update') as update:
            self.ui.consume_events()
        update.assert_called_once()
        self.ui.events_changed.assert_not_called()
        self.assertEqual('output', self.ui._page_cursor)
        self.assertIs(tile, self.ui.transcript.controls[0])
        self.assertEqual('early via poll\n', tile.subtitle.value)
        self.assertTrue(tile.title.value.startswith('⚙'))
        self.ui.consume_events()
        self.assertIs(tile, self.ui.transcript.controls[0])
        self.assertTrue(tile.title.value.startswith('✔'))
        self.assertEqual(['tool_call', 'tool_result'], [e['type'] for e in self.ui.recent_events])

    def test_shell_progress_is_plain_text_redacted_and_empty_events_are_noops(self):
        event = self.event
        self.ui._render_events([event('tool_call', id='one', name='shell.run'),
            event('shell_output', tool_call_id='one', stream='stdout',
                  text='**plain output**\napi_key=private-fixture\n')])
        tile = self.ui.transcript.controls[0]
        self.assertIsInstance(tile.subtitle, ft.Text)
        self.assertIn('**plain output**', tile.subtitle.value)
        self.assertNotIn('private-fixture', tile.subtitle.value)
        before = tile.subtitle.value
        self.assertFalse(self.ui._render_events([
            event('shell_output', tool_call_id='one', stream='stdout', text=''),
            event('shell_output', tool_call_id='one', stream='other', text='unsupported'),
        ]))
        self.assertEqual(before, tile.subtitle.value)

    def test_final_message_for_other_step_does_not_replace_open_stream(self):
        event = self.event
        self.ui._render_events([event('user_message', text='问题'),
            event('assistant_delta', step=1, delta='部分回复'),
            event('assistant_message', step=2, content='下一步回复')])
        self.assertEqual(3, len(self.ui.transcript.controls))
        self.assertEqual('部分回复', self.ui.transcript.controls[1].content.controls[-1].value)

    def test_rewritten_log_with_same_size_rebuilds_stream(self):
        self.ui.new()
        original = [self.event('user_message', text='旧问题'),
                    self.event('assistant_message', step=1, content='旧回答')]
        replacement = [self.event('user_message', text='新问题'),
                       self.event('assistant_message', step=1, content='新回答')]
        self.ui.events_provider = lambda sid: original
        self.ui.refresh()
        self.ui.events_provider = lambda sid: replacement
        self.ui.consume_events()
        self.assertEqual('新问题', self.ui.transcript.controls[0].content.controls[-1].value)
        self.assertEqual('新回答', self.ui.transcript.controls[1].content.controls[-1].value)

    def test_shorter_log_after_rewind_rebuilds_cursor(self):
        self.ui.new()
        original = [self.event('user_message', text='问题'),
                    self.event('assistant_message', step=1, content='已撤回')]
        self.ui.events_provider = lambda sid: original
        self.ui.refresh()
        self.ui.events_provider = lambda sid: []
        self.ui.consume_events()
        self.assertEqual(0, self.ui.event_cursor)
        self.assertEqual([], self.ui.transcript.controls)

    def test_switched_session_discards_late_provider_response(self):
        self.ui.new()
        original = [self.event('user_message', text='原会话')]
        self.ui.events_provider = lambda sid: original
        self.ui.refresh()
        def changed_selection(sid):
            self.sid = 'another-session'
            return original + [self.event('assistant_message', step=1, content='旧会话迟到回复')]
        self.ui.events_provider = changed_selection
        self.ui.consume_events()
        self.assertEqual(1, len(self.ui.transcript.controls))

    def test_full_history_is_paged_without_losing_older_messages(self):
        self.ui.new()
        for index in range(95):
            session_workspace._store().append(self.sid, 'user_message', {'text': f'研究问题 {index}'})
        with patch.object(session_workspace, 'transcript', side_effect=AssertionError('must not load full transcript')):
            self.ui.show_history()
            self.work.pop(0)()
        dialog = self.dialogs[-1]
        _, rows, label, navigation = dialog.content.content.controls
        self.assertLessEqual(len(rows.controls), 40)
        self.assertEqual('研究问题 80', rows.controls[0].content.controls[-1].value)
        navigation.controls[0].on_click(None)
        self.work.pop(0)()
        self.assertEqual('研究问题 40', rows.controls[0].content.controls[-1].value)
        navigation.controls[0].on_click(None)
        self.work.pop(0)()
        self.assertEqual('研究问题 0', rows.controls[0].content.controls[-1].value)
        self.assertTrue(navigation.controls[0].disabled)

    def test_history_search_filters_index_and_keeps_paging_and_clear(self):
        self.ui.new()
        for index in range(95):
            session_workspace._store().append(self.sid, 'user_message', {'text': f'{"Needle" if index % 2 == 0 else "Other"} 研究问题 {index}'})
        self.ui.show_history()
        self.work.pop(0)()
        search, rows, label, navigation = self.dialogs[-1].content.content.controls
        query = search.controls[0]
        query.value = 'needle'
        query.on_submit(None)
        self.work.pop(0)()
        self.assertIn('命中 48', label.value)
        self.assertEqual(40, len(rows.controls))
        self.assertTrue(all('Needle' in row.content.controls[-1].value for row in rows.controls))
        navigation.controls[1].on_click(None)
        self.work.pop(0)()
        self.assertEqual(8, len(rows.controls))
        query.value = '不存在的关键词'
        query.on_submit(None)
        self.work.pop(0)()
        self.assertIn('命中 0', label.value)
        self.assertTrue(navigation.controls[0].disabled and navigation.controls[1].disabled)
        search.controls[2].on_click(None)
        self.work.pop(0)()
        self.assertEqual('', query.value)
        self.assertIn('/ 95', label.value)
        self.assertEqual('Needle 研究问题 80', rows.controls[0].content.controls[-1].value)

    def test_rewind_requires_dialog_action_and_refreshes_same_session(self):
        self.ui.new()
        sid = self.sid
        with patch.object(ui, 'handle', return_value={'ok': True, 'removed': 3}) as backend, \
                patch.object(self.ui, 'refresh') as refresh:
            self.ui.rewind()
            backend.assert_not_called()
            dialog = self.dialogs[-1]
            self.assertIn('不会撤销', dialog.content.value)
            dialog.actions[0].on_click(None)
        backend.assert_called_once_with({'cmd': 'session_rewind', 'session_id': sid, 'turns': 1})
        refresh.assert_called_once()

    def test_rewind_failure_keeps_conversation_and_shows_error(self):
        self.ui.new()
        with patch.object(ui, 'handle', return_value={'ok': False, 'error': '子任务尚未结束'}), \
                patch.object(self.ui, 'refresh') as refresh:
            self.ui.rewind()
            self.dialogs[-1].actions[0].on_click(None)
        refresh.assert_not_called()
        self.assertEqual('子任务尚未结束', self.dialogs[-1].content.value)

    def test_rewind_cleanup_warning_is_visible_after_committed_rollback(self):
        self.ui.new()
        with patch.object(ui, 'handle', return_value={'ok': True, 'warning': '旧执行文件稍后清理'}), \
                patch.object(self.ui, 'refresh'):
            self.ui.rewind()
            self.dialogs[-1].actions[0].on_click(None)
        self.assertIn('旧执行文件稍后清理', self.dialogs[-1].content.value)

    def open(self,sid):
        self.sid = sid

    def test_add_file_creates_project_session_and_renders_attachment(self):
        self.ui.attach('notes.md')
        row = session_workspace.handle({'cmd':'chat_get','session_id':self.sid})['session']
        self.assertEqual(self.pid,row['project_id'])
        self.assertEqual(['notes.md'],row['attachments'])
        self.assertEqual(1,len(self.ui.attachments.controls))
        self.ui.detach('notes.md')
        self.assertEqual([],self.ui.attachments.controls)

    def test_first_attachment_preserves_already_written_request(self):
        self.request.value = '请比较附件中的研究方法。'
        changed = Mock()
        self.ui.on_draft_changed = changed
        self.ui.attach('notes.md')
        self.assertEqual('请比较附件中的研究方法。', self.request.value)
        self.assertEqual(['notes.md'], session_workspace._get(self.sid)['attachments'])
        changed.assert_called_once()

    def test_failed_first_attachment_keeps_already_written_request(self):
        self.request.value = '这段输入不能因为附件不存在而丢失。'
        self.ui.attach('missing.md')
        self.assertEqual('这段输入不能因为附件不存在而丢失。', self.request.value)
        self.assertEqual([], session_workspace._get(self.sid)['attachments'])

    def test_picker_binds_material_to_session_selected_before_await(self):
        self.ui.new()
        original = self.sid
        self.ui.new()
        other = self.sid
        self.sid = original
        self.ui.refresh()
        async def pick(**_kwargs):
            self.sid = other
            self.ui.refresh()
            self.request.value = '另一会话的输入'
            return [SimpleNamespace(path=str(self.root / 'project' / 'notes.md'))]
        self.ui.file_picker = SimpleNamespace(pick_files=pick)
        asyncio.run(self.ui.choose_attachments())
        self.assertEqual(['notes.md'], session_workspace._get(original)['attachments'])
        self.assertEqual([], session_workspace._get(other)['attachments'])
        self.assertEqual('另一会话的输入', self.request.value)
        self.assertEqual([], self.ui.attachments.controls)

    def test_picker_materializes_first_target_before_opening_dialog(self):
        self.request.value = '先写好请求再选材料'
        async def pick(**_kwargs):
            self.assertIsNotNone(self.sid, 'File picker needs a stable session before yielding')
            self.assertEqual('先写好请求再选材料', self.request.value)
            return []
        self.ui.file_picker = SimpleNamespace(pick_files=pick)
        asyncio.run(self.ui.choose_attachments())
        self.assertIsNotNone(self.sid)
        self.assertEqual('先写好请求再选材料', self.request.value)

    def test_native_picker_accepts_project_file_as_absolute_path(self):
        async def pick(**_kwargs):
            return [SimpleNamespace(path=str(self.root / 'project' / 'notes.md'))]
        self.ui.file_picker = SimpleNamespace(pick_files=pick)
        asyncio.run(self.ui.choose_attachments())
        self.assertEqual(['notes.md'], session_workspace._get(self.sid)['attachments'])

    def test_native_picker_rejects_file_outside_captured_project(self):
        outside = self.root / 'outside.md'
        outside.write_text('other project material', encoding='utf-8')
        async def pick(**_kwargs):
            return [SimpleNamespace(path=str(outside))]
        self.ui.file_picker = SimpleNamespace(pick_files=pick)
        asyncio.run(self.ui.choose_attachments())
        self.assertEqual([], session_workspace._get(self.sid)['attachments'])
        self.assertTrue(self.dialogs)

    def test_markdown_preview_and_new_session_do_not_send_material(self):
        self.request.value = '# 方法\n- 对照组'
        self.ui.toggle_preview()
        self.assertEqual(self.request.value,self.ui.preview.content.value)
        self.ui.new()
        self.assertFalse(self.ui.preview.visible)
        self.assertEqual([],session_workspace.transcript(self.sid))
        self.assertEqual([],self.work)

    def test_fork_and_side_discussion_keep_parent_identity(self):
        self.ui.new()
        parent = self.sid
        self.ui.open_side()
        side = self.ui.side_sid
        self.assertEqual(parent,self.sid)
        row = session_workspace.handle({'cmd':'chat_get','session_id':side})['session']
        self.assertEqual(parent,row['parent_id'])
        self.assertTrue(row['side_chat'])
        self.ui.fork()
        self.assertNotIn(self.sid,[side,parent])
        self.assertEqual(parent,session_workspace.handle({'cmd':'chat_get','session_id':self.sid})['session']['parent_id'])

    def test_late_side_reply_never_overwrites_another_side_session(self):
        self.ui.new()
        self.ui.open_side()
        original = self.ui.side_sid
        self.ui.side_input.value = '第一项问题'
        self.ui.send_side()
        self.ui.open_side()
        current = self.ui.side_sid
        with patch.object(ui,'handle',return_value={'ok':True,'result':{'content':'旧讨论回复','status':'done'}}) as backend:
            self.work.pop()()
        self.assertEqual(original,backend.call_args.args[0]['session_id'])
        self.assertEqual(current,self.ui.side_sid)
        self.assertEqual('',self.ui.side_answer.value)
        self.assertFalse(self.ui.side_send.disabled)

    def test_side_failure_keeps_request_available_for_retry(self):
        self.ui.new()
        self.ui.open_side()
        self.ui.side_input.value = '请检查假设是否成立。'
        self.ui.send_side()
        with patch.object(ui, 'handle', return_value={'ok': False, 'error': '网络不可用'}):
            self.work.pop()()
        self.assertEqual('请检查假设是否成立。', self.ui.side_input.value)
        self.assertFalse(self.ui.side_send.disabled)
        self.assertIn('网络不可用', self.ui.side_notice.value)

    def test_side_success_preserves_new_text_typed_while_sending(self):
        self.ui.new()
        self.ui.open_side()
        self.ui.side_input.value = '发送 A'
        self.ui.send_side()
        self.ui.side_input.value = '继续写 B'
        with patch.object(ui, 'handle', return_value={'ok': True, 'result': {'content': 'A 完成', 'status': 'done'}}):
            self.work.pop()()
        self.assertEqual('继续写 B', self.ui.side_input.value)
        self.assertEqual('A 完成', self.ui.side_answer.value)

    def test_side_success_preserves_same_text_retyped_after_submission(self):
        self.ui.new()
        self.ui.open_side()
        self.ui.side_input.value = '同一问题'
        self.ui.send_side()
        for text in ('', '同一问题'):
            self.ui.side_input.value = text
            if self.ui.side_input.on_change:
                self.ui.side_input.on_change(None)
        with patch.object(ui, 'handle', return_value={'ok': True, 'result': {'content': '完成', 'status': 'done'}}):
            self.work.pop()()
        self.assertEqual('同一问题', self.ui.side_input.value)

    def test_side_exception_releases_send_button_and_preserves_request(self):
        self.ui.new()
        self.ui.open_side()
        self.ui.side_input.value = '保留失败请求'
        self.ui.send_side()
        with patch.object(ui, 'handle', side_effect=OSError('连接中断')):
            self.work.pop()()
        self.assertEqual('保留失败请求', self.ui.side_input.value)
        self.assertFalse(self.ui.side_send.disabled)
        self.assertIn('连接中断', self.ui.side_notice.value)

    def test_side_success_clears_only_unchanged_confirmed_request(self):
        self.ui.new()
        self.ui.open_side()
        self.ui.side_input.value = '已提交'
        self.ui.send_side()
        with patch.object(ui, 'handle', return_value={'ok': True, 'result': {'content': '完成', 'status': 'done'}}):
            self.work.pop()()
        self.assertEqual('', self.ui.side_input.value)
        self.assertFalse(self.ui.side_send.disabled)
