"""The shipping executable can validate itself without opening a window."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.ui import desktop_launcher
from src.version import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]


class FrozenLauncherTest(unittest.TestCase):
    def run_check(self, output, *, resources=None, frozen=False, user_data=None):
        script = """
import sys
from pathlib import Path
from src.ui.desktop_launcher import self_check
raise SystemExit(self_check(Path(sys.argv[1]), resource_root=Path(sys.argv[2]), frozen=sys.argv[3] == 'true'))
"""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        if user_data is not None:
            env["GEZHI_DATA_DIR"] = str(user_data)
        return subprocess.run([sys.executable, "-c", script, str(output), str(resources or ROOT),
                               "true" if frozen else "false"], cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)

    def test_normal_launch_still_calls_flet_with_desktop_main(self):
        from src.ui.desktop import main
        with mock.patch("flet.run") as run:
            self.assertEqual(desktop_launcher.launch([]), 0)
        run.assert_called_once_with(main)

    def test_offline_guard_blocks_network_and_restores_socket_operations(self):
        import socket
        original = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
        with desktop_launcher._offline_network() as state:
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                socket.getaddrinfo('example.invalid', 443)
            with socket.socket() as stream, self.assertRaisesRegex(RuntimeError, 'disabled'):
                stream.connect(('127.0.0.1', 9))
            self.assertEqual(2, state['attempts'])
        self.assertEqual(original, (socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo))

    def test_source_self_check_passes_without_touching_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_data = root / "existing-data"
            user_data.mkdir()
            sentinel = user_data / "settings.json"
            sentinel.write_text('{"fixture": "must not be read or modified"}', encoding="utf-8")
            output = root / "report.json"
            result = self.run_check(output, user_data=user_data)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["ok"], report)
            checks = {item["name"]: item for item in report["checks"]}
            self.assertTrue(checks["runtime_plan"]["ok"])
            self.assertTrue(checks["swarm_plan_cancel"]["ok"])
            for name in ('session_event_projection', 'session_status_compact', 'network_isolation',
                         'product_readiness', 'product_demo_offline', 'pdf_backend', 'markdown_code_preview', 'experimental_defaults', 'experimental_queue', 'experimental_steering', 'evidence_review_inbox', 'history_index', 'composer_drafts', 'tool_outputs', 'shell_owned_execution', 'shell_stream_capture', 'interaction_controls'):
                self.assertIn(name, checks)
                self.assertTrue(checks[name]['ok'])
            self.assertEqual(report["version"], APP_VERSION)
            self.assertEqual(list(user_data.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"fixture": "must not be read or modified"}')

    def test_markdown_preview_check_uses_explicit_settings_and_no_clipboard(self):
        operation = getattr(desktop_launcher, '_markdown_code_preview', None)
        self.assertTrue(callable(operation), 'Missing executable Markdown/code preview check')
        with mock.patch('src.settings.load', side_effect=AssertionError('Unexpected settings read')), \
                mock.patch('flet.Clipboard.set', side_effect=AssertionError('Unexpected clipboard access')) as clipboard:
            self.assertEqual(operation()['code_lines'], 2)
        clipboard.assert_not_called()

    def test_markdown_preview_check_rejects_a_missing_copy_button(self):
        from src.ui.code_preview import CodePreview
        operation = getattr(desktop_launcher, '_markdown_code_preview', None)
        self.assertTrue(callable(operation), 'Missing executable Markdown/code preview check')
        refresh = CodePreview.refresh

        def without_copy_button(preview, *args, **kwargs):
            refresh(preview, *args, **kwargs)
            preview.header.controls.remove(preview.copy_button)

        with mock.patch.object(CodePreview, 'refresh', without_copy_button):
            with self.assertRaisesRegex(ValueError, 'copy button'):
                operation()

    def test_missing_resources_are_reported_and_exit_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "empty-resources"
            resources.mkdir()
            output = root / "report.json"
            result = self.run_check(output, resources=resources, frozen=True)
            self.assertEqual(result.returncode, 1)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(report["ok"])
            failed = {item["name"] for item in report["checks"] if not item["ok"]}
            self.assertIn("resource:workflows/research_pipeline.yaml", failed)
            self.assertIn("resource:assets/gezhi.svg", failed)
            self.assertIn("resource:LICENSE", failed)
            self.assertIn("frozen_desktop_client", failed)
            self.assertTrue(report["error"])


if __name__ == "__main__":
    unittest.main()
