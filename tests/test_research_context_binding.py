import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src import service
from src.orchestrator import engine


class ResearchContextBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [
            patch.object(service, "CHECKPOINT_DIR", self.root),
            patch.object(engine, "CHECKPOINT_DIR", self.root),
            patch.object(service, "_AGENTS_READY", True),
            patch.object(service, "load_workflow", return_value={"nodes": []}),
            patch.object(service, "run"),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        service._tasks.clear()
        self.tmp.cleanup()

    def test_create_task_persists_session_binding_and_restore_keeps_it(self):
        binding = {
            "project_id": "project-a", "workspace_mode": "research",
            "source_session_id": "session-a", "session_root": str(self.root),
        }

        task_id = service.create_task("scope test", research_context=binding)

        ctx = service.get(task_id)
        self.assertEqual(binding, ctx.params["research_context"])
        saved = json.loads((self.root / f"{task_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(binding, saved["params"]["research_context"])
        service._tasks.pop(task_id)
        restored = service.TaskContext.load(task_id)
        self.assertEqual(binding, restored.params["research_context"])

    def test_research_create_uses_journal_binding_and_ignores_model_scope_args(self):
        from src.agent_runtime import AgentRuntime

        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.offline = False
        runtime._session_topics = {}
        runtime._workspaces = {}
        runtime.sessions = object()
        binding = {"project_id": "project-a", "workspace_mode": "research",
                   "source_session_id": "session-a", "session_root": str(self.root)}
        helper = types.ModuleType("src.research_context")
        helper.binding_for_session = lambda store, sid: binding
        with patch.dict(sys.modules, {"src.research_context": helper}), \
             patch.object(service, "create_task", return_value="task-a") as create:
            result = runtime._research_create({"__session_id": "session-a",
                                               "topic": "topic", "project_id": "project-b"})

        self.assertTrue(result["ok"])
        create.assert_called_once_with("topic", research_context=binding)

    def test_loaded_checkpoint_keeps_project_binding_for_extraction_prompt_and_receipt(self):
        from src.agents import extraction
        from src.orchestrator.engine import TaskContext

        binding = {"project_id": "project-a", "workspace_mode": "research",
                   "source_session_id": "session-a", "session_root": str(self.root)}
        original = TaskContext("task-a", "research_pipeline", {
            "topic": "topic", "offline": False, "research_context": binding,
        })
        original.artifacts["lit_filter"] = {"pool": [{
            "id": "w1", "title": "Paper", "abstract": "We propose a method. Results show gains.",
        }]}
        original.save()
        restored = TaskContext.load("task-a")
        seen = {}

        def fake_chat(messages, **kwargs):
            seen.update(kwargs=kwargs, messages=messages)
            return {"method": "We propose a method.", "innovation": "", "conclusion": "Results show gains.",
                    "limitation": ""}

        def fake_sources(*, max_chars, project_root=None):
            seen["project_root"] = project_root
            return [{"kind": "instructions", "name": "GEZHI.md", "scope": "project",
                     "status": "included", "body": "PROJECT_A_CANARY", "used_chars": 18,
                     "total_chars": 18, "truncated": False}]

        with patch.object(extraction.llm, "available", return_value=True), \
             patch.object(extraction.llm, "chat_json", side_effect=fake_chat), \
             patch("src.project_workspace.resolve_path", return_value=self.root), \
             patch("src.context_file.load_sources", side_effect=fake_sources):
            result = extraction.extract_pool(restored, {})

        self.assertEqual("llm", result["records"][0]["extraction_method"])
        self.assertEqual(self.root, seen["project_root"])
        self.assertIn("PROJECT_A_CANARY", json.dumps(seen["messages"], ensure_ascii=False))
        self.assertNotIn("project-b", json.dumps(seen["messages"], ensure_ascii=False))
        receipt = json.loads((self.root / "session-a.research-contexts.json").read_text(encoding="utf-8"))
        self.assertEqual("project-a", receipt["entries"][0]["context_manifest"]["project_id"])


if __name__ == "__main__":
    unittest.main()
