from pathlib import Path
import unittest

from scripts.release_check import run_checks


class ReleaseCheckTest(unittest.TestCase):
    def test_release_check_reports_required_assets_and_hard_failure(self):
        root = Path(__file__).resolve().parents[1]
        report = run_checks(root)
        names = {item["name"] for item in report["checks"]}
        self.assertTrue({"核心源码", "工作流配置", "示例数据", "TUI 配置"} <= names)
        self.assertTrue(report["ok"])

    def test_release_check_marks_missing_workflow_as_failure(self):
        root = Path(__file__).resolve().parents[1]
        missing = root / "workflows" / "research_pipeline.yaml"
        backup = missing.read_text(encoding="utf-8")
        try:
            missing.unlink()
            report = run_checks(root)
            self.assertFalse(report["ok"])
        finally:
            missing.write_text(backup, encoding="utf-8")
