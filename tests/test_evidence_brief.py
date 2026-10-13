"""Evidence comparison displays assertions and material scope, never proof."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import config, research_notebook
from src import evidence_brief


class EvidenceBriefTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = mock.patch.object(config, 'DATA_DIR', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, evidence=None, task='task-a', title='反例检索是否改善综述'):
        result = research_notebook.handle({'cmd': 'notebook_save', 'record': {
            'kind': 'hypothesis', 'title': title, 'question': '是否改善局限覆盖？',
            'prediction': '在固定对照下检验', 'task_id': task, 'evidence': evidence or []}})
        self.assertTrue(result['ok'], result)
        return result['record']

    @staticmethod
    def item(relation, identifier, *, synthetic=False, quote='观察原文', scope='abstract'):
        return {'relation': relation, 'source': {'id': identifier, 'synthetic': synthetic},
                'quote': quote, 'scope': scope}

    def test_groups_are_assertions_with_scope_and_synthetic_warning(self):
        record = self.save([self.item('supports', 'W1'),
                            self.item('challenges', 'W2', scope='fulltext'),
                            self.item('context', 'demo', synthetic=True)])
        result = evidence_brief.handle({'cmd': 'evidence_brief', 'task_id': 'task-a'})
        row = result['brief']['records'][0]
        self.assertEqual(record['id'], row['id'])
        self.assertEqual(1, row['counts']['supports'])
        self.assertEqual(1, row['counts']['challenges'])
        self.assertEqual(1, row['counts']['synthetic'])
        self.assertEqual(3, row['counts']['unique_sources'])
        self.assertEqual('fulltext', row['groups']['challenges'][0]['scope'])
        self.assertEqual('not_performed', row['provenance']['scientific_verification'])
        self.assertNotIn('confidence', row)
        self.assertIn('合成', result['brief']['boundary'])

    def test_missing_counterevidence_becomes_draft_not_a_conclusion_or_auto_write(self):
        record = self.save([self.item('supports', 'W1')])
        before = {p.name: p.read_bytes() for p in self.root.glob('*.json')}
        with mock.patch('src.llm.chat', side_effect=AssertionError('model must not run')):
            row = evidence_brief.handle({'cmd': 'evidence_brief'})['brief']['records'][0]
        self.assertIn('缺少反驳材料', row['gaps'])
        self.assertIn(record['id'], row['followup_draft'])
        self.assertIn('版本 1', row['followup_draft'])
        self.assertIn('反驳', row['followup_draft'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.glob('*.json')})

    def test_task_scope_never_mixes_another_task(self):
        self.save(task='task-a')
        other = self.save(task='task-b')
        rows = evidence_brief.handle({'cmd': 'evidence_brief', 'task_id': 'task-a'})['brief']['records']
        self.assertEqual(1, len(rows))
        self.assertNotEqual(other['id'], rows[0]['id'])
        self.assertEqual([], evidence_brief.handle({'cmd': 'evidence_brief', 'task_id': 'absent'})['brief']['records'])

    def test_duplicates_do_not_inflate_unique_sources_and_long_quotes_are_marked(self):
        self.save([self.item('supports', 'W1', quote='a' * 4000), self.item('supports', 'W1')])
        row = evidence_brief.handle({'cmd': 'evidence_brief'})['brief']['records'][0]
        self.assertEqual(2, row['counts']['supports'])
        self.assertEqual(1, row['counts']['unique_sources'])
        self.assertTrue(row['groups']['supports'][0]['quote_truncated'])
        self.assertEqual(2000, len(row['groups']['supports'][0]['quote']))

    def test_preview_limits_keep_truthful_full_counts(self):
        self.save([self.item('supports', f'W{i}') for i in range(23)])
        row = evidence_brief.handle({'cmd': 'evidence_brief'})['brief']['records'][0]
        self.assertEqual(23, row['counts']['supports'])
        self.assertEqual(20, len(row['groups']['supports']))
        self.assertTrue(row['evidence_truncated'])

    def test_invalid_scope_extra_fields_and_secret_redaction(self):
        for req in ({'cmd': 'evidence_brief', 'task_id': '../private'},
                    {'cmd': 'evidence_brief', 'path': 'private.json'}):
            self.assertFalse(evidence_brief.handle(req)['ok'])
        self.save([self.item('context', 'W1', quote='api_key=hidden-value')])
        self.assertNotIn('hidden-value', json.dumps(evidence_brief.handle({'cmd': 'evidence_brief'})))

    def test_invalid_falsy_scope_never_widens_to_all_records(self):
        self.save(task='other-task')
        for scope in (False, 0, [], {}):
            with self.subTest(scope=scope):
                self.assertFalse(evidence_brief.handle({'cmd': 'evidence_brief', 'task_id': scope})['ok'])
