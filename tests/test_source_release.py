from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.prepare_opensource import public_files


class PublicSourceTest(unittest.TestCase):
    def test_same_day_next_version_has_its_own_source_directory(self):
        from scripts import prepare_opensource
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._put(root, 'docs/OPEN_SOURCE_README.md', 'preview')
            original = self._put(root, 'deliverables/scizhi-agent-20260928/keep.txt', 'previous release')
            with patch.object(prepare_opensource, 'ROOT', root):
                self.assertEqual(0, prepare_opensource.main(['--tag', '20260928', '--versioned-dir']))
                with self.assertRaises(FileExistsError):
                    prepare_opensource.main(['--tag', '20260928', '--versioned-dir'])
            self.assertTrue((root / f'deliverables/scizhi-agent-{prepare_opensource.VERSION}-20260928/README.md').exists())
            self.assertEqual('previous release', original.read_text())

    def _put(self, root, relative, content='fixture'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def test_public_source_includes_test_configuration_cleanup_dependency_and_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required = {'pytest.ini', 'scripts/clean_workspace.py', 'tests/test_clean_workspace.py',
                        'tests/fixtures/session_projection_legacy.json', 'assets/ui-design.json'}
            for relative in required:
                self._put(root, relative)
            self.assertTrue(required.issubset({p.relative_to(root).as_posix() for p in public_files(root)}))

    def test_nested_caches_and_credentials_are_excluded_from_public_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            for relative in ['src/main.py', 'tests/fixtures/example.json', 'src/.env', 'src/credentials.json',
                             'tests/.pytest_cache/cache/nodeids', 'src/.ruff_cache/state',
                             'examples/private.key', 'tui/src/.env.local']:
                self._put(root, relative)
            self.assertEqual({'src/main.py', 'tests/fixtures/example.json'},
                             {p.relative_to(root).as_posix() for p in public_files(root)})

    def test_runtime_credentials_and_caches_never_enter_source_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ["src/main.py", "src/__pycache__/main.pyc", "data/settings.json", ".env", "tui/node_modules/x/main.js", "scripts/verify_release.py", "docs/private.md"]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            picked = {p.relative_to(root).as_posix() for p in public_files(root)}
            self.assertEqual(picked, {"src/main.py", "scripts/verify_release.py"})

    def test_windows_shortcuts_target_built_executable(self):
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging/gezhi.nsi").read_text(encoding="utf-8")
        executable = "Gezhi.exe"
        for line in installer.splitlines():
            if "CreateShortcut" in line and "SciZhi.lnk" in line:
                self.assertIn(executable, line)

    def test_rebuild_excludes_stale_files_and_manifest_does_not_hash_itself(self):
        import json
        import zipfile
        from unittest.mock import patch
        from scripts import prepare_opensource
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "docs").mkdir()
            (root / "docs/OPEN_SOURCE_README.md").write_text("public", encoding="utf-8")
            destination = root / "deliverables/scizhi-agent"
            destination.mkdir(parents=True)
            (destination / "private-note.txt").write_text("fixture-only", encoding="utf-8")
            with patch.object(prepare_opensource, "ROOT", root), patch.object(prepare_opensource, "public_files", return_value=[]):
                prepare_opensource.main()
                prepare_opensource.main()
            manifest = json.loads((destination / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertNotIn("SOURCE_MANIFEST.json", manifest)
            self.assertNotIn("private-note.txt", manifest)
            with zipfile.ZipFile(root / f"deliverables/SciZhi-Agent-{prepare_opensource.VERSION}-source.zip") as archive:
                names = archive.namelist()
                self.assertEqual(len(names), len(set(names)))
                self.assertFalse(any("private-note" in name for name in names))

    def test_dated_release_contains_current_sources_and_docs_without_touching_old_deliverables(self):
        import json
        import zipfile
        from scripts import prepare_opensource
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._put(root, 'docs/OPEN_SOURCE_README.md', 'Current release documentation')
            self._put(root, 'pytest.ini', '[pytest]\ntestpaths = tests\n')
            self._put(root, 'scripts/clean_workspace.py', 'def clean_workspace(): pass\n')
            self._put(root, 'src/session_projection.py', 'CURRENT_INCREMENTAL_PROJECTION = True\n')
            self._put(root, 'tests/fixtures/session_projection_legacy.json', '{}')
            docs = ['docs/harness-controls-2026-09-27.md', 'docs/research/2026-09-27-harness-matrix.md',
                    'docs/research/session-projection-performance-2026-09-27.json']
            for name in docs:
                self._put(root, name, 'CURRENT PUBLIC DOC')
            for name in ['data/settings.json', 'tui/node_modules/private.js', 'docs/private.md']:
                self._put(root, name, 'DO NOT DISTRIBUTE')
            old_directory_file = self._put(root, 'deliverables/scizhi-agent/keep.txt', 'old source remains')
            old_archive = self._put(root, 'deliverables/SciZhi-Agent-0.5.0-source.zip', 'old archive remains')
            with patch.object(prepare_opensource, 'ROOT', root):
                self.assertEqual(0, prepare_opensource.main(['--tag', '20260927']))
            destination = root / 'deliverables/scizhi-agent-20260927'
            archive = root / f'deliverables/SciZhi-Agent-{prepare_opensource.VERSION}-20260927-source.zip'
            self.assertTrue(archive.is_file())
            self.assertEqual('Current release documentation', (destination / 'README.md').read_text(encoding='utf-8'))
            manifest = json.loads((destination / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
            for name in [*docs, 'pytest.ini', 'scripts/clean_workspace.py',
                         'src/session_projection.py', 'tests/fixtures/session_projection_legacy.json']:
                self.assertIn(name, manifest)
            self.assertNotIn('SOURCE_MANIFEST.json', manifest)
            self.assertEqual('old source remains', old_directory_file.read_text(encoding='utf-8'))
            self.assertEqual('old archive remains', old_archive.read_text(encoding='utf-8'))
            with zipfile.ZipFile(archive) as bundle:
                self.assertFalse(any('node_modules' in name or 'data/settings' in name or 'docs/private' in name
                                     for name in bundle.namelist()))
                self.assertTrue(all(name.startswith(f'scizhi-agent-{prepare_opensource.VERSION}-20260927/') for name in bundle.namelist()))
            before = archive.read_bytes()
            with patch.object(prepare_opensource, 'ROOT', root), self.assertRaises(FileExistsError):
                prepare_opensource.main(['--tag', '20260927'])
            self.assertEqual(before, archive.read_bytes())

    def test_dated_release_refuses_existing_archive_even_without_destination(self):
        from scripts import prepare_opensource
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._put(root, 'docs/OPEN_SOURCE_README.md', 'public')
            self._put(root, 'scripts/main.py')
            archive = self._put(root, f'deliverables/SciZhi-Agent-{prepare_opensource.VERSION}-20260927-source.zip', 'keep')
            with patch.object(prepare_opensource, 'ROOT', root), self.assertRaises(FileExistsError):
                prepare_opensource.main(['--tag', '20260927'])
            self.assertEqual('keep', archive.read_text(encoding='utf-8'))
            self.assertFalse((root / 'deliverables/scizhi-agent-20260927').exists())

    def test_tag_requires_a_real_calendar_date_and_never_creates_paths_on_failure(self):
        import contextlib
        import io
        from scripts import prepare_opensource
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tag in ('2026-09-27', '../private', '20260230', '202609'):
                with self.subTest(tag=tag), patch.object(prepare_opensource, 'ROOT', root), \
                        contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    prepare_opensource.main(['--tag', tag])
            self.assertFalse((root / 'deliverables').exists())
