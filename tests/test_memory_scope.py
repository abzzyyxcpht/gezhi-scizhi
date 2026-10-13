import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import settings, workspace_features as features


class MemoryScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(features, "DATA_DIR", self.root),
            patch.object(settings, "_PATH", self.root / "settings.json"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_legacy_records_default_to_global_scope(self):
        path = self.root / "research_memory.json"
        path.write_text(json.dumps({"version": 1, "items": [{
            "id": "legacy", "title": "旧记录", "content": "通用偏好",
            "approved": True, "enabled": True,
        }]}, ensure_ascii=False), encoding="utf-8")

        row = features.handle({"cmd": "memory_list"})["memories"][0]

        self.assertEqual("global", row["scope"])
        self.assertIn("通用偏好", features.memory_context(1000, project_id="p1"))

    def test_memory_list_and_context_filter_project_and_role_scopes(self):
        features.handle({"cmd": "memory_save", "memory": {
            "id": "global", "title": "全局", "content": "G", "approved": True,
        }})
        features.handle({"cmd": "memory_save", "memory": {
            "id": "project", "title": "项目", "content": "P", "approved": True,
            "scope": "project", "project_id": "proj-a",
        }})
        features.handle({"cmd": "memory_save", "memory": {
            "id": "role", "title": "角色", "content": "R", "approved": True,
            "scope": "role", "role": "critic",
        }})
        features.handle({"cmd": "memory_save", "memory": {
            "id": "role-project", "title": "项目角色", "content": "RP", "approved": True,
            "scope": "role", "role": "critic", "project_id": "proj-a",
        }})

        rows = features.handle({"cmd": "memory_list", "project_id": "proj-a", "role": "critic"})["memories"]
        self.assertEqual({"global", "project", "role", "role-project"}, {row["id"] for row in rows})
        rows = features.handle({"cmd": "memory_list", "project_id": "proj-b", "role": "critic"})["memories"]
        self.assertEqual({"global", "role"}, {row["id"] for row in rows})
        rows = features.handle({"cmd": "memory_list", "project_id": "proj-a", "role": "writer"})["memories"]
        self.assertEqual({"global", "project"}, {row["id"] for row in rows})

        context = features.memory_context(1000, project_id="proj-a", role="critic")
        self.assertIn("G", context)
        self.assertIn("P", context)
        self.assertIn("R", context)
        context = features.memory_context(1000, project_id="proj-b", role="writer")
        self.assertIn("G", context)
        self.assertNotIn("P", context)
        self.assertNotIn("R", context)

    def test_scope_or_project_metadata_edit_revokes_approval(self):
        saved = features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "title": "偏好", "content": "内容", "approved": True,
            "scope": "project", "project_id": "proj-a",
        }})["memory"]
        self.assertTrue(saved["approved"])

        changed = features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "scope": "project", "project_id": "proj-b",
        }})["memory"]
        self.assertFalse(changed["approved"])

    def test_switching_project_scope_to_global_clears_project_metadata(self):
        features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "title": "偏好", "content": "内容", "approved": True,
            "scope": "project", "project_id": "proj-a",
        }})

        changed = features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "scope": "global", "approved": True,
        }})["memory"]

        self.assertEqual("global", changed["scope"])
        self.assertNotIn("project_id", changed)
        self.assertNotIn("role", changed)
        persisted = features.handle({"cmd": "memory_list"})["memories"][0]
        self.assertNotIn("project_id", persisted)

    def test_switching_role_project_scope_to_all_projects_clears_binding(self):
        features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "title": "偏好", "content": "内容", "approved": True,
            "scope": "role", "role": "critic", "project_id": "proj-a",
        }})

        changed = features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "scope": "role", "role": "critic", "project_id": None,
            "approved": True,
        }})["memory"]

        self.assertEqual("role", changed["scope"])
        self.assertEqual("critic", changed["role"])
        self.assertNotIn("project_id", changed)
        persisted = features.handle({"cmd": "memory_list"})["memories"][0]
        self.assertNotIn("project_id", persisted)

        features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "approved": True,
        }})
        changed = features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "content": "更新内容",
        }})["memory"]
        self.assertFalse(changed["approved"])

    def test_memory_search_applies_scope_filters(self):
        from src.agent_runtime import AgentRuntime

        features.handle({"cmd": "memory_save", "memory": {
            "id": "global", "title": "全局", "content": "相同关键词", "approved": True,
        }})
        features.handle({"cmd": "memory_save", "memory": {
            "id": "project", "title": "项目", "content": "相同关键词", "approved": True,
            "scope": "project", "project_id": "proj-a",
        }})

        result = AgentRuntime._memory_search({"query": "关键词"})

        self.assertEqual({"global"}, {row["id"] for row in result["memories"]})

    def test_memory_search_rejects_caller_scope_selectors(self):
        from src.agent_runtime import AgentRuntime

        result = AgentRuntime._memory_search({
            "query": "关键词", "project_id": "proj-b", "role": "critic",
        })

        self.assertFalse(result["ok"])
        self.assertIn("作用域", result["error"])

    def test_bound_memory_search_uses_journal_scope_and_child_project(self):
        from unittest.mock import patch
        from src.agent_runtime import AgentRuntime

        for ident, scope, project_id in (
            ("global", "global", None), ("a", "project", "proj-a"),
            ("b", "project", "proj-b"),
        ):
            features.handle({"cmd": "memory_save", "memory": {
                "id": ident, "title": ident, "content": "关键词", "approved": True,
                "scope": scope, "project_id": project_id,
            }})

        class Store:
            def snapshot(self, sid):
                return {"agent": "critic", "project_id": "proj-a"}

        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.sessions = Store()
        with patch("src.context_manifest.session_scope",
                   return_value={"project_id": "proj-a", "project_root": self.root}):
            result = runtime._memory_search({
                "query": "关键词", "__session_id": "child-1",
            })

        self.assertEqual({"global", "a"}, {row["id"] for row in result["memories"]})

    def test_memory_context_manifest_reports_selected_and_deferred_records(self):
        features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "title": "短", "content": "可见", "approved": True,
        }})
        features.handle({"cmd": "memory_save", "memory": {
            "id": "m2", "title": "长", "content": "x" * 100, "approved": True,
        }})

        text, manifest = features.memory_context(80, with_manifest=True)

        self.assertIn("可见", text)
        self.assertEqual(["m1"], [row["id"] for row in manifest["selected"]])
        self.assertEqual(["m2"], [row["id"] for row in manifest["deferred"]])
        self.assertEqual(80, manifest["budget"]["limit_chars"])
        for group in ("selected", "deferred"):
            self.assertTrue({"used_chars", "total_chars", "source", "sha256", "updated_at"}
                            <= set(manifest[group][0]))

    def test_memory_context_manifest_records_scope_visible_exclusions(self):
        features.handle({"cmd": "memory_save", "memory": {
            "id": "pending", "title": "待确认", "content": "P", "approved": False,
        }})
        features.handle({"cmd": "memory_save", "memory": {
            "id": "off", "title": "已停用", "content": "O", "approved": True,
            "enabled": False,
        }})

        _, manifest = features.memory_context(1000, with_manifest=True)

        self.assertEqual(["pending"], [row["id"] for row in manifest["unapproved"]])
        self.assertEqual(["off"], [row["id"] for row in manifest["disabled"]])

    def test_zero_budget_reports_matching_approved_memories_as_deferred(self):
        features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "title": "预算", "content": "内容", "approved": True,
        }})

        text, manifest = features.memory_context(0, with_manifest=True)

        self.assertEqual("", text)
        self.assertEqual(["m1"], [row["id"] for row in manifest["deferred"]])

    def test_disabled_memory_setting_reports_approved_records_as_disabled(self):
        features.handle({"cmd": "memory_save", "memory": {
            "id": "m1", "title": "停用", "content": "内容", "approved": True,
        }})
        settings.save({"memory_enabled": False})

        text, manifest = features.memory_context(1000, with_manifest=True)

        self.assertEqual("", text)
        self.assertEqual(["m1"], [row["id"] for row in manifest["disabled"]])
        self.assertEqual("memory_disabled", manifest["disabled"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
