import unittest

from src.llm import extract_tool_calls


class AgentLlmAdapterTest(unittest.TestCase):
    def test_extract_tool_calls_from_openai_message(self):
        message = {"tool_calls": [{"id": "tc1", "function": {
            "name": "research.status",
            "arguments": '{"task_id":"t1"}',
        }}]}
        calls = extract_tool_calls(message)
        self.assertEqual(calls[0].name, "research.status")
        self.assertEqual(calls[0].arguments, {"task_id": "t1"})

    def test_malformed_tool_arguments_are_reported(self):
        message = {"tool_calls": [{"id": "tc2", "function": {
            "name": "research.status", "arguments": "not-json",
        }}]}
        calls = extract_tool_calls(message)
        self.assertEqual(calls[0].error, "invalid_arguments")
