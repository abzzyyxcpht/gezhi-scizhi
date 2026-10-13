import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, settings
from src.agent_runtime import SessionStore
from src.bridge import handle


class BridgeContractTest(unittest.TestCase):
    def test_bridge_returns_json_error_for_unknown_command(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.run(
            [sys.executable, "-m", "src.bridge"],
            input=json.dumps({"cmd": "unknown"}, ensure_ascii=False) + "\n",
            text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=10, env=env,
        )
        row = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertFalse(row["ok"])
        self.assertIn("unknown", row["error"])


class SessionRewindContractTest(unittest.TestCase):
    """session_rewind 桥契约：成功/失败响应形状与 request_id 回显。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for obj, key, value in [(config, 'DATA_DIR', self.root), (settings, '_PATH', self.root / 'settings.json')]:
            patcher = mock.patch.object(obj, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _seed(self, sid='contract01'):
        store = SessionStore(self.root / 'sessions')
        store.append(sid, 'user_message', {'text': '问题一', 'mode': 'agent'})
        store.append(sid, 'assistant_delta', {'step': 1, 'delta': '增'})
        store.append(sid, 'assistant_message', {'content': '回答一'})
        store.append(sid, 'completed', {'status': 'done'})
        store.append(sid, 'user_message', {'text': '问题二', 'mode': 'agent'})
        store.append(sid, 'subagent_started', {'child': 'c1', 'agent': 'literature', 'status': 'running', 'steps': 0, 'tokens': 0})
        store.append(sid, 'subagent_status', {'child': 'c1', 'agent': 'literature', 'status': 'running', 'steps': 1, 'tokens': 4})
        store.append(sid, 'subagent_completed', {'child': 'c1', 'status': 'done', 'steps': 2, 'tokens': 8})
        store.append(sid, 'completed', {'status': 'done'})
        return store

    def test_session_rewind_success_shape(self):
        self._seed()
        row = handle({'cmd': 'session_rewind', 'session_id': 'contract01', 'turns': 1,
                      'request_id': 'req-7'})
        self.assertTrue(row['ok'], row)
        self.assertEqual('req-7', row['request_id'])
        self.assertEqual(4, row['rewound_to'])
        self.assertEqual(5, row['removed'])
        events = SessionStore(self.root / 'sessions').read('contract01')
        self.assertEqual('rewound', events[-1]['payload']['reason'])
        self.assertNotIn('问题二', json.dumps(events, ensure_ascii=False))

    def test_session_rewind_failure_shape(self):
        row = handle({'cmd': 'session_rewind', 'session_id': 'ghost-session', 'turns': 1})
        self.assertFalse(row['ok'])
        self.assertTrue(row['error'])

    def test_session_rewind_rejects_running_and_overflow(self):
        store = SessionStore(self.root / 'sessions')
        store.append('live01', 'user_message', {'text': '运行中', 'mode': 'agent'})
        row = handle({'cmd': 'session_rewind', 'session_id': 'live01', 'turns': 1})
        self.assertFalse(row['ok'])
        self.assertIn('/cancel', row['error'])
        self._seed('contract02')
        row = handle({'cmd': 'session_rewind', 'session_id': 'contract02', 'turns': 5})
        self.assertFalse(row['ok'])
        self.assertIn('用户轮次', row['error'])

    def test_chat_get_carries_stream_and_subagent_events(self):
        self._seed()
        row = handle({'cmd': 'chat_get', 'session_id': 'contract01'})
        self.assertTrue(row['ok'], row)
        types = [e['type'] for e in row['events']]
        for kind in ('assistant_delta', 'subagent_started', 'subagent_status', 'subagent_completed'):
            self.assertIn(kind, types)
