import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src import config, settings
from src.command_center import execute


class ProjectCommandsTest(unittest.TestCase):
    def test_project_session_and_file_roundtrip_preserves_path_spaces(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)/'data'), patch.object(settings, '_PATH', Path(folder)/'settings.json'):
            project = Path(folder)/'研究 材料'
            project.mkdir()
            (project/'问题.md').write_text('# 新问题',encoding='utf-8')
            opened = execute('/project open "'+str(project)+'"')
            self.assertTrue(opened['ok'],opened)
            created = execute('/chat new 方法讨论')
            self.assertTrue(created['ok'],created)
            sid = created['session_id']
            attached = execute('/chat attach '+sid+' 问题.md')
            self.assertTrue(attached['ok'],attached)
            self.assertEqual(['问题.md'],attached['session']['attachments'])
            self.assertTrue(execute('/chat archive '+sid)['ok'])
            self.assertTrue(execute('/chat restore '+sid)['ok'])
            exported = execute('/chat share '+sid+' html')
            self.assertTrue(Path(exported['path']).is_file())


if __name__ == '__main__':
    unittest.main()
