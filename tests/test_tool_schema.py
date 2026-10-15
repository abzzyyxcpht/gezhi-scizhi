# -*- coding: utf-8 -*-
"""工具参数 Schema 校验测试：仅接受工具声明的参数。"""
from __future__ import annotations

import unittest

from src.agent_runtime import (AgentRuntime, ToolCall, validate_arguments)

SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "limit": {"type": "integer"},
        "mode": {"enum": ["fast", "strong"]},
    },
    "required": ["topic"],
    "additionalProperties": False,
}


class ValidateArgumentsTest(unittest.TestCase):
    def test_pass_with_valid_args(self):
        self.assertEqual(validate_arguments(
            SCHEMA, {"topic": "大模型", "limit": 5, "mode": "fast"}), [])

    def test_missing_required(self):
        problems = validate_arguments(SCHEMA, {"limit": 1})
        self.assertTrue(any("topic" in p for p in problems))

    def test_wrong_type_rejected(self):
        problems = validate_arguments(SCHEMA, {"topic": 123})
        self.assertTrue(any("topic" in p for p in problems))

    def test_bool_is_not_number(self):
        problems = validate_arguments(SCHEMA, {"topic": "t", "limit": True})
        self.assertTrue(any("limit" in p for p in problems))

    def test_enum_violation(self):
        problems = validate_arguments(SCHEMA, {"topic": "t", "mode": "xl"})
        self.assertTrue(any("mode" in p for p in problems))

    def test_undeclared_key_rejected(self):
        problems = validate_arguments(SCHEMA, {"topic": "t", "evil": 1})
        self.assertTrue(any("evil" in p for p in problems))

    def test_runtime_private_keys_ignored(self):
        self.assertEqual(validate_arguments(
            SCHEMA, {"topic": "t", "__session_id": "abc"}), [])

    def test_empty_schema_means_no_constraint(self):
        self.assertEqual(validate_arguments({}, {"anything": [1, 2]}), [])


class ExecuteCallSchemaGateTest(unittest.TestCase):
    def test_invalid_args_never_touch_handler(self):
        rt = AgentRuntime(offline=True, llm_enabled=False)
        call = ToolCall("t1", "research.create",
                        {"topic": 123, "bonus": 1}, "write")
        result = rt._execute_call("sess", call)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_arguments")
        self.assertTrue(any("topic" in p for p in result["problems"]))
        # 会话留痕：tool_result 里同样是结构化错误
        events = rt.sessions.read("sess")
        last = events[-1]
        self.assertEqual(last["type"], "tool_result")
        self.assertEqual(last["payload"]["result"]["error"], "invalid_arguments")

    def test_valid_args_reach_handler(self):
        rt = AgentRuntime(offline=True, llm_enabled=False)
        call = ToolCall("t2", "doctor.run", {}, "read")
        result = rt._execute_call("sess2", call)
        self.assertTrue(result.get("ok", True) or "items" in result)


class ToolsSchemaProtocolTest(unittest.TestCase):
    def test_openai_function_calling_shape(self):
        rt = AgentRuntime(offline=True, llm_enabled=False)
        schema = rt.tools_schema()
        self.assertGreaterEqual(len(schema), 10)
        fn = next(t for t in schema
                  if t["function"]["name"] == "research.create")
        self.assertEqual(fn["type"], "function")
        self.assertEqual(fn["function"]["parameters"]["required"], ["topic"])
        self.assertEqual(fn["function"]["parameters"]["type"], "object")


if __name__ == "__main__":
    unittest.main()
