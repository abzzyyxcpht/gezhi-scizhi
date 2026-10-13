import json
from unittest import mock
import tempfile
import unittest
from pathlib import Path

from scripts.verify_release import build_report, configure_output


class VerifyReleaseTest(unittest.TestCase):
    def test_configure_output_requests_utf8_replacement(self):
        stream = mock.Mock()
        with mock.patch("scripts.verify_release.sys.stdout", stream):
            configure_output()
        stream.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_report_has_hard_gates_and_json_safe_fields(self):
        with tempfile.TemporaryDirectory() as d:
            report = build_report(Path(d), [
                {"name": "demo", "returncode": 0, "duration_ms": 4, "stdout": "done"},
            ])
            self.assertTrue(report["ok"])
            self.assertIn("hard_gates", report)
            json.dumps(report, ensure_ascii=False)
