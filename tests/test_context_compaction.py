"""Context compaction preserves user authored turns and keeps provider state private."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import context_compaction, llm, model_protocols, model_registry, prompts, session_controls, session_steering, settings
from src.agent_driver import AdaptiveDriver, COMPRESS_SUMMARY_MAX_CHARS
from src.agent_runtime import AgentRuntime
from src.session_controls import _compacted_messages
from src.state_store import write_json


def _assistant(call_id: str = "call-1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": "file.read", "arguments": "{}"}}],
    }


def _tool(call_id: str = "call-1", content: str = '{"ok":true}') -> dict:
    return {"role": "tool", "tool_call_id": call_id,
            "name": "file.read", "content": content}


class ContextCompactionIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = {
            "connection_id": "fixture-connection", "profile_id": "fixture-profile",
            "model": "fixture-model", "protocol": "responses",
            "base_url": "https://fixture.invalid/v1", "thinking_style": "auto",
            "reasoning_effort": "auto", "thinking_budget": 4096,
            "context_window": 32768, "max_output_tokens": 8192, "api_key": "fixture",
        }
        patches = (
            mock.patch.object(settings, "_PATH", self.root / "settings.json"),
            mock.patch.object(llm, "available", return_value=False),
            mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(self.profile)),
            mock.patch.object(prompts, "agent_system", return_value="Stable system."),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.runtime = AgentRuntime(offline=True, llm_enabled=False, session_root=self.root)
        self.driver = AdaptiveDriver(self.runtime)

    def _state(self, messages, sid="compact-integrity"):
        return {
            "schema_version": 1, "session_id": sid, "agent": "research", "status": "running",
            "messages": messages, "original_request": messages[1]["content"],
            "model_identity": self.driver._identity(), "steps": 1, "tokens": 0,
            "model_calls": 1, "summary_calls": 0, "usage_complete": True,
            "max_steps": 16, "token_budget": 100000, "queue": [], "pending": None,
            "active_call": None, "active_calls": [], "used_call_ids": [], "unresolved": {},
            "content": "", "repair_rounds": 0, "compressions": 0,
        }

    def test_automatic_compression_keeps_every_user_turn_verbatim(self):
        constraint = "用户补充约束：" + "x" * 240 + "必须保留这句末尾"
        initial = {"role": "user", "content": "初始任务"}
        steering = {"role": "user", "content": constraint}
        messages = [
            {"role": "system", "content": "Stable system."}, initial, steering,
            _assistant("old"), _tool("old", '{"ok":true,"payload":"' + "z" * 1800 + '"}'),
            _assistant("new"), _tool("new"),
        ]
        state = self._state(copy.deepcopy(messages))
        self.assertTrue(self.driver._compress_context(state, self.profile))
        active_users = [
            message for message in state["messages"]
            if message.get("role") == "user" and "_context_compaction" not in message
        ]
        expected_users = [message for message in messages if message.get("role") == "user"]
        self.assertEqual(active_users, expected_users)
        self.assertIn(constraint, json.dumps(state["messages"], ensure_ascii=False))
        summary = next(message for message in state["messages"] if "_context_compaction" in message)
        self.assertEqual(summary["_context_compaction"]["version"], 1)
        self.assertEqual(state["context_archive"][-1], messages[2:5])

    def test_automatic_compression_with_no_gain_is_a_noop(self):
        huge = {"role": "user", "content": "用户约束" + "很长" * 10000}
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "goal"},
                    huge, {"role": "assistant", "content": ""}, _assistant("new"), _tool("new")]
        state = self._state(copy.deepcopy(messages), sid="compact-no-gain")
        before = copy.deepcopy(state["messages"])
        self.assertFalse(self.driver._compress_context(state, self.profile))
        self.assertEqual(state["messages"], before)
        self.assertNotIn("context_archive", state)
        self.assertFalse(self.runtime.sessions.read("compact-no-gain"))

    def test_no_gain_keeps_model_summary_usage_durable(self):
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "goal"},
                    {"role": "assistant", "content": ""}, _assistant("new"), _tool("new")]
        state = self._state(copy.deepcopy(messages), sid="compact-no-gain-usage")
        with mock.patch.object(llm, "available", return_value=True), mock.patch.object(
                llm, "chat", return_value={"content": "summary" * 300, "usage": {
                    "prompt_tokens": 11, "completion_tokens": 7, "usage_reported": True}}):
            self.assertFalse(self.driver._compress_context(state, self.profile))
        saved = json.loads(self.driver.path(state['session_id']).read_text(encoding='utf-8'))
        self.assertEqual(saved['messages'], messages)
        self.assertEqual(saved['tokens'], 18)
        self.assertEqual(saved['summary_calls'], 1)
        self.assertEqual(saved['model_calls'], 2)
        self.assertNotIn('model_inflight', saved)
        self.assertNotIn('context_archive', saved)

    def test_no_gain_attempt_is_not_rebilled_after_reload_until_messages_change(self):
        messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                    {'role': 'assistant', 'content': ''}, _assistant('tail'), _tool('tail')]
        state = self._state(copy.deepcopy(messages), sid='compact-no-gain-repeat')
        answer = {'content': 'summary' * 250, 'usage': {
            'prompt_tokens': 11, 'completion_tokens': 7, 'usage_reported': True}}
        with mock.patch.object(llm, 'available', return_value=True), mock.patch.object(
                llm, 'chat', return_value=answer) as chat:
            self.assertFalse(self.driver._compress_context(state, self.profile))
            self.assertFalse(self.driver._compress_context(state, self.profile))
            restored = self.driver._load(state['session_id'])
            self.assertFalse(self.driver._compress_context(restored, self.profile))
            self.assertEqual(chat.call_count, 1)
            self.assertEqual(restored['tokens'], 18)
            self.assertNotIn('model_inflight', restored)
            restored['messages'].append({'role': 'user', 'content': 'A new constraint'})
            self.assertFalse(self.driver._compress_context(restored, self.profile))
            self.assertEqual(chat.call_count, 2)
            self.assertEqual(restored['tokens'], 36)

    def test_model_compaction_carries_prior_summary_verbatim_without_trusting_model(self):
        prior = 'VERIFIED_ARTIFACT=' + 'x' * 300 + '/calibration-results.csv\nUNRESOLVED_CHECK=holdout'
        messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                    context_compaction.summary_message(prior), _assistant('old'),
                    _tool('old', json.dumps({'ok': True, 'padding': 'z' * 5000})),
                    _assistant('tail'), _tool('tail')]
        state = self._state(copy.deepcopy(messages), sid='compact-summary-continuity')
        answer = {'content': 'Recent file read succeeded.', 'usage': {
            'prompt_tokens': 11, 'completion_tokens': 7, 'usage_reported': True}}
        with mock.patch.object(llm, 'available', return_value=True), mock.patch.object(
                llm, 'chat', return_value=answer) as chat:
            self.assertTrue(self.driver._compress_context(state, self.profile))
        self.assertIn(prior, chat.call_args.args[0][1]['content'])
        summaries = [m for m in state['messages'] if context_compaction.is_internal_summary(m)]
        self.assertEqual(len(summaries), 1)
        self.assertIn(prior, summaries[0]['content'])
        self.assertIn(answer['content'], summaries[0]['content'])
        self.assertEqual(state['last_compression_summary'], summaries[0]['content'])
        self.assertLessEqual(len(summaries[0]['content']), COMPRESS_SUMMARY_MAX_CHARS)

    def test_offline_and_manual_compaction_keep_all_prior_summaries_including_suffixes(self):
        first = 'FIRST_ARTIFACT=' + 'a' * 500 + '/important-first-tail.csv'
        second = 'SECOND_ARTIFACT=' + 'b' * 500 + '/important-second-tail.csv'
        prefix = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                  context_compaction.summary_message(first), context_compaction.summary_message(second)]
        messages = prefix + [_assistant('old'), _tool('old', json.dumps({'ok': True, 'text': 'z' * 6000})),
                             _assistant('tail'), _tool('tail')]
        state = self._state(copy.deepcopy(messages), sid='compact-offline-continuity')
        self.assertTrue(self.driver._compress_context(state, self.profile))
        manual = _compacted_messages(prefix + [{'role': 'assistant', 'content': 'Observation ' * 25}
                                                for _ in range(10)])
        self.assertIsNotNone(manual)
        for candidate in (state['messages'], manual[0]):
            summaries = [m for m in candidate if context_compaction.is_internal_summary(m)]
            self.assertEqual(len(summaries), 1)
            self.assertIn(first, summaries[0]['content'])
            self.assertIn(second, summaries[0]['content'])
            self.assertLessEqual(len(summaries[0]['content']), COMPRESS_SUMMARY_MAX_CHARS)

    def test_full_summary_budget_defers_without_model_call_or_losing_prior_text(self):
        prior = 'x' * (COMPRESS_SUMMARY_MAX_CHARS - len('KEEP_LAST_ARTIFACT')) + 'KEEP_LAST_ARTIFACT'
        messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                    context_compaction.summary_message(prior), _assistant('old'),
                    _tool('old', json.dumps({'ok': True, 'text': 'z' * 6000})),
                    _assistant('tail'), _tool('tail')]
        state = self._state(copy.deepcopy(messages), sid='compact-summary-capacity')
        with mock.patch.object(llm, 'available', return_value=True), mock.patch.object(
                llm, 'chat', side_effect=AssertionError('no summary space')) as chat:
            self.assertFalse(self.driver._compress_context(state, self.profile))
            self.assertFalse(self.driver._compress_context(state, self.profile))
        self.assertEqual(chat.call_count, 0)
        self.assertEqual(state['messages'], messages)
        self.assertNotIn('context_archive', state)
        saved = json.loads(self.driver.path(state['session_id']).read_text(encoding='utf-8'))
        self.assertEqual(saved['compression_skip']['reason'], 'summary_capacity')
        self.assertEqual(saved['model_calls'], 1)
        self.assertEqual(saved['tokens'], 0)
        manual = messages[:3] + [{'role': 'assistant', 'content': 'observation' * 100}
                                 for _ in range(12)]
        self.assertIsNone(_compacted_messages(manual))

    def test_prior_plus_new_summary_over_budget_does_not_commit_or_rebill(self):
        prior = 'p' * (COMPRESS_SUMMARY_MAX_CHARS - 100)
        messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                    context_compaction.summary_message(prior), _assistant('old'),
                    _tool('old', json.dumps({'ok': True, 'text': 'z' * 6000})),
                    _assistant('tail'), _tool('tail')]
        state = self._state(copy.deepcopy(messages), sid='compact-summary-overflow')
        with mock.patch.object(llm, 'available', return_value=True), mock.patch.object(
                llm, 'chat', return_value={'content': 'n' * 150, 'usage': {
                    'prompt_tokens': 11, 'completion_tokens': 7, 'usage_reported': True}}) as chat:
            self.assertFalse(self.driver._compress_context(state, self.profile))
            self.assertFalse(self.driver._compress_context(state, self.profile))
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(state['messages'], messages)
        self.assertEqual(state['tokens'], 18)
        self.assertNotIn('context_archive', state)

    def test_repeated_compaction_keeps_every_committed_summary_until_total_limit(self):
        state = self._state([{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                             _assistant('seed'), _tool('seed', json.dumps({'ok': True, 'text': 'z' * 6000}))],
                            sid='compact-cumulative-limit')
        committed = []
        with mock.patch.object(llm, 'available', return_value=True), mock.patch.object(llm, 'chat') as chat:
            for index in range(10):
                state['messages'].extend([_assistant(f'tail-{index}'),
                                          _tool(f'tail-{index}', json.dumps({'ok': True, 'text': 'z' * 6000}))])
                new_summary = f'artifact-{index}=' + 'x' * 230 + f'/end-{index}.csv'
                chat.return_value = {'content': new_summary, 'usage': {
                    'prompt_tokens': 1, 'completion_tokens': 1, 'usage_reported': True}}
                before = copy.deepcopy(state['messages'])
                if not self.driver._compress_context(state, self.profile):
                    self.assertEqual(state['messages'], before)
                    self.assertEqual(state['compression_skip']['reason'], 'summary_capacity')
                    calls = chat.call_count
                    self.assertFalse(self.driver._compress_context(state, self.profile))
                    self.assertEqual(chat.call_count, calls)
                    break
                committed.append(new_summary)
                summary = state['messages'][2]['content']
                self.assertEqual(summary, '\n\n'.join(committed))
                self.assertLessEqual(len(summary), COMPRESS_SUMMARY_MAX_CHARS)
            else:
                self.fail('cumulative summary bound was not enforced')
        self.assertGreater(len(committed), 2)
        for text in committed:
            self.assertIn(text, state['messages'][2]['content'])

    def test_interrupted_summary_attempt_is_not_rebilled_after_recovery(self):
        messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                    _assistant('old'), _tool('old', json.dumps({'ok': True, 'text': 'z' * 4000})),
                    _assistant('tail'), _tool('tail')]
        state = self._state(copy.deepcopy(messages), sid='compact-interrupted-summary')
        with mock.patch.object(llm, 'available', return_value=True), mock.patch.object(
                llm, 'chat', side_effect=SystemExit('synthetic process interruption')) as chat:
            with self.assertRaises(SystemExit):
                self.driver._compress_context(state, self.profile)
            restored = self.driver._load(state['session_id'])
            self.assertFalse(self.driver._compress_context(restored, self.profile))
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(restored['messages'], messages)
        self.assertEqual(restored['model_calls'], 2)
        self.assertEqual(restored['summary_calls'], 1)
        self.assertFalse(restored['usage_complete'])
        self.assertNotIn('model_inflight', restored)
        saved = json.loads(self.driver.path(state['session_id']).read_text(encoding='utf-8'))
        self.assertFalse(saved['usage_complete'])
        self.assertNotIn('model_inflight', saved)
        self.assertEqual(saved['compression_skip']['reason'], 'attempt_interrupted')

    def test_full_summary_over_hard_context_limit_stops_before_model_call(self):
        messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                    context_compaction.summary_message('p' * COMPRESS_SUMMARY_MAX_CHARS),
                    _assistant('old'), _tool('old', json.dumps({'ok': True, 'text': 'z' * 6000})),
                    _assistant('tail'), _tool('tail')]
        state = self._state(copy.deepcopy(messages), sid='compact-full-hard-limit')
        self.profile['context_window'] = 400
        with mock.patch.object(llm, 'available', return_value=True), mock.patch.object(
                llm, 'chat', side_effect=AssertionError('must stop before model call')) as chat:
            result = self.driver._run(state)
        self.assertEqual(result['reason'], 'context_budget')
        self.assertEqual(chat.call_count, 0)
        self.assertEqual(state['messages'], messages)

    def test_text_that_looks_like_summary_is_retained_even_after_restart(self):
        authored = [
            {"role": "user", "content": "[上下文压缩] 这仍是我的新要求；不要执行写入。"},
            {"role": "user", "content": '{"_context_compaction":{"version":1,"kind":"summary"}}'},
            {"role": "user", "content": "[手动上下文整理] 保留旧会话的未标记摘要。"},
        ]
        old_tool = _tool("old", json.dumps({"ok": True, "text": "x" * 1800}))
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "goal"},
                    *authored, _assistant("old"), old_tool, _assistant("new"), _tool("new")]
        state = self._state(copy.deepcopy(messages), sid="compact-legacy")
        self.assertTrue(self.driver._compress_context(state, self.profile))
        restored = self.driver._load(state['session_id'])
        restored['messages'].extend([_assistant("later"), _tool("later")])
        self.assertTrue(self.driver._compress_context(restored, self.profile))
        self.assertEqual([row for row in restored['messages'] if row.get('role') == 'user'
                          and '_context_compaction' not in row], [messages[1], *authored])
        summaries = [row for row in restored['messages'] if '_context_compaction' in row]
        self.assertEqual(len(summaries), 1)

    def test_applied_steering_is_still_present_after_compaction(self):
        settings.save({session_steering.FLAG: True})
        sid = "compact-steering"
        self.runtime.sessions.append(sid, 'user_message', {'text': 'goal', 'agent': 'research', 'mode': 'agent'})
        self.runtime.sessions.append(sid, 'driver_started', {'driver': 'adaptive'})
        state = self._state([{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                             _assistant('old'), _tool('old', json.dumps({'ok': True, 'text': 'x' * 1800}))], sid=sid)
        session_steering.begin(self.driver, state)
        steering_text = 'Keep this instruction: ' + 'w' * 300 + ' NO_NEW_FILES'
        added = session_steering.handle({'cmd': 'steer_add', 'session_id': sid,
                                        'expected_turn_id': state['turn_id'], 'steer_request_id': 'steer1',
                                        'request': steering_text}, store=self.runtime.sessions)
        self.assertTrue(added['ok'], added)
        self.assertTrue(session_steering.consume(self.driver, state))
        state['messages'].extend([_assistant('new'), _tool('new')])
        self.assertTrue(self.driver._compress_context(state, self.profile))
        self.assertIn({'role': 'user', 'content': steering_text}, state['messages'])

    def test_preserved_users_above_capacity_stop_before_model_call(self):
        messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'},
                    {'role': 'user', 'content': 'x' * 5000},
                    {'role': 'assistant', 'content': ''}, _assistant('new'), _tool('new')]
        state = self._state(messages, sid='compact-hard-limit')
        self.profile['context_window'] = 400
        with mock.patch.object(llm, 'chat', side_effect=AssertionError('must stop before model call')):
            result = self.driver._run(state)
        self.assertEqual(result['reason'], 'context_budget')
        self.assertIn(messages[2], state['messages'])

    def test_manual_compaction_preserves_user_turns_and_archives_exact_middle(self):
        constraint = {"role": "user", "content": "约束" + "y" * 260}
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "goal"},
                    constraint, {"role": "assistant", "content": "观察" * 80},
                    {"role": "assistant", "content": "中间观察" * 80}, {"role": "user", "content": "中间提问"},
                    {"role": "assistant", "content": "尾部前观察"}, {"role": "user", "content": "尾部前提问"},
                    {"role": "assistant", "content": "尾部观察"}, {"role": "user", "content": "尾部问题"},
                    {"role": "assistant", "content": "", "tool_calls": [{"id": "tail", "type": "function",
                                                                        "function": {"name": "file.read", "arguments": "{}"}}]},
                    _tool("tail"), {"role": "assistant", "content": "尾部"}]
        compacted = _compacted_messages(messages)
        self.assertIsNotNone(compacted)
        candidate, archived = compacted
        self.assertEqual(archived, messages[2:-8])
        users = [message for message in candidate
                 if message.get("role") == "user" and "_context_compaction" not in message]
        self.assertEqual(users, [message for message in messages if message.get("role") == "user"])
        self.assertIn(constraint["content"], json.dumps(candidate, ensure_ascii=False))

    def test_repeated_compression_does_not_treat_old_summary_as_user_constraint(self):
        old_summary = {"role": "user", "content": "old summary",
                       "_context_compaction": {"version": 1, "kind": "summary"}}
        latest = {"role": "user", "content": "最新补充必须保留"}
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "goal"},
                    old_summary, latest, _assistant("old"), _tool("old"),
                    _assistant("new"), _tool("new")]
        state = self._state(copy.deepcopy(messages), sid="compact-repeat")
        self.assertTrue(self.driver._compress_context(state, self.profile))
        self.assertIn(latest, state["messages"])
        self.assertNotIn(old_summary, [m for m in state["messages"] if m.get("role") == "user"
                                       and "_context_compaction" not in m])

    def test_manual_compaction_audit_counts_only_removed_messages(self):
        sid = 'compact-manual-count'
        self.runtime.sessions.append(sid, 'user_message', {'text': 'goal', 'agent': 'research', 'mode': 'agent'})
        self.runtime.sessions.append(sid, 'driver_started', {'driver': 'adaptive'})
        self.runtime.sessions.append(sid, 'completed', {'status': 'done'})
        messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'goal'}]
        for index in range(8):
            messages.extend([{'role': 'user', 'content': f'constraint {index}' + 'v' * 220},
                             {'role': 'assistant', 'content': 'observation ' * 160}])
        state = self._state(messages, sid=sid)
        state['status'] = 'done'
        write_json(self.driver.path(sid), state)
        with mock.patch.object(llm, 'chat', side_effect=AssertionError('manual compaction is offline')):
            result = session_controls.compact_session(sid, store=self.runtime.sessions)
        saved = json.loads(self.driver.path(sid).read_text(encoding='utf-8'))
        audit = self.runtime.sessions.read(sid)[-1]['payload']
        removed = len(messages) - len(saved['messages'])
        self.assertTrue(result['changed'])
        self.assertEqual(result['removed_messages'], removed)
        self.assertEqual(audit['from_messages'], len(messages))
        self.assertEqual(audit['to_messages'], len(saved['messages']))
        self.assertEqual(audit['compressed_count'], removed)
        self.assertEqual(audit['preserved_user_count'], 4)

    def test_compaction_marker_is_not_sent_to_any_provider_wire_payload(self):
        marker = {"version": 1, "kind": "summary", "source_message_count": 3}
        messages = [{"role": "system", "content": "sys"},
                    {"role": "user", "content": "summary", "_context_compaction": marker}]
        for protocol in ("chat_completions", "responses", "anthropic_messages", "gemini", "ollama"):
            profile = {**self.profile, "protocol": protocol, "model": "fixture-model"}
            with self.subTest(protocol=protocol):
                payload = model_protocols.build_request(profile, messages, 0.0, 128, tools=[])
                self.assertNotIn("_context_compaction", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
