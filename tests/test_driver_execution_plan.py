from __future__ import annotations

import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import llm, model_protocols, model_registry, prompts, research_permissions, settings
from src.agent_driver import AdaptiveDriver
from src.agent_runtime import AgentRuntime
from tests.test_driver_resilience import driver_state, response


class DriverExecutionPlanTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = {"connection_id": "connection", "profile_id": "profile", "model": "model",
                        "protocol": "responses", "base_url": "https://fixture.invalid/v1",
                        "thinking_style": "auto", "reasoning_effort": "auto", "thinking_budget": 4096,
                        "context_window": 32768, "max_output_tokens": 8192, "api_key": "test-key"}
        for patch in (mock.patch.object(settings, "_PATH", self.root / "settings.json"),
                      mock.patch.object(llm, "available", return_value=True),
                      mock.patch.object(model_registry, "resolve", side_effect=lambda *_: dict(self.profile)),
                      mock.patch.object(prompts, "agent_system", return_value="Fixed system.")):
            patch.start()
            self.addCleanup(patch.stop)
        self.sid = ""

    def runtime(self, permissions=None):
        return AgentRuntime(offline=False, llm_enabled=True, session_root=self.root, permissions=permissions)

    def api(self):
        try:
            return importlib.import_module('src.execution_plan')
        except ModuleNotFoundError:
            self.fail('Execution-plan API is not implemented')

    def args(self, runtime, *, completed=False):
        plan = self.api().read_plan(runtime.sessions, self.sid)
        return {'expected_turn_id': plan['turn_id'], 'expected_revision': plan['revision'], 'steps': [{
            'id': 'read', 'title': '读取当前工作区', 'criterion': '获得当前工作区状态',
            'kind': 'execution', 'status': 'completed' if completed else 'in_progress',
            'evidence': ['receipt'] if completed else [], 'reason': '',
        }]}

    def call(self, runtime, identifier, *, completed=False):
        return response(calls=[(identifier, 'plan.update', self.args(runtime, completed=completed))])

    def start(self, runtime):
        return runtime.start('读取工作区并核对结果', on_started=lambda sid: setattr(self, 'sid', sid))

    def test_plan_tools_are_registered_for_only_primary_roles(self):
        runtime = self.runtime()
        self.assertIn('plan.update', runtime.tools)
        self.assertEqual(runtime.tools['plan.update'].capability, 'write')
        self.assertTrue(runtime.tools['plan.update'].retry_safe)
        for role in ('general', 'research', 'pipeline', 'explore', 'critic', 'stats'):
            runtime._session_agent[role] = role
            names = {row['function']['name'] for row in runtime.tools_schema(role)}
            self.assertEqual('plan.read' in names, role in {'general', 'research', 'pipeline'})
            self.assertEqual('plan.update' in names, role in {'general', 'research', 'pipeline'})

    def test_metadata_update_is_allowed_in_readonly_tier_but_explicit_deny_wins(self):
        settings.save({'agent_permission_tier': 'read-only'})
        runtime = self.runtime()
        self.assertEqual(runtime._agent_gate('session', 'plan.update', 'write'), 'allow')
        action = research_permissions.classify('plan.update', 'write')
        self.assertEqual(action['tier'], 'read-only')
        self.assertIn('会话', action['impact'])
        denied = self.runtime({'plan.update': 'deny'})
        self.assertEqual(denied._agent_gate('session', 'plan.update', 'write'), 'deny')
        runtime._session_agent['review-session'] = 'general'
        runtime.sessions.append('review-session', 'user_message', {'text': 'Review', 'mode': 'review'})
        self.assertEqual(runtime._agent_gate('review-session', 'plan.update', 'write'), 'deny')

    def test_completed_plan_uses_real_tool_receipt_and_reaches_done(self):
        runtime = self.runtime()
        answers = iter([lambda: self.call(runtime, 'plan-create'),
                        lambda: response(calls=[('receipt', 'workspace.read', {})]),
                        lambda: self.call(runtime, 'plan-close', completed=True),
                        lambda: response('已读取当前工作区。')])
        seen = []
        def answer(messages, **_):
            seen.append(copy.deepcopy(messages))
            return next(answers)()
        with mock.patch.object(llm, 'chat', side_effect=answer):
            result = self.start(runtime)
        self.assertEqual(result['status'], 'done', result)
        plan = self.api().read_plan(runtime.sessions, self.sid)
        self.assertTrue(plan['ready'])
        self.assertEqual(plan['steps'][0]['evidence_details'][0]['tool'], 'workspace.read')
        self.assertEqual(len(seen), 4)
        for messages in seen:
            self.assertLessEqual(sum('_execution_plan' in m for m in messages), 1)
            self.assertEqual(messages[0], seen[0][0])
            calls = set()
            for message in messages:
                calls.update(call['id'] for call in message.get('tool_calls', []))
                if message['role'] == 'tool':
                    self.assertIn(message['tool_call_id'], calls)
        self.assertTrue(any('_execution_plan' in m for m in seen[-1]))

    def test_unfinished_plan_gets_one_feedback_then_blocks(self):
        runtime = self.runtime()
        answers = iter([lambda: self.call(runtime, 'plan-create'),
                        lambda: response('我已完成全部事项。'), lambda: response('仍声称已完成。')])
        with mock.patch.object(llm, 'chat', side_effect=lambda *_a, **_kw: next(answers)()) as model:
            result = self.start(runtime)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['reason'], 'plan_incomplete')
        self.assertTrue(result['message'])
        self.assertEqual(model.call_count, 3)
        events = runtime.sessions.read(self.sid)
        self.assertEqual(sum(e['type'] == 'plan_completion_required' for e in events), 1)
        self.assertNotEqual(events[-1]['payload']['status'], 'done')

    def test_completion_feedback_can_be_resolved_with_receipts(self):
        runtime = self.runtime()
        answers = iter([lambda: self.call(runtime, 'plan-create'), lambda: response('先准备结束'),
                        lambda: response(calls=[('receipt', 'workspace.read', {})]),
                        lambda: self.call(runtime, 'plan-close', completed=True), lambda: response('实际核对完成')])
        with mock.patch.object(llm, 'chat', side_effect=lambda *_a, **_kw: next(answers)()):
            result = self.start(runtime)
        self.assertEqual(result['status'], 'done', result)
        saved = json.loads(AdaptiveDriver(runtime).path(self.sid).read_text(encoding='utf-8'))
        self.assertEqual(saved['plan_completion_checks'], 1)
        self.assertEqual(saved['model_calls'], 5)

    def test_feedback_never_resets_step_budget(self):
        runtime = self.runtime()
        settings.save({'agent_max_steps': 2})
        answers = iter([lambda: self.call(runtime, 'plan-create'), lambda: response('先准备结束')])
        with mock.patch.object(llm, 'chat', side_effect=lambda *_a, **_kw: next(answers)()) as model:
            result = self.start(runtime)
        self.assertEqual(result['reason'], 'step_budget')
        self.assertEqual(model.call_count, 2)

    def test_cancel_wins_when_incomplete_final_answer_returns(self):
        runtime = self.runtime()
        def cancel():
            runtime.cancel_session(self.sid)
            return response('迟到完成回答')
        answers = iter([lambda: self.call(runtime, 'plan-create'), cancel])
        with mock.patch.object(llm, 'chat', side_effect=lambda *_a, **_kw: next(answers)()):
            result = self.start(runtime)
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(any(e['type'] == 'plan_completion_required' for e in runtime.sessions.read(self.sid)))

    def test_no_plan_keeps_single_call_question_answer_behavior(self):
        runtime = self.runtime()
        with mock.patch.object(llm, 'chat', return_value=response('简单回答')) as model:
            result = self.start(runtime)
        self.assertEqual(result['status'], 'done')
        self.assertEqual(model.call_count, 1)
        self.assertFalse(any(e['type'] == 'plan_completion_required' for e in runtime.sessions.read(self.sid)))

    def test_old_frozen_tools_do_not_gain_execution_plan_permissions(self):
        runtime = self.runtime()
        sid = self.sid = 'old-frozen'
        runtime.sessions.append(sid, 'user_message', {'text': '旧会话', 'mode': 'agent'})
        driver = AdaptiveDriver(runtime)
        state = driver_state(runtime, sid, [{'role': 'system', 'content': 'Frozen'},
                                            {'role': 'user', 'content': '旧会话'}],
                             tool_schemas=[{'type': 'function', 'function': {
                                 'name': 'workspace.read', 'parameters': runtime._obj_schema({})}}])
        driver._save(state)
        answers = iter([lambda: self.call(runtime, 'not-exposed'), lambda: response('已使用原会话工具范围')])
        with mock.patch.object(llm, 'chat', side_effect=lambda *_a, **_kw: next(answers)()):
            result = driver.resume(sid)
        self.assertEqual(result['status'], 'done')
        saved = json.loads(driver.path(sid).read_text(encoding='utf-8'))
        self.assertNotIn('plan.update', {row['function']['name'] for row in saved['tool_schemas']})
        self.assertFalse(self.api().read_plan(runtime.sessions, sid)['has_plan'])

    def test_restart_cannot_grant_another_completion_feedback_attempt(self):
        runtime = self.runtime()
        original_save = AdaptiveDriver._save
        class Interrupted(Exception):
            pass
        def save_and_interrupt(driver, state):
            original_save(driver, state)
            if state.get('plan_completion_checks') == 1 and state['status'] == 'running':
                raise Interrupted
        answers = iter([lambda: self.call(runtime, 'plan-create'), lambda: response('提前结束')])
        with mock.patch.object(AdaptiveDriver, '_save', save_and_interrupt):
            with mock.patch.object(llm, 'chat', side_effect=lambda *_a, **_kw: next(answers)()):
                with self.assertRaises(Interrupted):
                    self.start(runtime)
        fresh = self.runtime()
        with mock.patch.object(llm, 'chat', return_value=response('重启后仍未核查')) as model:
            result = AdaptiveDriver(fresh).resume(self.sid)
        self.assertEqual((result['status'], result['reason']), ('blocked', 'plan_incomplete'))
        self.assertEqual(model.call_count, 1)

    def test_new_turn_resets_feedback_count_and_does_not_inherit_old_plan(self):
        runtime = self.runtime()
        answers = iter([lambda: self.call(runtime, 'plan-create'), lambda: response('准备结束'),
                        lambda: response(calls=[('receipt', 'workspace.read', {})]),
                        lambda: self.call(runtime, 'plan-close', completed=True), lambda: response('完成')])
        with mock.patch.object(llm, 'chat', side_effect=lambda *_a, **_kw: next(answers)()):
            self.assertEqual(self.start(runtime)['status'], 'done')
        with mock.patch.object(llm, 'chat', return_value=response('新问题的答案')):
            result = runtime.send(self.sid, '一个新问题')
        self.assertEqual(result['status'], 'done')
        state = json.loads(AdaptiveDriver(runtime).path(self.sid).read_text(encoding='utf-8'))
        self.assertEqual(state['plan_completion_checks'], 0)
        self.assertFalse(any('_execution_plan' in message for message in state['messages']))
        self.assertFalse(self.api().read_plan(runtime.sessions, self.sid)['has_plan'])

    def test_feedback_keeps_consumed_token_budget(self):
        runtime = self.runtime()
        last = response('提前结束')
        last['usage'] = {'prompt_tokens': 99980, 'completion_tokens': 5, 'usage_reported': True}
        answers = iter([lambda: self.call(runtime, 'plan-create'), lambda: last])
        with mock.patch.object(llm, 'chat', side_effect=lambda *_a, **_kw: next(answers)()) as model:
            result = self.start(runtime)
        self.assertEqual(result['reason'], 'token_budget')
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result['tokens'], 100000)

    def test_plan_reminder_counts_towards_context_limit_before_model_call(self):
        runtime = self.runtime()
        self.profile['context_window'] = 800
        with mock.patch.object(self.api(), 'context_text', return_value='材料' * 3000):
            with mock.patch.object(llm, 'chat', return_value=response('不应请求')) as model:
                result = self.start(runtime)
        self.assertEqual(result['reason'], 'context_budget')
        model.assert_not_called()

    def test_plan_reminder_survives_compaction_and_stays_single(self):
        runtime = self.runtime()
        driver = AdaptiveDriver(runtime)
        sid = 'compact-plan'
        runtime.sessions.append(sid, 'user_message', {'text': 'original', 'mode': 'agent'})
        messages = [{'role': 'system', 'content': 'Frozen'}, {'role': 'user', 'content': 'original'}]
        for index in range(5):
            call = response('旧观察' * 300, calls=[(f'old-{index}', 'workspace.read', {})])
            messages += [call['assistant_message'], {'role': 'tool', 'tool_call_id': f'old-{index}',
                         'name': 'workspace.read', 'content': '{"ok":true}'}]
        state = driver_state(runtime, sid, messages, tool_schemas=runtime.tools_schema())
        with mock.patch.object(self.api(), 'context_text', return_value='当前计划：仍需核对真实回执'):
            driver._refresh_plan_context(state)
            with mock.patch.object(llm, 'available', return_value=False):
                self.assertTrue(driver._compress_context(state, self.profile))
            driver._refresh_plan_context(state)
            driver._refresh_plan_context(state)
        reminders = [message for message in state['messages'] if '_execution_plan' in message]
        self.assertEqual(len(reminders), 1)
        self.assertIn('仍需核对', reminders[0]['content'])
        self.assertEqual(state['messages'][0], messages[0])
        self.assertEqual(state['messages'][1], messages[1])

    def test_plan_state_failure_after_compaction_terminates_cleanly(self):
        runtime = self.runtime()
        self.profile['context_window'] = 800
        with mock.patch.object(self.api(), 'context_text', side_effect=['材料' * 3000, ValueError('计划损坏')]):
            with mock.patch.object(llm, 'chat', return_value=response('不应请求')) as model:
                result = self.start(runtime)
        self.assertEqual((result['status'], result['reason']), ('error', 'plan_state_error'))
        model.assert_not_called()

    def test_plan_state_failure_at_completion_terminates_cleanly(self):
        runtime = self.runtime()
        with mock.patch.object(self.api(), 'completion_issue', side_effect=ValueError('计划损坏')):
            with mock.patch.object(llm, 'chat', return_value=response('最终回答')):
                result = self.start(runtime)
        self.assertEqual((result['status'], result['reason']), ('error', 'plan_state_error'))

    def test_plan_update_never_enters_parallel_read_batch(self):
        runtime = self.runtime()
        driver = AdaptiveDriver(runtime)
        state = driver_state(runtime, 'batch', [])
        state['queue'] = [{'id': 'read', 'name': 'workspace.read', 'arguments': {}},
                          {'id': 'plan', 'name': 'plan.update', 'arguments': {}}]
        self.assertEqual(driver._collect_read_batch(state), state['queue'][:1])

    def test_all_provider_adapters_omit_private_plan_marker(self):
        message = {'role': 'user', 'content': 'Persisted execution plan',
                   '_execution_plan': {'version': 1}}
        for protocol in ('chat_completions', 'responses', 'anthropic_messages', 'gemini', 'ollama'):
            with self.subTest(protocol=protocol):
                payload = model_protocols.build_request({**self.profile, 'protocol': protocol},
                                                       [message], 0.2, 100)
                serialized = json.dumps(payload)
                self.assertNotIn('_execution_plan', serialized)
                self.assertIn('Persisted execution plan', serialized)


if __name__ == '__main__':
    unittest.main()
