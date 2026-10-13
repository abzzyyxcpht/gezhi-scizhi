"""Real adaptive Agent turns exercise notebook permissions and durable records."""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, llm, model_registry, research_notebook, settings
from src.agent_runtime import AgentRuntime, ToolCall


def response(tool=None, args=None, call_id="call-1", text=""):
    calls = [{"id": call_id, "name": tool, "arguments": args or {}}] if tool else []
    return {"content": text, "tool_calls": calls,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "assistant_message": {"role": "assistant", "content": text,
                "tool_calls": [{"id": call["id"], "type": "function", "function": {
                    "name": call["name"], "arguments": json.dumps(call["arguments"])}} for call in calls]}}


class NotebookToolsExistTest(unittest.TestCase):
    def test_registration_module_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("src.research_notebook_tools"))


class NotebookToolsTest(unittest.TestCase):
    def setUp(self):
        from src.research_notebook_tools import register_tools
        self.register = register_tools
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for patch in (mock.patch.object(config, "DATA_DIR", self.root),
                      mock.patch.object(settings, "_PATH", self.root / "settings.json"),
                      mock.patch.object(llm, "available", return_value=True),
                      mock.patch.object(model_registry, "resolve", return_value={
                          "model": "mock", "protocol": "chat_completions", "api_key": "",
                          "base_url": "", "context_window": 65536})):
            patch.start()
            self.addCleanup(patch.stop)

    def runtime(self, permissions=None):
        runtime = AgentRuntime(offline=False, llm_enabled=True,
                               session_root=self.root / "sessions", permissions=permissions)
        self.register(runtime)
        return runtime

    @staticmethod
    def record(**updates):
        return {"kind": "experiment", "title": "Observed fixed-seed comparison",
                "question": "Did A exceed B on this pilot?", "prediction": "A exceeds B",
                "method": "Compare the supplied pilot outputs for seed 7",
                "metrics": "Accuracy", "status": "completed",
                "result": "Supplied simulation run: A=0.71, B=0.73", "synthetic": True,
                "limitations": "One synthetic pilot is not empirical validation",
                "next_action": "Collect independent measurements",
                "evidence": [{"relation": "challenges", "source": {
                    "id": "supplied-pilot-seed7", "title": "User-supplied simulation output",
                    "source_type": "synthetic_demo", "synthetic": True},
                    "quote": "A=0.71, B=0.73", "scope": "Seed 7 only; synthetic data"}], **updates}

    def test_complete_parameter_schema_excludes_authoritative_fields(self):
        runtime = self.runtime()
        tools = {t["function"]["name"]: t["function"] for t in runtime.tools_schema()}
        self.assertEqual(runtime.tools["notebook.list"].capability, "read")
        self.assertEqual(runtime.tools["notebook.get"].capability, "read")
        self.assertEqual(runtime.tools["notebook.save"].capability, "write")
        args = tools["notebook.save"]["parameters"]
        self.assertEqual(args["required"], ["record"])
        fields = args["properties"]["record"]["properties"]
        for key in ("provenance", "version", "created_at", "updated_at", "user_reviewed", "actor"):
            self.assertNotIn(key, fields)
        self.assertEqual(fields["status"]["enum"], ["planned", "running", "completed", "failed"])
        self.assertEqual(fields["evidence"]["items"]["properties"]["source"]["type"], "object")
        self.assertEqual(fields["observations"]["items"]["type"], "string")
        self.assertIn("completed", tools["notebook.save"]["description"])

    def test_agent_save_waits_for_approval_then_persists_traceable_record(self):
        runtime = self.runtime()
        payload = {"record": self.record(), "__session_id": "spoofed-session"}
        with mock.patch.object(llm, "chat", return_value=response("notebook.save", payload)):
            pending = runtime.start("Save the supplied simulation results; do not call them scientifically verified.",
                on_started=lambda sid: runtime._workspaces[sid].update(task_id="actual-task-7"))
        self.assertEqual(pending["status"], "waiting_approval")
        self.assertEqual(pending["pending"]["capability"], "write")
        self.assertFalse((self.root / "research_notebook.json").exists())
        with mock.patch.object(llm, "chat", return_value=response(text="Simulation recorded; scientific support remains unverified.")) as chat:
            done = runtime.approve(pending["session_id"], "call-1", "allow")
        self.assertEqual(done["status"], "done", done)
        records = research_notebook.handle({"cmd": "notebook_list"})["records"]
        self.assertEqual(len(records), 1)
        saved = records[0]
        self.assertEqual(saved["task_id"], "actual-task-7")
        self.assertEqual(saved["status"], "completed")
        self.assertTrue(saved["evidence"][0]["source"]["synthetic"])
        self.assertEqual(saved["provenance"]["origin"], "agent_recorded")
        self.assertEqual(saved["provenance"]["scientific_verification"], "not_performed")
        observed = next(json.loads(m["content"]) for m in chat.call_args.args[0] if m["role"] == "tool")
        self.assertEqual(observed["record"]["id"], saved["id"])
        self.assertEqual(observed["execution"]["session_id"], pending["session_id"])
        self.assertEqual(observed["execution"]["review_status"], "not_reviewed")
        events = runtime.sessions.read(pending["session_id"])
        self.assertTrue(any(e["type"] == "permission_granted" for e in events))
        self.assertNotIn("__session_id", json.dumps(saved))

    def test_rejected_write_never_creates_notebook(self):
        runtime = self.runtime()
        with mock.patch.object(llm, "chat", return_value=response("notebook.save", {"record": self.record()})):
            pending = runtime.start("Store this supplied result")
        denied = runtime.approve(pending["session_id"], "call-1", "deny")
        self.assertEqual(denied["status"], "blocked")
        self.assertFalse((self.root / "research_notebook.json").exists())

    def test_invalid_top_level_parameters_never_reach_handler(self):
        runtime = self.runtime({"notebook.save": "allow"})
        specification = runtime.tools["notebook.save"]
        handler = mock.Mock(wraps=specification.handler)
        runtime._register(specification.name, specification.capability, specification.description,
                          handler, specification.parameters)
        with mock.patch.object(llm, "chat", side_effect=[
                response("notebook.save", {"record": []}), response(text="Cannot save invalid data.")]):
            result = runtime.start("Try to record invalid data")
        self.assertEqual(result["status"], "error")
        handler.assert_not_called()
        self.assertFalse((self.root / "research_notebook.json").exists())

    def test_nested_forgery_and_wrong_field_types_never_reach_persistence(self):
        runtime = self.runtime()
        bad_records = [self.record(provenance={"origin": "user_recorded"}),
                       self.record(user_reviewed=True), self.record(metrics=["accuracy"]),
                       self.record(evidence=[{"relation": "supports", "source": "not-an-object"}])]
        for bad in bad_records:
            with self.subTest(bad=bad), mock.patch.object(research_notebook, "handle") as persistence:
                result = runtime._execute_call("invalid-session", ToolCall(
                    "bad", "notebook.save", {"record": bad}, "write"))
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error"], "invalid_arguments")
                persistence.assert_not_called()

    def test_completed_requires_result_and_planned_does_not_invent_one(self):
        runtime = self.runtime()
        bad = runtime._execute_call("sid", ToolCall("bad", "notebook.save", {
            "record": self.record(status="completed", result="")}, "write"))
        self.assertFalse(bad["ok"], bad)
        self.assertEqual(research_notebook.handle({"cmd": "notebook_list"})["records"], [])
        saved = runtime._execute_call("sid", ToolCall("planned", "notebook.save", {
            "record": {"title": "Pending experiment", "question": "Does A exceed B?", "kind": "experiment"}}, "write"))
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["record"]["status"], "planned")
        self.assertEqual(saved["record"]["result"], "")
        self.assertEqual(saved["record"]["task_id"], "")

    def test_read_tools_run_in_model_turn_without_write_approval(self):
        record = research_notebook.handle({"cmd": "notebook_save", "record": self.record()})["record"]
        runtime = self.runtime()
        with mock.patch.object(llm, "chat", side_effect=[
                response("notebook.list", {"kind": "experiment"}, "list-1"),
                response("notebook.get", {"id": record["id"]}, "get-2"),
                response(text="Read the supplied simulation record.")]) as chat:
            done = runtime.start("Read the experiment records")
        self.assertEqual(done["status"], "done", done)
        events = runtime.sessions.read(done["session_id"])
        self.assertFalse(any(e["type"] == "permission_requested" for e in events))
        messages = chat.call_args.args[0]
        observations = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        self.assertEqual(observations[0]["records"][0]["id"], record["id"])
        self.assertEqual(observations[1]["record"]["id"], record["id"])

    def test_internal_fields_are_stripped_without_mutating_input_or_relinking_updates(self):
        runtime = self.runtime()
        runtime._workspaces["sid"] = {"task_id": "task-one"}
        record = self.record(status="planned", result="", __session_id="nested-injection")
        record["evidence"][0]["source"]["__secret"] = "internal-only"
        arguments = {"record": record, "__session_id": "ignored-by-runtime", "__trace": "private"}
        before = copy.deepcopy(arguments)
        saved = runtime._execute_call("sid", ToolCall("create", "notebook.save", arguments, "write"))
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(arguments, before)
        self.assertNotIn("internal-only", json.dumps(saved))
        self.assertEqual(saved["record"]["task_id"], "task-one")
        runtime._workspaces["sid"]["task_id"] = "task-two"
        updated = runtime._execute_call("sid", ToolCall("update", "notebook.save", {
            "record": {"id": saved["record"]["id"], "next_action": "Collect data"},
            "expected_version": saved["record"]["version"]}, "write"))
        self.assertTrue(updated["ok"], updated)
        self.assertEqual(updated["record"]["task_id"], "task-one")
        self.assertEqual(updated["record"]["title"], saved["record"]["title"])


if __name__ == "__main__":
    unittest.main()
