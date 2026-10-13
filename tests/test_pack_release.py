"""Installer publishing requires a passing frozen check and preserves old releases."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import pack_release, prepare_opensource
from src.version import APP_VERSION


class PackReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'dist' / APP_VERSION / 'Gezhi'
        self.put(f'dist/{APP_VERSION}/Gezhi/Gezhi.exe', b'fixture exe')
        self.put('assets/gezhi.ico', b'fixture icon')
        self.put('packaging/gezhi.nsi', b'fixture installer')
        self.put('tools/nsis-3.10/Bin/makensis.exe', b'fixture compiler')
        self.old = self.put('dist/SciZhi-Agent-Setup-0.5.0.exe', b'old installer')

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def successful_run(self, command, **kwargs):
        if '--self-check' in command:
            report = {'ok': True, 'version': APP_VERSION, 'frozen': True, 'checks': [
                {'name': name, 'ok': True} for name in ('imports', 'version', 'data_isolation', 'runtime_plan',
                    'swarm_plan_cancel', 'frozen_desktop_client', 'session_event_projection',
                    'session_status_compact', 'network_isolation', 'product_readiness', 'product_demo_offline', 'pdf_backend', 'experimental_defaults', 'experimental_queue', 'experimental_steering', 'evidence_review_inbox', 'history_index', 'composer_drafts', 'tool_outputs', 'shell_owned_execution', 'shell_stream_capture', 'interaction_controls')]}
            Path(command[-1]).write_text(json.dumps(report), encoding='utf-8')
        else:
            self.assertIn(f'-DAPP_VER={APP_VERSION}', command)
            (Path(kwargs['cwd']) / f'SciZhi-Agent-Setup-{APP_VERSION}.exe').write_bytes(b'new verified installer')
        return SimpleNamespace(returncode=0, stdout='fixture output')

    def test_pack_runs_frozen_check_before_nsis_and_preserves_older_installer(self):
        with mock.patch.object(pack_release, 'ROOT', self.root), \
                mock.patch.object(pack_release.subprocess, 'run', side_effect=self.successful_run) as run:
            self.assertEqual(0, pack_release.main([]))
        self.assertIn('--self-check', run.call_args_list[0].args[0])
        self.assertEqual(2, run.call_count)
        output = self.root / f'dist/SciZhi-Agent-Setup-{APP_VERSION}.exe'
        self.assertEqual(b'new verified installer', output.read_bytes())
        self.assertEqual(b'old installer', self.old.read_bytes())
        self.assertTrue(output.with_suffix('.exe.sha256').is_file())
        report = json.loads(output.with_suffix('.self-check.json').read_text(encoding='utf-8'))
        self.assertTrue(report['ok'])

    def test_failed_frozen_check_never_runs_nsis_or_publishes(self):
        with mock.patch.object(pack_release, 'ROOT', self.root), \
                mock.patch.object(pack_release.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='failed')) as run:
            with self.assertRaisesRegex(SystemExit, 'self-check'):
                pack_release.main([])
        self.assertEqual(1, run.call_count)
        self.assertFalse((self.root / f'dist/SciZhi-Agent-Setup-{APP_VERSION}.exe').exists())
        self.assertEqual(b'old installer', self.old.read_bytes())

    def test_stale_or_incomplete_self_check_cannot_publish(self):
        for patch in ({'version': '0.5.0'}, {'frozen': False}, {'checks': []}):
            with self.subTest(patch=patch):
                def wrong_report(command, **kwargs):
                    result = self.successful_run(command, **kwargs)
                    report_path = Path(command[-1])
                    report = json.loads(report_path.read_text(encoding='utf-8'))
                    report.update(patch)
                    report_path.write_text(json.dumps(report), encoding='utf-8')
                    return result
                with mock.patch.object(pack_release, 'ROOT', self.root), \
                        mock.patch.object(pack_release.subprocess, 'run', side_effect=wrong_report) as run:
                    with self.assertRaisesRegex(SystemExit, 'self-check'):
                        pack_release.main([])
                self.assertEqual(1, run.call_count)

    def test_existing_current_installer_is_not_overwritten(self):
        path = self.put(f'dist/SciZhi-Agent-Setup-{APP_VERSION}.exe', b'keep this release')
        with mock.patch.object(pack_release, 'ROOT', self.root), mock.patch.object(pack_release.subprocess, 'run') as run:
            with self.assertRaises(FileExistsError):
                pack_release.main([])
        run.assert_not_called()
        self.assertEqual(b'keep this release', path.read_bytes())

    def test_release_version_is_shared_and_build_paths_are_version_scoped(self):
        from src import version
        self.assertEqual(version.APP_VERSION, version.APP_VERSION)  # 版本唯一来源：src/version.py
        self.assertTrue(version.APP_VERSION.count('.') == 2, '语义化三段版本号')
        self.assertEqual(version.APP_VERSION, pack_release.APP_VER)
        self.assertEqual(version.APP_VERSION, prepare_opensource.VERSION)
        batch = (Path(__file__).resolve().parents[1] / 'scripts/build_desktop.bat').read_text(encoding='utf-8')
        self.assertIn('--distpath', batch)
        self.assertIn('--workpath', batch)
