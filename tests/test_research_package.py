# -*- coding: utf-8 -*-
"""可复现研究包：manifest 字段、脱敏、缺口标注与命令路由。"""
from __future__ import annotations

import json
import copy
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
        settings_patch = mock.patch('src.settings.load', return_value={})
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
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

    def test_manifest_uses_workflow_citation_entries_and_lists_review_frame(self):
        self.ctx.artifacts["review_frame"] = {"outline_md": "综述框架"}
        self.ctx.artifacts["citation"] = {"entries": [{"text": "[1] Fixture"}]}
        manifest = build_manifest(self.ctx)
        self.assertEqual(manifest["evidence"]["citations"], 1)
        self.assertNotIn("no_citations", {item["code"] for item in manifest["deficits"]})
        entries = {item["key"]: item["label"] for item in manifest["data"]["artifacts"]}
        self.assertEqual(entries["review_frame"], "综述框架")
        self.assertEqual(entries["citation"], "引用格式")

    def test_stale_flat_citations_do_not_override_workflow_entries(self):
        self.ctx.artifacts["citation"] = {"entries": []}
        self.ctx.artifacts["citations"] = [{"text": "outdated flattened view"}]
        manifest = build_manifest(self.ctx)
        self.assertEqual(manifest["evidence"]["citations"], 0)
        self.assertIn("no_citations", {item["code"] for item in manifest["deficits"]})

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

    def test_package_redacts_connection_keys_without_mutating_settings(self):
        original = {
            'llm_api_key': 'fake-primary-secret',
            'embedding_api_key': '',
            'model_connections': [
                {'id': 'remote', 'label': 'Research', 'protocol': 'responses',
                 'base_url': 'https://models.example.org/v1', 'api_key': 'fake-nested-secret'},
                {'id': 'local', 'base_url': 'http://localhost:11434', 'api_key': ''},
            ],
            'model_profiles': [{'id': 'reasoner', 'connection_id': 'remote', 'model': 'research-model'}],
            'model_routes': {'review': 'reasoner'},
            'agent_token_budget': 100000,
        }
        unchanged = copy.deepcopy(original)
        with mock.patch('src.settings.load', return_value=original):
            result = build_package(self.ctx.task_id, self.tmp)
        self.assertEqual(original, unchanged)
        safe = result['manifest']['settings']
        self.assertIs(safe['llm_api_key'], True)
        self.assertIs(safe['embedding_api_key'], False)
        self.assertIs(safe['model_connections'][0]['api_key'], True)
        self.assertIs(safe['model_connections'][0]['has_api_key'], True)
        self.assertIs(safe['model_connections'][1]['api_key'], False)
        self.assertIs(safe['model_connections'][1]['has_api_key'], False)
        self.assertEqual(safe['model_profiles'], original['model_profiles'])
        self.assertEqual(safe['model_routes'], original['model_routes'])
        self.assertEqual(safe['agent_token_budget'], 100000)
        safe['model_profiles'][0]['model'] = 'changed export only'
        self.assertEqual(original, unchanged)
        with zipfile.ZipFile(result['zip_path']) as archive:
            for name in archive.namelist():
                data = archive.read(name)
                self.assertNotIn(b'fake-primary-secret', data, name)
                self.assertNotIn(b'fake-nested-secret', data, name)

    def test_legacy_endpoint_credentials_are_omitted_from_manifest(self):
        original = {
            'llm_base_url': 'https://fake-user:fake-password@api.example.org/v1?key=fake-query#fake-fragment',
            'embedding_base_url': 'https://embed.example.org/v1?api-version=2026&signature=fake-signature',
            'model_connections': [{'id': 'legacy-file',
                                   'base_url': 'https://old-user:old-pass@old.example.org/v1?token=old-token',
                                   'api_key': 'fake-legacy-key'}],
        }
        unchanged = copy.deepcopy(original)
        with mock.patch('src.settings.load', return_value=original):
            manifest = build_manifest(self.ctx)
        self.assertEqual(original, unchanged)
        safe = manifest['settings']
        self.assertEqual(safe['llm_base_url'], 'https://api.example.org/v1')
        self.assertEqual(safe['embedding_base_url'], 'https://embed.example.org/v1')
        self.assertEqual(safe['model_connections'][0]['base_url'], 'https://old.example.org/v1')
        text = json.dumps(manifest)
        for secret in ('fake-user', 'fake-password', 'fake-query', 'fake-fragment',
                       'fake-signature', 'old-user', 'old-pass', 'old-token', 'fake-legacy-key'):
            self.assertNotIn(secret, text)

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
