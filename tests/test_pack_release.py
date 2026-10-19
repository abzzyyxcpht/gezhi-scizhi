"""Installer publishing requires a passing frozen check and preserves old releases."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
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
                    'session_status_compact', 'network_isolation', 'product_readiness', 'product_demo_offline', 'pdf_backend', 'markdown_code_preview', 'execution_plan', 'experimental_defaults', 'experimental_queue', 'experimental_steering', 'evidence_review_inbox', 'history_index', 'composer_drafts', 'tool_outputs', 'shell_owned_execution', 'shell_stream_capture', 'interaction_controls')]}
            Path(command[-1]).write_text(json.dumps(report), encoding='utf-8')
        else:
            self.assertIn(f'-DAPP_VER={APP_VERSION}', command)
            manifest = (Path(kwargs['cwd']) / 'uninstall-files.nsh').read_text(encoding='utf-8')
            self.assertIn('Delete "$INSTDIR\\Gezhi.exe"', manifest)
            self.assertNotIn('RMDir /r', manifest)
            preflight = (Path(kwargs['cwd']) / 'uninstall-preflight.nsh').read_text(encoding='utf-8')
            self.assertIn('Push "$INSTDIR"', preflight)
            self.assertIn('Call un.AssertPlainPath', preflight)
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

    def test_missing_markdown_preview_check_cannot_publish(self):
        def missing_preview(command, **kwargs):
            result = self.successful_run(command, **kwargs)
            if '--self-check' in command:
                report_path = Path(command[-1])
                report = json.loads(report_path.read_text(encoding='utf-8'))
                report['checks'] = [item for item in report['checks'] if item['name'] != 'markdown_code_preview']
                report_path.write_text(json.dumps(report), encoding='utf-8')
            return result

        with mock.patch.object(pack_release, 'ROOT', self.root), \
                mock.patch.object(pack_release.subprocess, 'run', side_effect=missing_preview) as run:
            with self.assertRaisesRegex(SystemExit, 'self-check'):
                pack_release.main([])
        self.assertEqual(1, run.call_count)
        self.assertFalse((self.root / f'dist/SciZhi-Agent-Setup-{APP_VERSION}.exe').exists())

    def test_release_version_is_shared_and_build_paths_are_version_scoped(self):
        from src import version
        self.assertEqual(version.APP_VERSION, version.APP_VERSION)  # 版本唯一来源：src/version.py
        self.assertTrue(version.APP_VERSION.count('.') == 2, '语义化三段版本号')
        self.assertEqual(version.APP_VERSION, pack_release.APP_VER)
        self.assertEqual(version.APP_VERSION, prepare_opensource.VERSION)
        batch = (Path(__file__).resolve().parents[1] / 'scripts/build_desktop.bat').read_text(encoding='utf-8')
        self.assertIn('--distpath', batch)
        self.assertIn('--workpath', batch)

    def test_uninstall_manifest_lists_only_package_files_and_deepest_directories_first(self):
        self.put(f'dist/{APP_VERSION}/Gezhi/_internal/nested/研究 $R0.txt', b'owned')
        self.put(f'dist/{APP_VERSION}/Gezhi/_internal/${{NSISDIR}}.txt', b'owned')
        manifest = pack_release._uninstall_manifest(self.app)
        self.assertIn('StrCpy $R9 "$$"', manifest)
        self.assertIn('Delete "$INSTDIR\\_internal\\nested\\研究 $R9R0.txt"', manifest)
        self.assertIn('Delete "$INSTDIR\\_internal\\$R9{NSISDIR}.txt"', manifest)
        self.assertLess(manifest.index('RMDir "$INSTDIR\\_internal\\nested"'),
                        manifest.index('RMDir "$INSTDIR\\_internal"'))
        self.assertNotIn('RMDir /r', manifest)
        self.assertNotIn('*', manifest)
        self.assertNotIn('RMDir "$INSTDIR"', manifest)

    def test_nsis_relative_paths_escape_literals_and_reject_unsafe_targets(self):
        self.assertEqual(pack_release._nsis_relative('nested/a$R0"b.txt'), r'nested\a$R9R0$\"b.txt')
        for unsafe in ('../outside.txt', '/absolute.txt', 'C:/outside.txt', 'nested/../../escape',
                       'nested/*', 'nested/?.txt', 'nested/line\nfeed', 'nested/line\rfeed', 'nested/ads:stream'):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                pack_release._nsis_relative(unsafe)

    def test_uninstall_preflight_checks_parents_before_children_and_before_any_delete(self):
        self.put(f'dist/{APP_VERSION}/Gezhi/_internal/nested/a$R0.txt', b'owned')
        preflight = pack_release._uninstall_preflight(self.app)
        targets = ['Push "$INSTDIR"', 'Push "$INSTDIR\\_internal"',
                   'Push "$INSTDIR\\_internal\\nested"', 'Push "$INSTDIR\\_internal\\nested\\a$R9R0.txt"']
        positions = [preflight.index(target) for target in targets]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('Delete ', preflight)
        source = (Path(__file__).resolve().parents[1] / 'packaging/gezhi.nsi').read_text(encoding='utf-8')
        uninstall = source.split('Section "Uninstall"', 1)[1]
        self.assertLess(uninstall.index('!include "uninstall-preflight.nsh"'), uninstall.index('Delete '))
        self.assertIn('GetFileAttributesW(w r0)', source)
        self.assertIn('IntOp $1 $1 & 0x400', source)

    def test_uninstall_manifest_rejects_linked_files_outside_application(self):
        target = self.put('outside.txt', b'keep outside')
        link = self.app / 'linked.txt'
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f'Symlink creation unavailable: {exc}')
        with self.assertRaisesRegex(ValueError, 'link|outside'):
            pack_release._uninstall_manifest(self.app)

    def test_nsis_installer_uses_versioned_icons_and_isolated_marker(self):
        source = (Path(__file__).resolve().parents[1] / 'packaging/gezhi.nsi').read_text(encoding='utf-8')
        self.assertIn('File /oname=gezhi-${APP_VER}.ico "gezhi.ico"', source)
        self.assertEqual(source.count('"" "$INSTDIR\\gezhi-${APP_VER}.ico" 0'), 2)
        self.assertIn('!include "uninstall-files.nsh"', source)
        self.assertNotIn('RMDir /r', source)
        self.assertIn('GetCommandLineW()', source)
        self.assertIn('"/D="', source)
        self.assertIn('"/ISOLATED"', source)
        self.assertIn('PathGetDriveNumberW(w r1)', source)
        self.assertIn('StrCpy $2 "$1" 1 2', source)
        self.assertIn('StrCmp $2 $IsolatedDir 0 isolated_invalid', source)
        self.assertIn('SetSilent silent', source)
        self.assertIn('IfFileExists "$INSTDIR\\.scizhi-isolated" uninstall_files', source)

    def compile_installer_fixture(self, *, protect_global=False):
        root = Path(__file__).resolve().parents[1]
        compiler = root / 'tools/nsis-3.10/Bin/makensis.exe'
        if not compiler.is_file():
            self.skipTest('Bundled Windows NSIS compiler unavailable')
        stage = self.root / 'nsis-compile'
        stage.mkdir()
        self.put(f'dist/{APP_VERSION}/Gezhi/_internal/研究 $R0.txt', b'owned')
        self.put(f'dist/{APP_VERSION}/Gezhi/_internal/${{NSISDIR}}.txt', b'owned')
        shutil.copytree(self.app, stage / 'app')
        shutil.copy2(root / 'assets/gezhi.ico', stage / 'gezhi.ico')
        source = (root / 'packaging/gezhi.nsi').read_text(encoding='utf-8')
        if protect_global:
            # Runtime QA must fail before any fallback can touch the real installation or shell entries.
            source = source.replace('normal_destination:\n',
                'normal_destination:\n    SetErrorLevel 99\n    Abort "Unexpected normal installation in test"\n')
            source = source.replace('    CreateDirectory "$SMPROGRAMS\\格致"',
                '    SetErrorLevel 99\n    Abort "Unexpected global installation in test"\n'
                '    CreateDirectory "$SMPROGRAMS\\格致"')
            source = source.replace('    Delete "$SMPROGRAMS\\格致\\格致 SciZhi.lnk"',
                '    SetErrorLevel 99\n    Abort "Unexpected global uninstall in test"\n'
                '    Delete "$SMPROGRAMS\\格致\\格致 SciZhi.lnk"')
        (stage / 'gezhi.nsi').write_text(source, encoding='utf-8')
        manifest = pack_release._uninstall_manifest(stage / 'app')
        # Windows cannot create quote-containing filenames, but the generated NSIS string must still be safe.
        if not protect_global:
            manifest += f'Delete "$INSTDIR\\{pack_release._nsis_relative(chr(34) + "literal.txt")}"\n'
        (stage / 'uninstall-files.nsh').write_text(manifest, encoding='utf-8')
        (stage / 'uninstall-preflight.nsh').write_text(pack_release._uninstall_preflight(stage / 'app'), encoding='utf-8')
        result = subprocess.run([str(compiler), '-WX', '-INPUTCHARSET', 'UTF8', f'-DAPP_VER={APP_VERSION}', 'gezhi.nsi'],
                                cwd=stage, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        executable = stage / f'SciZhi-Agent-Setup-{APP_VERSION}.exe'
        self.assertGreater(executable.stat().st_size, 0)
        return executable

    def test_nsis_compiles_real_script_with_generated_uninstall_manifest(self):
        self.compile_installer_fixture()

    def test_real_isolated_install_and_uninstall_preserve_unknown_files(self):
        installer = self.compile_installer_fixture(protect_global=True)
        target = self.root / '独立安装'
        result = subprocess.run([str(installer), '/S', '/ISOLATED', '/D=' + str(target)], timeout=30)
        self.assertEqual(result.returncode, 0, 'New isolated installation must accept a nonexistent absolute target')
        self.assertEqual((target / 'Gezhi.exe').read_bytes(), b'fixture exe')
        self.assertEqual((target / '_internal/研究 $R0.txt').read_bytes(), b'owned')
        self.assertEqual((target / '_internal/${NSISDIR}.txt').read_bytes(), b'owned')
        self.assertTrue((target / f'gezhi-{APP_VERSION}.ico').is_file())
        self.assertTrue((target / '.scizhi-isolated').is_file())
        (target / '用户保留.txt').write_text('keep user content', encoding='utf-8')
        (target / '_internal/user-notes.txt').write_text('keep nested content', encoding='utf-8')
        # Run an owned copy synchronously; _?= points only at this test's installation.
        runner = self.root / 'isolated-uninstaller.exe'
        shutil.copy2(target / '卸载.exe', runner)
        removed = subprocess.run([str(runner), '/S', '_?=' + str(target)], timeout=30)
        self.assertEqual(removed.returncode, 0)
        self.assertFalse((target / 'Gezhi.exe').exists())
        self.assertFalse((target / '卸载.exe').exists())
        self.assertFalse((target / f'gezhi-{APP_VERSION}.ico').exists())
        self.assertFalse((target / '.scizhi-isolated').exists())
        self.assertEqual((target / '用户保留.txt').read_text(encoding='utf-8'), 'keep user content')
        self.assertEqual((target / '_internal/user-notes.txt').read_text(encoding='utf-8'), 'keep nested content')

    def test_real_isolated_install_rejects_missing_relative_and_existing_destinations(self):
        installer = self.compile_installer_fixture(protect_global=True)
        existing = self.root / 'existing'
        existing.mkdir()
        sentinel = existing / 'keep.txt'
        sentinel.write_bytes(b'keep')
        for extra in ([], ['/D=C:relative'], ['/D=\\root-relative'], ['/D=' + str(existing)]):
            with self.subTest(extra=extra):
                result = subprocess.run([str(installer), '/S', '/ISOLATED', *extra], timeout=30)
                self.assertEqual(result.returncode, 2)
        self.assertEqual(list(existing.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_real_isolated_install_supports_spaced_paths_and_removes_an_empty_installation(self):
        installer = self.compile_installer_fixture(protect_global=True)
        target = self.root / '独立 安装'
        # NSIS /D= and _?= must be the final unquoted parameter, including when paths contain spaces.
        result = subprocess.run(f'"{installer}" /S /ISOLATED /D={target}', timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertTrue((target / '.scizhi-isolated').is_file())
        runner = self.root / 'isolated-uninstaller.exe'
        shutil.copy2(target / '卸载.exe', runner)
        removed = subprocess.run(f'"{runner}" /S _?={target}', timeout=30)
        self.assertEqual(removed.returncode, 0)
        self.assertFalse(target.exists())

    def test_real_uninstall_rejects_a_swapped_junction_before_removing_any_files(self):
        installer = self.compile_installer_fixture(protect_global=True)
        import _winapi
        target = self.root / 'junction-install'
        installed = subprocess.run([str(installer), '/S', '/ISOLATED', '/D=' + str(target)], timeout=30)
        self.assertEqual(installed.returncode, 0)
        internal = target / '_internal'
        original = self.root / 'original-internal'
        outside = self.root / 'outside-installation'
        outside.mkdir()
        sentinel = outside / '研究 $R0.txt'
        sentinel.write_bytes(b'outside content must survive')
        # Both moved directories and the junction target belong to this test's fresh temp root.
        self.assertTrue(internal.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(original.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(outside.resolve().is_relative_to(self.root.resolve()))
        internal.rename(original)
        _winapi.CreateJunction(str(outside), str(internal))
        runner = self.root / 'isolated-uninstaller.exe'
        shutil.copy2(target / '卸载.exe', runner)
        refused = subprocess.run([str(runner), '/S', '_?=' + str(target)], timeout=30)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual((target / 'Gezhi.exe').read_bytes(), b'fixture exe')
        self.assertTrue((target / '.scizhi-isolated').is_file())
        self.assertTrue((target / '卸载.exe').is_file())
        self.assertEqual(sentinel.read_bytes(), b'outside content must survive')
        self.assertTrue(internal.is_junction())
