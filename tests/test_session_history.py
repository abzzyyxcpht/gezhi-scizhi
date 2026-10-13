from __future__ import annotations
import importlib
import json
import os
import multiprocessing
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.agent_runtime import SessionStore


def _cold_history_process(root, output):
    from src.session_history import history
    page = history('history-one', store=SessionStore(root), query='研究')
    output.put((page['total'], len(page['messages'])))


class SessionHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionStore(Path(self.temp.name) / 'sessions')
        self.sid = 'history-one'
        self.module = importlib.import_module('src.session_history')

    def add(self, text, kind='user_message'):
        self.store.append(self.sid, kind, {'content' if kind == 'assistant_message' else 'text': text})

    def page(self, **options):
        return self.module.history(self.sid, store=self.store, **options)

    def test_pages_use_persisted_index_without_redecoding_old_events(self):
        for index in range(105):
            self.add(f'研究问题 {index}')
        last = self.page(page_size=40)
        self.assertEqual(3, last['page'])
        self.assertEqual(105, last['total'])
        self.assertEqual('研究问题 80', last['messages'][0]['content'])
        with mock.patch.object(self.module, '_decode', side_effect=AssertionError('old journal decoded')):
            first = self.page(page=1, page_size=40)
            self.assertEqual('研究问题 0', first['messages'][0]['content'])
        self.add('新的末尾')
        with mock.patch.object(self.module, '_decode', wraps=self.module._decode) as decode:
            tail = self.page(page_size=40)
        self.assertEqual(1, decode.call_count)
        self.assertEqual(106, tail['total'])

    def test_unicode_substring_search_and_redaction_match_visible_text(self):
        for text in ['Straße 大模型反例', 'strasse 应用', '100%_原文', 'api_key=private-value']:
            self.add(text)
        self.assertEqual(2, self.page(query='STRASSE')['total'])
        self.assertEqual(1, self.page(query='反例')['total'])
        self.assertEqual(1, self.page(query='%_')['total'])
        self.assertEqual(0, self.page(query='private-value')['total'])
        self.assertNotIn('private-value', json.dumps(self.page()))

    def test_opened_snapshot_keeps_page_positions_during_append(self):
        for index in range(5):
            self.add(str(index))
        first = self.page(page=1, page_size=2)
        self.add('new')
        second = self.page(page=2, page_size=2, snapshot=first['snapshot'])
        self.assertEqual(['2', '3'], [row['content'] for row in second['messages']])
        self.assertEqual(5, second['total'])
        self.assertTrue(second['new_messages'])
        self.assertEqual(6, self.page()['total'])

    def test_replacement_resets_snapshot_and_half_json_waits_for_newline(self):
        self.add('old')
        old = self.page()
        path = self.store._path(self.sid)
        replacement = path.with_suffix('.replacement')
        replacement.write_text(json.dumps({'type': 'steer_applied', 'payload': {'text': '当前补充'}}) + '\n', encoding='utf-8')
        os.replace(replacement, path)
        current = self.page(snapshot=old['snapshot'])
        self.assertTrue(current['reset'])
        self.assertEqual('steer_applied', current['messages'][0]['kind'])
        data = json.dumps({'type': 'assistant_message', 'payload': {'content': '半行完整化'}}, ensure_ascii=False).encode()
        with path.open('ab') as stream:
            stream.write(data[:-2])
        self.assertEqual(1, self.page()['total'])
        with path.open('ab') as stream:
            stream.write(data[-2:] + b'\n')
        self.assertEqual(2, self.page()['total'])

    def test_corrupt_derived_db_rebuilds_without_changing_journal(self):
        self.add('保留原始材料')
        self.page()
        before = self.store._path(self.sid).read_bytes()
        index = self.module.index_path(self.store, self.sid)
        index.write_bytes(b'not a database')
        page = self.page()
        self.assertEqual('保留原始材料', page['messages'][0]['content'])
        self.assertTrue(page['index_rebuilt'])
        self.assertEqual(before, self.store._path(self.sid).read_bytes())

    def test_long_message_chunks_preserve_complete_text_and_reject_old_generation(self):
        text = '科研文本🧪' * 5000
        self.add(text)
        history = self.page()
        row = history['messages'][0]
        self.assertTrue(row['truncated'])
        pieces, offset = [], 0
        while offset is not None:
            part = self.module.message(self.sid, row['id'], store=self.store, generation=history['generation'], offset=offset)
            pieces.append(part['content'])
            offset = part['next_offset']
        self.assertEqual(text, ''.join(pieces))
        self.store._path(self.sid).write_text(json.dumps({'type': 'user_message', 'payload': {'text': 'replacement'}})+'\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.module.message(self.sid, row['id'], store=self.store, generation=history['generation'])

    def test_query_quotes_spaces_and_percent_remain_literal_substrings(self):
        texts = ['alpha "quoted" beta', 'two  spaces', '100%_literal', '中文中间的字', 'emoji 🧪试验']
        for text in texts:
            self.add(text)
        for needle in ['"quoted"', 'two  ', '%_lit', '中间的', '🧪试验']:
            rows = self.page(query=needle)['messages']
            expected = [text for text in texts if needle.strip().casefold() in text.casefold()]
            self.assertEqual(expected, [row['content'] for row in rows])

    def test_unrelated_or_malformed_events_are_not_visible_history(self):
        self.add('known')
        with self.store._path(self.sid).open('ab') as stream:
            stream.write(b'not json\n[]\n{"type":"user_message","payload":[]}\n')
        self.store.append(self.sid, 'tool_result', {'secret': 'not part of transcript'})
        self.assertEqual(1, self.page()['total'])

    def test_two_cold_processes_build_the_same_index_without_duplicate_messages(self):
        for index in range(120):
            self.add(f'研究记录 {index}')
        context = multiprocessing.get_context('spawn')
        output = context.Queue()
        processes = [context.Process(target=_cold_history_process, args=(str(self.store.root), output)) for _ in range(2)]
        try:
            for process in processes:
                process.start()
            self.assertEqual([(120, 40), (120, 40)], [output.get(timeout=5), output.get(timeout=5)])
            for process in processes:
                process.join(5)
                self.assertEqual(0, process.exitcode)
            self.assertEqual(120, self.page()['total'])
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(3)
            output.close()

    def test_same_length_rewrite_rebuilds_search_and_credentials_never_enter_cache(self):
        self.add('alpha api_key=secret-value')
        self.page()
        target = self.module.index_path(self.store, self.sid)
        self.assertNotIn(b'secret-value', target.read_bytes())
        log = self.store._path(self.sid)
        previous = log.stat()
        log.write_bytes(log.read_bytes().replace(b'alpha', b'bravo'))
        os.utime(log, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1000000))
        self.assertEqual(0, self.page(query='alpha')['total'])
        self.assertEqual(1, self.page(query='bravo')['total'])

    def test_absent_trigram_backend_falls_back_and_later_availability_rebuilds(self):
        original = sqlite3.connect
        class NoTrigram(sqlite3.Connection):
            def execute(self, statement, *args):
                if 'CREATE VIRTUAL TABLE' in statement:
                    raise sqlite3.OperationalError('no such tokenizer: trigram')
                return super().execute(statement, *args)
        self.add('可检索的 boundary needle')
        with mock.patch.object(self.module.sqlite3, 'connect', side_effect=lambda *args, **kwargs: original(*args, factory=NoTrigram, **kwargs)):
            fallback = self.page(query='boundary')
        self.assertEqual(1, fallback['total'])
        self.assertEqual('literal_scan', fallback['search_method'])
        rebuilt = self.page(query='boundary')
        self.assertEqual(1, rebuilt['total'])
        self.assertTrue(rebuilt['index_rebuilt'])
