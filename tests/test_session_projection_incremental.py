"""Projection checkpoints decode only committed additions, not old history."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.agent_runtime import SessionStore


class IncrementalProjectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root)

    def seed(self, count=1200, sid='incremental'):
        rows = [{'type': 'user_message', 'session_id': sid,
                 'payload': {'text': 'Original request', 'mode': 'agent'}}]
        rows += [{'type': 'assistant_delta', 'session_id': sid,
                  'payload': {'step': 1, 'delta': f'科研片段{i}'}} for i in range(count - 1)]
        self.store._path(sid).write_bytes(b''.join(json.dumps(row, ensure_ascii=False).encode('utf-8')
                                                 + b'\n' for row in rows))
        return sid

    def test_own_append_decodes_only_one_new_event_and_idle_decodes_none(self):
        sid = self.seed(10000)
        self.store.snapshot(sid)
        self.store.append(sid, 'completed', {'status': 'done'})
        with mock.patch('json.loads', wraps=json.loads) as decode:
            self.assertEqual('done', self.store.snapshot(sid)['status'])
            self.store.snapshot(sid)
            self.store.snapshot(sid)
        self.assertEqual(1, decode.call_count)

    def test_external_append_decodes_only_added_events_even_with_same_mtime(self):
        sid = self.seed()
        self.store.snapshot(sid)
        path = self.store._path(sid)
        previous = path.stat()
        other = SessionStore(self.root)
        other.append(sid, 'assistant_message', {'content': '新增结果'})
        other.append(sid, 'completed', {'status': 'done'})
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        with mock.patch('json.loads', wraps=json.loads) as decode:
            snapshot = self.store.snapshot(sid)
        self.assertEqual(2, decode.call_count)
        self.assertEqual('新增结果', snapshot['content'])
        self.assertEqual(1202, self.store.list()[0]['events'])

    def test_unterminated_valid_json_waits_for_newline_then_applies_once(self):
        sid = self.seed(2)
        self.store.snapshot(sid)
        path = self.store._path(sid)
        with path.open('ab') as stream:
            stream.write(b'{"type":"completed","payload":{"status":"done"}}')
        self.assertEqual('running', self.store.snapshot(sid)['status'])
        self.assertEqual(2, self.store.list()[0]['events'])
        with path.open('ab') as stream:
            stream.write(b'\n')
        self.assertEqual('done', self.store.snapshot(sid)['status'])
        self.assertEqual(3, self.store.list()[0]['events'])

    def test_half_utf8_record_is_neither_decoded_nor_lost(self):
        sid = self.seed(2)
        self.store.snapshot(sid)
        path = self.store._path(sid)
        event = json.dumps({'type': 'assistant_message', 'payload': {'content': '中文完整结果'}},
                           ensure_ascii=False).encode('utf-8') + b'\n'
        cut = event.index('中'.encode()) + 1
        with path.open('ab') as stream:
            stream.write(event[:cut])
        with mock.patch('json.loads', wraps=json.loads) as decode:
            self.assertEqual('running', self.store.snapshot(sid)['status'])
        decode.assert_not_called()
        with path.open('ab') as stream:
            stream.write(event[cut:])
        self.assertEqual('中文完整结果', self.store.snapshot(sid)['content'])
        self.assertEqual(3, self.store.list()[0]['events'])

    def test_new_and_incremental_projection_match_frozen_legacy_prefixes(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' / 'session_projection_legacy.json').read_text(encoding='utf-8'))
        for case in fixture['cases']:
            sid = case['name']
            for index, expected in enumerate(case['states']):
                with self.subTest(case=case['name'], prefix=index):
                    if index:
                        event = case['events'][index - 1]
                        self.store.append(sid, event['type'], event['payload'])
                    actual = self.store.snapshot(sid)
                    self.assertEqual({**expected, 'session_id': sid}, actual)
                    self.assertEqual(expected, SessionStore._project('legacy', case['events'][:index]))

    def test_atomic_replacement_rebuilds_count_state_and_permission_resolution(self):
        sid = self.seed(3)
        self.store.append(sid, 'permission_granted', {'tool_call_id': 'call'})
        self.store.snapshot(sid)
        path = self.store._path(sid)
        replacement = self.root / 'replacement.jsonl'
        replacement.write_text(json.dumps({'type': 'permission_requested',
            'payload': {'tool_call_id': 'call', 'tool': 'new.write'}}) + '\n', encoding='utf-8')
        os.replace(replacement, path)
        result = self.store.snapshot(sid)
        self.assertEqual('waiting_approval', result['status'])
        self.assertEqual('call', result['pending']['id'])
        self.assertEqual(1, self.store.list()[0]['events'])

    def test_truncated_and_same_length_rewritten_logs_replace_cached_content(self):
        sid = self.seed(4)
        self.store.snapshot(sid)
        path = self.store._path(sid)
        path.write_bytes(path.read_bytes().splitlines()[0] + b'\n')
        self.assertEqual(1, self.store.list()[0]['events'])
        previous = path.stat()
        path.write_bytes(path.read_bytes().replace(b'Original', b'Replaced'))
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 10_000_000))
        self.assertEqual('Replaced request', self.store.snapshot(sid)['request'])

    def test_snapshot_does_not_invoke_raw_approval_read(self):
        sid = self.seed()
        with mock.patch.object(self.store, 'read', side_effect=AssertionError('approval read must remain separate')):
            self.store.snapshot(sid)
            self.store.append(sid, 'completed', {'status': 'done'})
            self.assertEqual('done', self.store.snapshot(sid)['status'])

    def test_old_snapshots_stay_immutable_after_incremental_updates(self):
        sid = self.seed(1)
        self.store.append(sid, 'permission_requested', {'tool_call_id': 'one', 'tool': 'write',
            'arguments': {'nested': ['original']}})
        old = self.store.snapshot(sid)
        self.store.append(sid, 'permission_granted', {'tool_call_id': 'one'})
        self.assertIsNone(self.store.snapshot(sid)['pending'])
        self.assertEqual(['original'], old['pending']['arguments']['nested'])
        old['pending']['arguments']['nested'].append('poison')
        self.assertIsNone(self.store.snapshot(sid)['pending'])

    def test_failed_second_page_never_partially_publishes_a_projection(self):
        from src import session_projection
        sid = self.seed(1)
        self.store.snapshot(sid)
        with self.store._path(sid).open('ab') as stream:
            for index in range(501):
                stream.write(json.dumps({'type': 'tool_call', 'payload': {'id': f'call-{index}'}}).encode() + b'\n')
        original = session_projection.read_event_page
        pages = []

        def interrupted(*args, **kwargs):
            if pages:
                raise OSError('temporary reader failure')
            page = original(*args, **kwargs)
            pages.append(page)
            return page

        with mock.patch.object(session_projection, 'read_event_page', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'temporary reader failure'):
                self.store.snapshot(sid)
        self.assertEqual(502, self.store.list()[0]['events'])
        self.assertEqual('running', self.store.snapshot(sid)['status'])

    def test_warm_reader_survives_concurrent_lru_eviction(self):
        sid = self.seed(1)
        self.store.snapshot(sid)
        original = self.store._signature

        def evicted(path):
            signature = original(path)
            with self.store._projection_lock:
                self.store._projection_cache.pop(sid, None)
            return signature

        with mock.patch.object(self.store, '_signature', side_effect=evicted):
            self.assertEqual('Original request', self.store.snapshot(sid)['request'])

    def test_list_consumes_the_same_incremental_projection_without_reparsing(self):
        sid = self.seed()
        self.store.list()
        self.store.append(sid, 'completed', {'status': 'done'})
        with mock.patch('json.loads', wraps=json.loads) as decode:
            rows = self.store.list()
            snapshot = self.store.snapshot(sid)
        self.assertEqual(1, decode.call_count)
        self.assertEqual('done', rows[0]['status'])
        self.assertEqual(snapshot['status'], rows[0]['status'])
        self.assertEqual(1201, rows[0]['events'])
