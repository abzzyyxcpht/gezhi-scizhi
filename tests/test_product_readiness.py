"""First-run readiness is local observation; the explicit demo is offline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest import mock

from src import config, llm, model_registry, settings
from src import product_readiness as product


class ProductReadinessTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'workflows').mkdir()
        (root / 'workflows/literature_scan.yaml').write_text('fixture', encoding='utf-8')
        for patcher in [mock.patch.object(config, 'ROOT_DIR', root),
                        mock.patch.object(config, 'LLM_ENABLED', True, create=True),
                        mock.patch.object(settings, '_PATH', root / 'settings.json')]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.profile = {'model': 'fixture-model', 'base_url': 'http://127.0.0.1:9/v1',
                        'api_key': 'secret-not-for-display', 'protocol': 'chat_completions'}

    def read(self, profile=None):
        with mock.patch.object(model_registry, 'resolve', return_value=self.profile if profile is None else profile):
            return product.readiness()

    def test_configured_is_not_connection_verified_and_does_not_call_network(self):
        with mock.patch('httpx.Client.request', side_effect=AssertionError('network')), \
                mock.patch.object(settings, 'save', side_effect=AssertionError('write')):
            result = self.read()
        self.assertEqual('configured', result['model']['status'])
        self.assertFalse(result['model']['connection_verified'])
        self.assertTrue(result['model']['execution_ready'])
        self.assertEqual('model', result['agent_mode'])
        self.assertEqual('/demo', result['suggestions']['offline_command'])
        serialized = json.dumps(result)
        self.assertNotIn('secret-not-for-display', serialized)
        self.assertNotIn('127.0.0.1', serialized)
        self.assertFalse(settings._PATH.exists())

    def test_missing_disabled_and_invalid_are_distinct(self):
        self.assertEqual('not_configured', self.read({})['model']['status'])
        with mock.patch.object(config, 'LLM_ENABLED', False):
            self.assertEqual('disabled', self.read()['model']['status'])
        invalid = self.read({**self.profile, 'base_url': 'https://user:private@example.com?api_key=secret'})
        self.assertEqual('invalid', invalid['model']['status'])
        self.assertEqual('rules', invalid['agent_mode'])
        self.assertNotIn('private', json.dumps(invalid))

    def test_stage_route_errors_are_actionable_without_private_error_echo(self):
        with mock.patch.object(model_registry, 'resolve', side_effect=ValueError('secret-private-profile')):
            result = product.readiness()
        self.assertEqual('invalid', result['model']['status'])
        self.assertNotIn('secret-private', json.dumps(result))

    def test_demo_has_explicit_offline_contract_without_changing_preferences(self):
        with mock.patch('src.workbench.handle', return_value={'ok': True, 'task_id': 'demo01'}) as call, \
                mock.patch.object(settings, 'save', side_effect=AssertionError('settings mutated')):
            result = product.start_demo()
        self.assertEqual(True, call.call_args.args[0]['offline'])
        self.assertEqual('literature_scan', call.call_args.args[0]['workflow_id'])
        self.assertTrue(result['offline'])
        self.assertIn('合成样例', result['notice'])

    def test_missing_demo_resources_disable_the_button(self):
        (config.ROOT_DIR / 'workflows/literature_scan.yaml').unlink()
        self.assertFalse(self.read()['research']['offline_available'])
        with self.assertRaises(ValueError):
            product.start_demo()

    def test_model_available_rejects_invalid_endpoint_locally(self):
        with mock.patch.object(model_registry, 'resolve', return_value={**self.profile, 'base_url': 'file:///private'}):
            self.assertFalse(llm.available('planning'))

    def test_invalid_ports_are_not_reported_as_ready(self):
        for url in ('http://localhost:notaport', 'http://localhost:70000', 'http://localhost:0'):
            profile = {**self.profile, 'base_url': url}
            with self.subTest(url=url), mock.patch.object(model_registry, 'resolve', return_value=profile):
                self.assertFalse(llm.available('planning'))
                self.assertEqual('invalid', product.readiness()['model']['status'])


class OfflineRankingTest(unittest.TestCase):
    def test_offline_scoring_never_constructs_configured_embedding_or_calls_llm(self):
        from src.agents import fixtures, literature
        with mock.patch('src.embedding.get_provider', side_effect=AssertionError('remote embedding')) as provider, \
                mock.patch.object(literature, '_llm_refine', side_effect=AssertionError('model request')):
            result = literature.score_pool('language model agents', fixtures.demo_works('language model agents'),
                                           offline=True)
        provider.assert_not_called()
        self.assertEqual('lexical', result['funnel']['tier'])

    def test_explicit_embedding_evaluation_still_measures_embedding_without_chat(self):
        from scripts import eval_retrieval
        from src.agents import fixtures
        provider = mock.Mock(model='fixture-embedding')
        provider.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        with mock.patch('src.embedding.get_provider', return_value=provider), \
                mock.patch.object(llm, 'chat', side_effect=AssertionError('evaluation must not chat')):
            result = eval_retrieval._mode_report('language model agents', fixtures.demo_works('agents'), .18)
        provider.embed.assert_called_once()
        self.assertEqual('lexical+embedding', result['funnel']['tier'])

    def test_full_offline_pipeline_makes_zero_network_attempts_with_configured_endpoints(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
import contextlib, io, json, sys
from pathlib import Path
attempts = []
def deny_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
        attempts.append(event)
        raise RuntimeError('offline test forbids network')
sys.addaudithook(deny_network)
from scripts.demo_run import run_demo
with contextlib.redirect_stdout(io.StringIO()):
    result = run_demo(out_dir=Path(sys.argv[1])/'exports', offline=True)
assert not attempts, attempts
assert result['status'] == 'done', result
print(json.dumps({'network_attempts':len(attempts),'status':result['status']}))
'''
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, GEZHI_DATA_DIR=temporary, PYTHONIOENCODING='utf-8',
                       GEZHI_LLM_BASE_URL='http://127.0.0.1:9/v1', GEZHI_LLM_MODEL='configured',
                       GEZHI_EMB_BASE_URL='http://127.0.0.1:9/v1', GEZHI_EMB_MODEL='configured')
            run = subprocess.run([sys.executable, '-c', script, temporary], cwd=root, env=env,
                                 capture_output=True, text=True, encoding='utf-8', timeout=40)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(0, json.loads(run.stdout)['network_attempts'])


class ProductBridgeTest(unittest.TestCase):
    def test_local_and_remote_readiness_and_demo_boundaries(self):
        from src import bridge, remote_control
        with mock.patch.object(product, 'readiness', return_value={'version': '0.6.0'}):
            result = bridge.handle({'cmd': 'product_readiness', 'request_id': 'ready'})
        self.assertEqual('ready', result['request_id'])
        self.assertEqual('0.6.0', result['readiness']['version'])
        for command in ('product_readiness', 'product_demo'):
            self.assertEqual({'cmd': command}, remote_control._validated({'cmd': command}))
            with self.assertRaises(ValueError):
                remote_control._validated({'cmd': command, 'offline': False})
        self.assertIn('product_readiness', remote_control._READ_ONLY)
        self.assertNotIn('product_demo', remote_control._READ_ONLY)
