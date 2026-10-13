"""Project boundary and real temporary-repository integration tests."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src import config


class ProjectWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'research'
        self.root.mkdir()
        self.data = self.base / 'app-data'
        self.data.mkdir()
        self.patch = patch.object(config, 'DATA_DIR', self.data)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('src.project_workspace'),
                             'Project workspace backend has not been implemented')
        from src import project_workspace
        return project_workspace

    def open(self):
        self.api = self.module()
        return self.api.handle({'cmd': 'project_open', 'path': str(self.root)})['project']

    def call(self, cmd, **kwargs):
        return self.api.handle({'cmd': cmd, 'project_id': self.project['id'], **kwargs})

    def test_open_is_existing_directory_only_deduplicated_and_does_not_touch_project(self):
        self.project = self.open()
        again = self.api.handle({'cmd': 'project_open', 'path': str(self.root / '.')})
        self.assertEqual(self.project['id'], again['project']['id'])
        self.assertEqual([], list(self.root.iterdir()))
        self.assertEqual(1, len(self.api.handle({'cmd': 'project_list'})['projects']))
        with self.assertRaises(ValueError):
            self.api.handle({'cmd': 'project_open', 'path': str(self.base / 'missing')})
        self.assertFalse((self.base / 'missing').exists())

    def test_create_update_select_and_dynamic_data_directory(self):
        self.project = self.open()
        new = self.api.handle({'cmd': 'project_create', 'parent': str(self.base), 'name': '第二项目'})['project']
        self.assertTrue(Path(new['path']).is_dir())
        self.call('project_update', name='研究 A', view='split')
        self.call('project_select')
        listing = self.api.handle({'cmd': 'project_list'})
        self.assertEqual(self.project['id'], listing['active_project_id'])
        self.assertEqual('split', self.api.get_project(self.project['id'])['view'])
        with patch.object(config, 'DATA_DIR', self.base / 'other-data'):
            self.assertEqual([], self.api.handle({'cmd': 'project_list'})['projects'])
        for name in ['../escape', '.', 'research', 'bad/name']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.api.handle({'cmd': 'project_create', 'parent': str(self.base), 'name': name})

    def test_safe_listing_and_traversal_secret_and_link_rejection(self):
        self.project = self.open()
        (self.root / 'paper.md').write_text('# Paper', encoding='utf-8')
        for name in ['.env', '.env.local', 'private.key', 'id_rsa']:
            (self.root / name).write_text('secret', encoding='utf-8')
        (self.root / '.git').mkdir()
        (self.root / 'nested').mkdir()
        self.assertEqual({'paper.md', 'nested'}, {e['name'] for e in self.call('project_files')['entries']})
        (self.base / 'outside.txt').write_text('outside', encoding='utf-8')
        for name in ['../outside.txt', '..\\outside.txt', str(self.base / 'outside.txt'), '.env', '.env.local',
                     'private.key', 'id_rsa', '.git/config', 'paper.md:secret', './.env']:
            with self.subTest(path=name), self.assertRaises(ValueError):
                self.call('project_preview', path=name)
        link = self.root / 'escape'
        if os.name == 'nt':
            command = "New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + str(self.base).replace("'", "''") + "' | Out-Null"
            completed = subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True)
            self.assertEqual(0, completed.returncode, completed.stderr)
        else:
            link.symlink_to(self.base, target_is_directory=True)
        self.addCleanup(lambda: link.rmdir() if os.name == 'nt' else link.unlink())
        with self.assertRaises(ValueError):
            self.call('project_preview', path='escape/outside.txt')

    def test_preview_types_limits_attachment_hash_and_context(self):
        self.project = self.open()
        text = '# 论文\n结果 = 42\n'
        (self.root / '论文.md').write_bytes(text.encode('utf-8'))
        (self.root / 'code.py').write_text('print(42)', encoding='utf-8')
        (self.root / 'table.csv').write_text('a,b\n1,2', encoding='utf-8')
        (self.root / 'photo.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        (self.root / 'large.txt').write_text('x' * 300000, encoding='utf-8')
        (self.root / 'binary.bin').write_bytes(b'\x00\xff')
        for path, kind in [('论文.md', 'markdown'), ('code.py', 'code'), ('table.csv', 'csv'), ('photo.png', 'image')]:
            self.assertEqual(kind, self.call('project_preview', path=path)['kind'])
        large = self.call('project_preview', path='large.txt')
        self.assertTrue(large['truncated'])
        self.assertLessEqual(len(large['content']), 128 * 1024)
        first = self.call('project_attachment', path='论文.md')['attachment']
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), first['sha256'])
        self.assertEqual(text, first['text'])
        context, manifest = self.api.attachment_context(self.project['id'], ['论文.md', 'code.py'])
        self.assertIn(text, context)
        self.assertEqual(2, len(manifest))
        self.assertEqual(first['sha256'], manifest[0]['sha256'])
        (self.root / '论文.md').write_text('changed', encoding='utf-8')
        second = self.call('project_attachment', path='论文.md')['attachment']
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertNotEqual(first['id'], second['id'])
        for paths in [['missing.txt'], ['binary.bin'], ['photo.png']]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.api.attachment_context(self.project['id'], paths)
        with self.assertRaises(ValueError):
            self.api.attachment_context(self.project['id'], ['论文.md'], max_chars=1)

    def git(self, *args, cwd=None):
        result = subprocess.run(['git', '-C', str(cwd or self.root), *args], capture_output=True,
                                encoding='utf-8', errors='replace', timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def init_git(self):
        self.project = self.open()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Temporary Test')
        self.git('config', 'user.email', 'test@example.invalid')

    def test_git_commit_selected_preserves_other_staged_and_pushes_real_bare_remote(self):
        self.init_git()
        (self.root / 'selected.txt').write_text('one', encoding='utf-8')
        (self.root / 'other.txt').write_text('other', encoding='utf-8')
        self.git('add', 'other.txt')
        committed = self.call('project_git_commit', paths=['selected.txt'], message='Add selected file')
        self.assertTrue(committed['ok'])
        self.assertEqual('selected.txt', self.git('ls-tree', '--name-only', 'HEAD'))
        self.assertEqual('other.txt', self.git('diff', '--cached', '--name-only'))
        (self.root / 'selected.txt').write_text('two', encoding='utf-8')
        self.assertIn('two', self.call('project_git_diff', path='selected.txt')['diff'])
        status = self.call('project_git_status')
        self.assertEqual('main', status['branch'])
        self.assertIn('selected.txt', [row['path'] for row in status['status']])
        remote = self.base / 'remote.git'
        self.git('init', '--bare', str(remote))
        self.git('remote', 'add', 'origin', str(remote))
        pushed = self.call('project_git_push', remote='origin', branch='main')
        self.assertTrue(pushed['ok'], pushed)
        self.assertEqual(committed['commit'], self.git('rev-parse', 'refs/heads/main', cwd=remote))
        self.assertEqual('other.txt', self.git('diff', '--cached', '--name-only'))

    def test_git_rejects_directory_wildcard_unknown_remote_and_options_and_redacts(self):
        self.init_git()
        (self.root / 'source.txt').write_text('old\n', encoding='utf-8')
        self.call('project_git_commit', paths=['source.txt'], message='Initial')
        (self.root / 'source.txt').write_text('api_key = "sk-secret-not-for-output"\n', encoding='utf-8')
        self.git('remote', 'add', 'origin', 'https://alice:topsecret@example.invalid/repo.git?token=hidden')
        status_json = json.dumps(self.call('project_git_status'))
        self.assertNotIn('topsecret', status_json)
        self.assertNotIn('hidden', status_json)
        self.assertNotIn('sk-secret-not-for-output', self.call('project_git_diff')['diff'])
        for paths in [['.'], ['*'], ['../outside.txt'], ['.git/config']]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.call('project_git_commit', paths=paths, message='Bad')
        for remote, branch in [('--receive-pack=evil', 'main'), ('missing', 'main'), ('origin', '--all'),
                               ('origin', 'main:other'), ('https://example.invalid/repo', 'main')]:
            with self.subTest(remote=remote, branch=branch), self.assertRaises(ValueError):
                self.call('project_git_push', remote=remote, branch=branch)
        self.git('remote', 'set-url', 'origin', 'ext::evil arg')
        with self.assertRaises(ValueError):
            self.call('project_git_push', remote='origin', branch='main')

    def test_sensitive_directory_names_and_ssh_credentials_are_not_exposed(self):
        self.init_git()
        for name in ['private', 'key', 'secrets']:
            (self.root / name).mkdir()
            (self.root / name / 'data.txt').write_text('secret contents', encoding='utf-8')
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.call('project_preview', path=f'{name}/data.txt')
        self.git('remote', 'add', 'origin', 'ssh://user:passwordvalue@example.invalid/repo')
        self.assertNotIn('passwordvalue', json.dumps(self.call('project_git_status')))

    def test_untracked_diff_and_explicit_deletion_commit(self):
        self.init_git()
        (self.root / 'new.txt').write_text('untracked contents', encoding='utf-8')
        self.assertIn('untracked contents', self.call('project_git_diff', path='new.txt')['diff'])
        self.call('project_git_commit', paths=['new.txt'], message='New file')
        (self.root / 'new.txt').unlink()
        self.call('project_git_commit', paths=['new.txt'], message='Remove file')
        self.assertEqual('', self.git('ls-tree', '--name-only', 'HEAD'))

    def test_push_reports_non_fast_forward_and_does_not_follow_unselected_tags(self):
        self.init_git()
        (self.root / 'file.txt').write_text('one', encoding='utf-8')
        self.call('project_git_commit', paths=['file.txt'], message='Initial')
        remote = self.base / 'remote.git'
        self.git('init', '--bare', str(remote))
        self.git('remote', 'add', 'origin', str(remote))
        self.git('config', 'push.followTags', 'true')
        self.git('tag', '-a', 'private-tag', '-m', 'Local annotated tag')
        self.assertTrue(self.call('project_git_push', remote='origin', branch='main')['ok'])
        self.assertEqual('', self.git('tag', '--list', cwd=remote), 'Only the requested branch may be pushed')
        first = self.git('rev-parse', 'HEAD')
        (self.root / 'file.txt').write_text('two', encoding='utf-8')
        self.call('project_git_commit', paths=['file.txt'], message='Second')
        self.assertTrue(self.call('project_git_push', remote='origin', branch='main')['ok'])
        latest = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/heads/main', first)
        rejected = self.call('project_git_push', remote='origin', branch='main')
        self.assertFalse(rejected['ok'])
        self.assertNotEqual(0, rejected['exit_code'])
        self.assertEqual(latest, self.git('rev-parse', 'refs/heads/main', cwd=remote))


if __name__ == '__main__':
    unittest.main()
