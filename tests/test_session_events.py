"""Byte-cursor event pages must survive rewrites without rereading history."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from src import bridge, remote_control
from src.agent_runtime import SessionStore


class SessionEventsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root)

    def seed(self, count=5, sid='events01'):
        for number in range(count):
            self.store.append(sid, 'assistant_delta', {'step': 1, 'delta': f'研究{number}', 'n': number})
        return sid

    def page(self, sid='events01', cursor=None, limit=200, tail=False):
        self.assertTrue(callable(getattr(self.store, 'event_page', None)), 'SessionStore needs event_page')
        return self.store.event_page(sid, cursor=cursor, limit=limit, tail=tail)

    def numbers(self, page):
        return [event['payload']['n'] for event in page['events']]

    def test_page_contract_and_forward_pagination_without_duplicates(self):
        self.seed()
        first = self.page(limit=2)
        self.assertEqual({'events', 'cursor', 'reset', 'has_more'}, set(first))
        self.assertFalse(first['reset'])
        self.assertTrue(first['has_more'])
        self.assertIsInstance(first['cursor'], str)
        second = self.page(cursor=first['cursor'], limit=2)
        third = self.page(cursor=second['cursor'], limit=2)
        self.assertEqual([0, 1, 2, 3, 4], self.numbers(first) + self.numbers(second) + self.numbers(third))
        self.assertFalse(third['has_more'])

    def test_append_in_other_store_and_resume_in_new_store(self):
        self.seed(3)
        first = self.page(tail=True, limit=2)
        other = SessionStore(self.root)
        other.append('events01', 'assistant_delta', {'n': 3, 'delta': '中文增量'})
        page = other.event_page('events01', cursor=first['cursor'], limit=2, tail=True)
        self.assertEqual([3], self.numbers(page))
        self.assertFalse(page['reset'])

    def test_idle_page_does_not_parse_events_or_call_raw_read(self):
        from src import session_events
        self.seed(12)
        first = self.page(tail=True, limit=4)
        with mock.patch.object(session_events, '_decode_event', wraps=session_events._decode_event) as decode, \
                mock.patch.object(self.store, 'read', side_effect=AssertionError('raw read not allowed')):
            for _ in range(5):
                idle = self.page(cursor=first['cursor'])
                self.assertEqual([], idle['events'])
                self.assertFalse(idle['has_more'])
                self.assertFalse(idle['reset'])
        decode.assert_not_called()

    def test_tail_reads_bounded_blocks_and_decodes_only_requested_events(self):
        from src import session_events
        sid = 'long01'
        path = self.store._path(sid)
        path.write_bytes(b''.join(json.dumps({'type': 'assistant_delta', 'payload': {'n': i,
            'delta': '科研' * 50}}, ensure_ascii=False).encode('utf-8') + b'\n' for i in range(10000)))
        original_open = Path.open
        reads = []

        class TracedFile:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, key):
                return getattr(self.stream, key)
            def read(self, size=-1):
                data = self.stream.read(size)
                reads.append(len(data))
                return data

        def traced(path_arg, *args, **kwargs):
            stream = original_open(path_arg, *args, **kwargs)
            return TracedFile(stream) if path_arg == path and args == ('rb',) else stream

        with mock.patch.object(Path, 'open', new=traced), \
                mock.patch.object(session_events, '_decode_event', wraps=session_events._decode_event) as decode:
            page = self.page(sid, limit=40, tail=True)
        self.assertEqual(list(range(9960, 10000)), self.numbers(page))
        self.assertEqual(40, decode.call_count)
        self.assertLess(sum(reads), 256 * 1024)
        self.assertFalse(page['has_more'])

    def test_atomic_replacement_resets_even_with_same_length_and_timestamp(self):
        self.seed(3)
        page = self.page()
        path = self.store._path('events01')
        before = path.stat()
        replacement = self.root / 'replacement.jsonl'
        replacement.write_bytes(path.read_bytes().replace(b'"n": 0', b'"n": 9'))
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, path)
        reset = self.page(cursor=page['cursor'])
        self.assertTrue(reset['reset'])
        self.assertEqual([9, 1, 2], self.numbers(reset))

    def test_same_length_in_place_rewrite_resets(self):
        self.seed(3)
        page = self.page()
        path = self.store._path('events01')
        previous = path.stat()
        path.write_bytes(path.read_bytes().replace(b'"n": 1', b'"n": 8'))
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 10_000_000))
        reset = self.page(cursor=page['cursor'])
        self.assertTrue(reset['reset'])
        self.assertEqual([0, 8, 2], self.numbers(reset))

    def test_truncation_detected_even_before_cursor_reaches_old_end(self):
        self.seed(10)
        page = self.page(limit=2)
        path = self.store._path('events01')
        path.write_bytes(b'\n'.join(path.read_bytes().splitlines()[:5]) + b'\n')
        reset = self.page(cursor=page['cursor'], limit=2, tail=True)
        self.assertTrue(reset['reset'])
        self.assertEqual([3, 4], self.numbers(reset))

    def test_tail_is_ignored_on_valid_incremental_cursor(self):
        self.seed(8)
        first = self.page(limit=2)
        second = self.page(cursor=first['cursor'], limit=2, tail=True)
        self.assertFalse(second['reset'])
        self.assertEqual([2, 3], self.numbers(second))
        self.assertTrue(second['has_more'])

    def test_missing_file_cursor_resets_when_file_appears_or_disappears(self):
        empty = self.page()
        self.assertEqual([], empty['events'])
        self.assertFalse(empty['reset'])
        self.assertFalse(self.page(cursor=empty['cursor'])['reset'])
        self.seed(1)
        appeared = self.page(cursor=empty['cursor'])
        self.assertTrue(appeared['reset'])
        self.store._path('events01').unlink()
        removed = self.page(cursor=appeared['cursor'])
        self.assertTrue(removed['reset'])
        self.assertEqual([], removed['events'])

    def test_partial_json_and_utf8_tail_wait_for_newline_then_deliver_once(self):
        from src import session_events
        self.seed(1)
        path = self.store._path('events01')
        record = json.dumps({'type': 'assistant_delta', 'payload': {'n': 1, 'delta': '中文续写'}},
                            ensure_ascii=False).encode('utf-8') + b'\n'
        split = record.index('中'.encode('utf-8')) + 1
        with path.open('ab') as stream:
            stream.write(record[:split])
        first = self.page()
        self.assertEqual([0], self.numbers(first))
        self.assertFalse(first['has_more'])
        with mock.patch.object(session_events, '_decode_event', wraps=session_events._decode_event) as decode:
            self.assertEqual([], self.page(cursor=first['cursor'])['events'])
        decode.assert_not_called()
        with path.open('ab') as stream:
            stream.write(record[split:])
        next_page = self.page(cursor=first['cursor'])
        self.assertFalse(next_page['reset'])
        self.assertEqual([1], self.numbers(next_page))
        self.assertEqual([], self.page(cursor=next_page['cursor'])['events'])

    def test_tail_skips_incomplete_suffix_and_malformed_complete_lines(self):
        self.seed(3)
        path = self.store._path('events01')
        with path.open('ab') as stream:
            stream.write(b'not-json\n[]\n{"partial":')
        page = self.page(limit=2, tail=True)
        self.assertEqual([1, 2], self.numbers(page))
        self.assertFalse(page['has_more'])
        self.assertEqual([], self.page(cursor=page['cursor'])['events'])

    def test_forward_skips_bad_lines_and_advances_without_stalling(self):
        self.seed(1)
        path = self.store._path('events01')
        with path.open('ab') as stream:
            stream.write(b'invalid\nnull\n\xff\n')
        first = self.page(limit=1)
        second = self.page(cursor=first['cursor'])
        self.assertEqual([], second['events'])
        self.assertFalse(second['has_more'])
        self.assertNotEqual(first['cursor'], second['cursor'])

    def test_cursor_has_no_absolute_path_and_is_scoped_to_session(self):
        self.seed(2)
        self.seed(2, sid='other')
        page = self.page()
        decoded = base64.urlsafe_b64decode(page['cursor'] + '=' * (-len(page['cursor']) % 4)).decode()
        self.assertNotIn(str(self.root), decoded)
        other = self.page('other', cursor=page['cursor'])
        self.assertTrue(other['reset'])
        self.assertEqual([0, 1], self.numbers(other))

    def test_sidechain_can_page_after_store_restart(self):
        self.store.register_sidechain('child', 'parent')
        (self.root / 'parent' / 'children' / 'child.meta.json').write_text('{}', encoding='utf-8')
        self.store.append('child', 'assistant_delta', {'n': 1})
        page = SessionStore(self.root).event_page('child', tail=True)
        self.assertEqual([1], self.numbers(page))

    def test_tail_handles_multiblock_records_and_crlf_without_dropping_events(self):
        path = self.store._path('events01')
        records = [{'type': 'assistant_message', 'payload': {'n': n, 'content': '跨块中文' * 20000}}
                   for n in range(3)]
        path.write_bytes(b''.join(json.dumps(row, ensure_ascii=False).encode('utf-8') + b'\r\n'
                                  for row in records))
        page = self.page(limit=2, tail=True)
        self.assertEqual([1, 2], self.numbers(page))
        self.assertEqual(records[1]['payload']['content'], page['events'][0]['payload']['content'])
        self.assertFalse(page['has_more'])

    def test_valid_json_without_newline_is_not_committed(self):
        path = self.store._path('events01')
        path.write_bytes(b'{"type":"assistant_delta","payload":{"n":1}}')
        for tail in (False, True):
            with self.subTest(tail=tail):
                page = self.page(tail=tail)
                self.assertEqual([], page['events'])
                self.assertFalse(page['has_more'])
        with path.open('ab') as stream:
            stream.write(b'\n')
        completed = self.page(cursor=page['cursor'])
        self.assertFalse(completed['reset'])
        self.assertEqual([1], self.numbers(completed))

    def test_atomic_replace_during_read_never_returns_removed_events(self):
        from src import session_events
        self.seed(4)
        first = self.page(limit=1)
        path = self.store._path('events01')
        original = session_events._read_forward
        original_stat = Path.stat
        ready, replaced = [], []

        def reading(*args):
            result = original(*args)
            ready.append(True)
            return result

        def stat(path_arg, *args, **kwargs):
            if path_arg == path and ready and not replaced:
                replacement = self.root / 'incoming.jsonl'
                replacement.write_bytes(json.dumps({'type': 'assistant_message',
                    'payload': {'n': 9, 'content': '替换后的记录'}}, ensure_ascii=False).encode('utf-8') + b'\n')
                os.replace(replacement, path)
                replaced.append(True)
            return original_stat(path_arg, *args, **kwargs)

        with mock.patch.object(session_events, '_read_forward', side_effect=reading), \
                mock.patch.object(Path, 'stat', new=stat):
            page = self.page(cursor=first['cursor'])
        self.assertTrue(page['reset'])
        self.assertEqual([9], self.numbers(page))

    def test_normal_append_during_read_is_received_on_following_page(self):
        from src import session_events
        self.seed(3)
        first = self.page(limit=1)
        original = session_events._read_forward

        def reading(*args):
            result = original(*args)
            SessionStore(self.root).append('events01', 'assistant_delta', {'n': 3})
            return result

        with mock.patch.object(session_events, '_read_forward', side_effect=reading):
            page = self.page(cursor=first['cursor'])
        self.assertFalse(page['reset'])
        self.assertEqual([1, 2], self.numbers(page))
        following = self.page(cursor=page['cursor'])
        self.assertFalse(following['reset'])
        self.assertEqual([3], self.numbers(following))

    def test_invalid_parameters_are_rejected_without_path_access(self):
        for kwargs in ({'sid': '../unsafe'}, {'sid': ''}, {'limit': True}, {'limit': 0},
                       {'limit': 501}, {'limit': '20'}, {'tail': 1}, {'cursor': {}},
                       {'cursor': 'x' * 2049}, {'cursor': 'bad-cursor'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.page(**kwargs)


class SessionEventsBridgeTest(unittest.TestCase):
    def test_bridge_returns_redacted_page_and_echoes_request_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            store.append('bridge01', 'tool_result', {'api_key': 'secret-value', 'content': 'token=secret-inline'})
            with mock.patch.object(bridge, '_runtime', return_value=SimpleNamespace(sessions=store)):
                page = bridge.handle({'cmd': 'session_events', 'session_id': 'bridge01', 'tail': True,
                                      'limit': 5, 'request_id': 'page-1'})
            self.assertTrue(page['ok'], page)
            self.assertEqual('page-1', page['request_id'])
            self.assertNotIn('secret-value', json.dumps(page))
            self.assertNotIn('secret-inline', json.dumps(page))
            self.assertEqual('[REDACTED]', page['events'][0]['payload']['api_key'])

    def test_event_pages_use_status_lane_while_research_workers_are_busy(self):
        release = threading.Event()
        requests = [{'cmd': 'slow', 'request_id': i} for i in range(4)]
        requests.append({'cmd': 'session_events', 'request_id': 'page'})

        def dispatch(req):
            if req['cmd'] == 'slow':
                release.wait(2)
            else:
                release.set()
            return {'ok': True}

        output = io.StringIO()
        bridge.process_requests(io.StringIO('\n'.join(json.dumps(req) for req in requests)), output, dispatch)
        rows = [json.loads(row) for row in output.getvalue().splitlines()]
        self.assertIn('page', [r['request_id'] for r in rows[:4]])

    def test_remote_allows_only_bounded_read_only_page_fields(self):
        request = {'cmd': 'session_events', 'session_id': 'remote01', 'cursor': None, 'limit': 200, 'tail': True}
        self.assertEqual(request, remote_control._validated(request))
        self.assertIn('session_events', remote_control._READ_ONLY)
        for patch in ({'cursor': {}}, {'cursor': 'x' * 2049}, {'limit': True}, {'limit': 501},
                      {'limit': 0}, {'tail': 1}, {'path': 'C:/secret'}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                remote_control._validated({**request, **patch})
