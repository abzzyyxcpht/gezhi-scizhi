from __future__ import annotations

import importlib
import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from src import config, settings, research_notebook

_MODEL_FIXTURE = {'model': 'review-fixture', 'protocol': 'chat_completions', 'context_window': 32768,
                  'api_key': 'fixture-only', 'base_url': 'http://127.0.0.1:9/v1'}


def _interrupted_model_process(root, record_id, entered):
    from src import llm, model_registry
    from src.evidence_reviews import EvidenceReviews
    config.DATA_DIR = Path(root)
    settings._PATH = Path(root) / 'settings.json'
    settings.save({'llm_enabled': True, 'agent_stream_enabled': False})
    def wait_model(*args, **kwargs):
        entered.set()
        threading.Event().wait(20)
        raise AssertionError('fixture process should have been terminated')
    with mock.patch.object(llm, 'available', return_value=True), \
            mock.patch.object(model_registry, 'resolve', return_value=_MODEL_FIXTURE), \
            mock.patch.object(llm, 'chat', side_effect=wait_model):
        manager = EvidenceReviews(root)
        reply = manager.handle({'cmd': 'review_start', 'record_id': record_id, 'expected_version': 1,
                                'review_request_id': 'interrupted-model', 'mode': 'model'})
        if not reply['ok']:
            raise RuntimeError(reply['error'])
        threading.Event().wait(20)


def _review_process(root, record_id, request_id, notifications, release):
    from src.evidence_reviews import EvidenceReviews
    config.DATA_DIR = Path(root)
    settings._PATH = Path(root) / 'settings.json'
    def runner(job, cancelled, progress):
        notifications.put(job['id'])
        release.wait(8)
        return {'text': 'controlled result', 'scientific_verification': 'not_performed'}
    manager = EvidenceReviews(root, runner=runner)
    try:
        reply = manager.handle({'cmd': 'review_start', 'record_id': record_id, 'expected_version': 1,
                                'review_request_id': request_id, 'mode': 'local'})
        if not reply['ok']:
            raise RuntimeError(reply['error'])
        for _ in range(600):
            job = manager.handle({'cmd': 'review_get', 'job_id': reply['job']['id']})['job']
            if job['status'] == 'ready':
                return
            time.sleep(.02)
        raise RuntimeError('worker did not settle')
    finally:
        manager.close()


class EvidenceReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for obj, field, value in [(config, 'DATA_DIR', self.root), (settings, '_PATH', self.root / 'settings.json'),
                                  (config, 'LLM_ENABLED', getattr(config, 'LLM_ENABLED', True))]:
            patch = mock.patch.object(obj, field, value, create=True)
            patch.start()
            self.addCleanup(patch.stop)
        settings.save({'experimental_evidence_reviews': True, 'llm_enabled': False})
        self.record = research_notebook.handle({'cmd': 'notebook_save', 'record': {
            'title': '综述边界假设', 'question': '反例检索能否改善边界说明？', 'kind': 'hypothesis',
            'evidence': [{'relation': 'supports', 'scope': 'abstract', 'quote': '合成观察',
                          'source': {'id': 'fixture-source', 'synthetic': True}}]}})['record']
        self.module = importlib.import_module('src.evidence_reviews')
        self.manager = self.create_manager()

    def create_manager(self, **kwargs):
        manager = self.module.EvidenceReviews(self.root, **kwargs)
        self.addCleanup(manager.close)
        return manager

    def call(self, cmd, **fields):
        return self.manager.handle({'cmd': cmd, **fields})

    def start(self, key='one', mode='local'):
        reply = self.call('review_start', record_id=self.record['id'], expected_version=self.record['version'],
                          review_request_id=key, mode=mode)
        self.assertTrue(reply['ok'], reply)
        return reply['job']

    def wait(self, job_id, status='ready'):
        until = time.monotonic() + 4
        while time.monotonic() < until:
            reply = self.call('review_get', job_id=job_id)
            if reply.get('ok') and reply['job']['status'] == status:
                return reply['job']
            time.sleep(.02)
        self.fail(str(reply))

    def test_local_review_is_durable_readonly_and_retry_is_idempotent(self):
        before = (self.root / 'research_notebook.json').read_bytes()
        job = self.start()
        self.assertEqual(job['id'], self.start()['id'])
        ready = self.wait(job['id'])
        self.assertEqual(self.record['version'], ready['record_version'])
        self.assertIn('合成', ready['result']['text'])
        self.assertEqual('not_performed', ready['result']['scientific_verification'])
        self.assertEqual(before, (self.root / 'research_notebook.json').read_bytes())

    def test_accept_only_appends_review_note_once_and_never_changes_evidence(self):
        job = self.start()
        self.wait(job['id'])
        first = self.call('review_accept', job_id=job['id'])
        self.assertTrue(first['ok'], first)
        second = self.call('review_accept', job_id=job['id'])
        self.assertTrue(second['ok'], second)
        saved = research_notebook.handle({'cmd': 'notebook_get', 'id': self.record['id']})['record']
        self.assertEqual(self.record['version'] + 1, saved['version'])
        self.assertEqual(1, len(saved['observations']))
        self.assertEqual(self.record['evidence'], saved['evidence'])
        self.assertEqual('planned', saved['status'])

    def test_changed_record_makes_review_stale_and_cannot_be_accepted(self):
        job = self.start()
        self.wait(job['id'])
        update = research_notebook.handle({'cmd': 'notebook_save', 'expected_version': 1,
                                          'record': {'id': self.record['id'], 'question': '新的研究问题'}})
        self.assertTrue(update['ok'])
        current = self.call('review_get', job_id=job['id'])['job']
        self.assertTrue(current['stale'])
        self.assertFalse(self.call('review_accept', job_id=job['id'])['ok'])
        self.assertEqual([], update['record']['observations'])

    def test_cancelled_job_cannot_be_replaced_by_late_success(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def runner(job, cancelled, progress):
            entered.set()
            release.wait(3)
            return {'text': 'late output', 'scientific_verification': 'not_performed'}
        self.manager.close()
        self.manager = self.create_manager(runner=runner)
        job = self.start()
        self.assertTrue(entered.wait(2))
        self.assertTrue(self.call('review_cancel', job_id=job['id'])['ok'])
        release.set()
        cancelled = self.wait(job['id'], 'cancelled')
        self.assertNotEqual('ready', cancelled['status'])
        self.assertFalse(self.call('review_accept', job_id=job['id'])['ok'])

    def test_model_review_never_silently_falls_back_when_unconfigured(self):
        reply = self.call('review_start', record_id=self.record['id'], expected_version=1,
                          review_request_id='model-one', mode='model')
        self.assertFalse(reply['ok'])
        self.assertEqual([], self.call('review_list')['jobs'])

    def test_accept_receipt_failure_does_not_append_a_second_observation(self):
        job = self.start()
        self.wait(job['id'])
        original = self.manager._save
        def fail_receipt(row):
            if row['status'] == 'accepted':
                raise OSError('receipt interrupted')
            original(row)
        with mock.patch.object(self.manager, '_save', side_effect=fail_receipt):
            self.assertFalse(self.call('review_accept', job_id=job['id'])['ok'])
        self.assertTrue(self.call('review_accept', job_id=job['id'])['ok'])
        record = research_notebook.handle({'cmd': 'notebook_get', 'id': self.record['id']})['record']
        self.assertEqual(2, record['version'])
        self.assertEqual(1, len(record['observations']))
        self.assertFalse(self.call('review_get', job_id=job['id'])['job']['stale'])

    def test_closed_owner_requires_explicit_resume_before_execution(self):
        from src.session_queue import _Lease
        slots = [_Lease.take(self.manager.root / f'.slot-{index}.lock') for index in range(2)]
        self.addCleanup(lambda: [slot.close() for slot in slots])
        job = self.start()
        self.manager.close()
        self.manager = self.create_manager()
        current = self.call('review_get', job_id=job['id'])['job']
        self.assertEqual('interrupted', current['status'])
        self.assertFalse(self.manager.workers)
        for slot in slots:
            slot.close()
        self.assertTrue(self.call('review_resume', job_id=job['id'])['ok'])
        self.wait(job['id'])

    def test_three_processes_share_only_two_running_slots(self):
        import queue
        ctx = multiprocessing.get_context('spawn')
        entered, release = ctx.Queue(), ctx.Event()
        processes = [ctx.Process(target=_review_process, args=(str(self.root), self.record['id'], f'process-{index}', entered, release)) for index in range(3)]
        try:
            for process in processes:
                process.start()
            first, second = entered.get(timeout=6), entered.get(timeout=6)
            self.assertNotEqual(first, second)
            with self.assertRaises(queue.Empty):
                entered.get(timeout=.25)
            release.set()
            third = entered.get(timeout=6)
            self.assertEqual(3, len({first, second, third}))
            for process in processes:
                process.join(8)
                self.assertEqual(0, process.exitcode)
        finally:
            release.set()
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(3)
            entered.close()

    def test_model_tools_are_frozen_readonly_and_have_independent_budget(self):
        from src import llm, prompts
        from tests.test_adaptive_driver import response
        from src.evidence_review_runner import model_runtime
        settings.save({'llm_enabled': True, 'agent_stream_enabled': False, 'agent_max_steps': 20})
        with mock.patch.object(prompts, 'agent_system', side_effect=AssertionError('frozen review must not read live skills/memory/context')), \
                mock.patch.object(llm, 'available', return_value=True), mock.patch.object(llm, 'chat', side_effect=[
                response(tool='review.evidence', args={'index': 0}), response('引用材料0：这是合成材料，不能验证结论')]):
            job = self.start(mode='model')
            ready = self.wait(job['id'])
        self.assertEqual('model_advice', ready['result']['method'])
        private = self.manager._input(self.manager._load(job['id']))
        state = json.loads((self.manager.root / 'sessions' / f'{job["session_id"]}.driver.json').read_text(encoding='utf-8'))
        self.assertEqual(8, state['max_steps'])
        self.assertEqual(16000, state['token_budget'])
        runtime = model_runtime(private, self.manager.root)
        self.assertEqual({'review.record', 'review.evidence'}, set(runtime.tools))
        self.assertEqual('deny', runtime._agent_gate(job['session_id'], 'file.write', 'write', {}))
        self.assertFalse(runtime._run_hooks('before_tool', {})['blocked'])
        change = research_notebook.handle({'cmd': 'notebook_save', 'record': {'id': self.record['id'], 'title': '新标题'}})
        self.assertTrue(change['ok'])
        frozen = runtime.tools['review.record'].handler({})
        self.assertEqual(self.record['title'], frozen['record']['title'])
        self.assertTrue(self.call('review_get', job_id=job['id'])['job']['stale'])

    def test_one_corrupt_job_does_not_hide_other_review_results(self):
        job = self.start()
        self.wait(job['id'])
        (self.manager.root / 'rv_broken.json').write_text('[]', encoding='utf-8')
        result = self.call('review_list')
        self.assertTrue(result['ok'], result)
        self.assertEqual(job['id'], result['jobs'][0]['id'])
        self.assertEqual(1, len(result['warnings']))

    def test_large_frozen_material_is_not_embedded_in_polled_job_metadata(self):
        changed = research_notebook.handle({'cmd': 'notebook_save', 'record': {
            'id': self.record['id'], 'method': '方法细节' * 15000}})
        self.record = changed['record']
        job = self.start()
        self.wait(job['id'])
        metadata = self.manager._path(job['id']).read_text(encoding='utf-8')
        self.assertLess(len(metadata), 10000)
        self.assertNotIn('方法细节', metadata)
        frozen = self.manager._input(self.manager._load(job['id']))
        self.assertEqual(self.record['method'], frozen['snapshot']['method'])

    def test_model_process_crash_requires_explicit_resume_and_keeps_unknown_usage(self):
        from src import llm, model_registry
        from tests.test_adaptive_driver import response
        ctx = multiprocessing.get_context('spawn')
        entered = ctx.Event()
        process = ctx.Process(target=_interrupted_model_process, args=(str(self.root), self.record['id'], entered))
        process.start()
        try:
            self.assertTrue(entered.wait(6))
        finally:
            process.terminate()
            process.join(3)
        jobs = self.call('review_list')['jobs']
        self.assertEqual(1, len(jobs))
        self.assertEqual('interrupted', jobs[0]['status'])
        self.assertFalse(self.manager.workers)
        with mock.patch.object(model_registry, 'resolve', return_value=_MODEL_FIXTURE), \
                mock.patch.object(llm, 'chat', side_effect=[response(tool='review.record'), response('恢复后的复核建议')]) as chat:
            self.assertTrue(self.call('review_resume', job_id=jobs[0]['id'])['ok'])
            result = self.wait(jobs[0]['id'])
        self.assertEqual(2, chat.call_count)
        self.assertEqual(3, result['result']['model_calls'])
        self.assertFalse(result['result']['usage_complete'])
        self.assertEqual(1, research_notebook.handle({'cmd': 'notebook_get', 'id': self.record['id']})['record']['version'])

    def test_model_answer_without_reading_frozen_data_is_not_a_ready_review(self):
        from src import llm
        from tests.test_adaptive_driver import response
        settings.save({'llm_enabled': True, 'agent_stream_enabled': False})
        with mock.patch.object(llm, 'available', return_value=True), mock.patch.object(llm, 'chat', return_value=response('全部验证通过')):
            job = self.start(mode='model')
            result = self.wait(job['id'], 'error')
        self.assertFalse(result['can_accept'])
        self.assertIn('没有读取', result['error'])
