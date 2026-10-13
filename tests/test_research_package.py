# -*- coding: utf-8 -*-
"""可复现研究包：manifest 字段、脱敏、缺口标注与命令路由。"""
from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src import feature_commands, service
from src.bridge import handle
from src.orchestrator.engine import TaskContext
from src.research_package import build_manifest, build_package


def _seed_task(topic: str = "测试课题") -> TaskContext:
    ctx = TaskContext("pkgtest1", "research_pipeline", {"topic": topic})
    ctx.status = "done"
    ctx.artifacts = {
        "lit_filter": {"pool": [
            {"id": "w1", "title": "本地材料甲", "year": 2024, "source": "local",
             "sha256": "a" * 64, "pages": 3},
            {"id": "w2", "title": "检索条目乙", "year": 2023, "source": "openalex"},
        ]},
        "extract": {"digest_md": "摘要稿正文"},
        "review": {"summary": "审计通过"},
    }
    service._tasks[ctx.task_id] = ctx
    return ctx


class ResearchPackageTest(unittest.TestCase):
    def setUp(self):
        self.ctx = _seed_task()
        self.tmp = Path(__file__).parent / "_pkg_tmp"
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self):
        service._tasks.pop(self.ctx.task_id, None)
        for path in sorted(self.tmp.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.tmp.rmdir()

    def test_manifest_records_prompt_tools_environment_and_data(self):
        manifest = build_manifest(self.ctx)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["task_id"], "pkgtest1")
        self.assertTrue(manifest["prompt"]["prompt_version"])
        self.assertEqual(manifest["context"]["workflow"], "research_pipeline")
        self.assertIn("python", manifest["environment"])
        self.assertEqual(len(manifest["data"]["materials"]), 2)
        local = manifest["data"]["materials"][0]
        self.assertEqual(local["sha256"], "a" * 64)
        self.assertEqual(local["pages"], 3)

    def test_manifest_marks_missing_outline_and_citations_as_deficits(self):
        manifest = build_manifest(self.ctx)
        codes = {d["code"] for d in manifest["deficits"]}
        self.assertIn("no_outline", codes)
        self.assertIn("no_citations", codes)
        self.assertNotIn("no_pool", codes)
        self.assertNotIn("no_digest", codes)

    def test_package_zip_contains_manifest_artifacts_workflows_and_readme(self):
        result = build_package("pkgtest1", self.tmp)
        zip_path = Path(result["zip_path"])
        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            self.assertIn("README.md", names)
            self.assertIn("manifest.json", names)
            self.assertTrue(any(n.startswith("artifacts/") for n in names))
            self.assertTrue(any(n.startswith("workflows/") for n in names))
            readme = archive.read("README.md").decode("utf-8")
        self.assertEqual(manifest["topic"], "测试课题")
        self.assertIn("复现步骤", readme)
        self.assertIn("workflows/reproduce.yaml", readme)

    def test_settings_snapshot_redacts_secrets(self):
        with mock.patch("src.settings.load",
                        return_value={"llm_base_url": "https://api.example.com/v1",
                                      "llm_api_key": "sk-super-secret",
                                      "embedding_api_key": "emb-secret"}):
            manifest = build_manifest(self.ctx)
        settings = manifest["settings"]
        self.assertIs(settings["llm_api_key"], True)
        self.assertIs(settings["embedding_api_key"], True)
        self.assertNotIn("sk-super-secret", json.dumps(manifest, ensure_ascii=False))
        self.assertNotIn("emb-secret", json.dumps(manifest, ensure_ascii=False))
        self.assertEqual(settings["llm_base_url"], "https://api.example.com/v1")

    def test_unknown_task_raises_clear_error(self):
        with self.assertRaises(ValueError):
            build_package("no-such-task", self.tmp)

    def test_bridge_requires_task_id_and_reports_unknown_task(self):
        missing = handle({"cmd": "research_package"})
        self.assertFalse(missing["ok"])
        self.assertIn("任务 ID", missing["error"])
        unknown = handle({"cmd": "research_package", "task_id": "no-such-task"})
        self.assertFalse(unknown["ok"])
        self.assertIn("不存在", unknown["error"])

    def test_slash_command_routes_to_bridge(self):
        result = feature_commands.execute("package", "pkgtest1")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(Path(result["zip_path"]).is_file())
        self.assertIn("研究包已导出", result["text"])
        Path(result["zip_path"]).unlink()

    def test_command_catalog_lists_package(self):
        names = {item[0] for item in feature_commands.CATALOG}
        self.assertIn("package", names)


if __name__ == "__main__":
    unittest.main()
