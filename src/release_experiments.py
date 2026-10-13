"""Functional release checks for the shared experimental runtime, with no provider."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager

from . import settings

FLAGS = ('experimental_message_queue', 'experimental_inflight_steering', 'experimental_evidence_reviews')


def defaults():
    if any(settings.load().get(name) is not False for name in FLAGS):
        raise ValueError('Experimental release defaults must remain explicitly off')
    return {'disabled_by_default': list(FLAGS)}


def _call(cmd, **fields):
    from .bridge import handle
    reply = handle({'cmd': cmd, **fields})
    if reply.get('ok') is not True:
        raise ValueError(reply.get('error') or 'Self-check did not receive a valid result')
    return reply


def _until(operation, ready):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = operation()
        if ready(value):
            return value
        time.sleep(.02)
    raise ValueError('Self-check state did not reach the expected boundary')


@contextmanager
def _enabled(flag):
    before = settings.load()
    settings.save({flag: True, 'agent_stream_enabled': False})
    try:
        yield
    finally:
        settings.save({flag: before.get(flag, False), 'agent_stream_enabled': before.get('agent_stream_enabled', True)})


def queue():
    from . import session_queue
    from .agent_runtime import SessionStore
    from . import config
    try:
        with _enabled('experimental_message_queue'):
            sid = _call('chat_new', title='Frozen queue self-check')['session_id']
            _call('chat_turn', session_id=sid, request='Offline plan', mode='plan', online=False)
            _call('queue_pause', session_id=sid)
            request = dict(session_id=sid, request='Queued offline plan', queue_request_id='frozen-queue')
            ticket = _call('queue_add', **request)['ticket']
            assert _call('queue_add', **request)['ticket']['id'] == ticket['id']
            _call('queue_resume', session_id=sid)
            _until(lambda: _call('queue_list', session_id=sid)['queue'],
                   lambda value: value['counts']['delivered'] == 1 and value['worker_state'] == 'idle')
            messages = [event for event in SessionStore(config.DATA_DIR / 'sessions').read(sid)
                        if event['type'] == 'user_message' and event['payload'].get('queue_ticket')]
            assert len(messages) == 1 and messages[0]['payload']['offline'] is True
            return 'persistent queue, idempotent receipt and one real offline plan delivery'
    finally:
        session_queue.close()


def steering():
    from . import config, llm
    from .agent_runtime import AgentRuntime
    from .agent_driver import AdaptiveDriver
    from .session_steering import handle
    runtime = AgentRuntime(session_root=config.DATA_DIR / 'sessions', offline=False, llm_enabled=True)
    store, sid = runtime.sessions, 'frozen-steering'
    request = 'Initial isolated request'
    store.append(sid, 'user_message', {'text': request, 'mode': 'agent', 'agent': 'research', 'offline': False})
    runtime._session_agent[sid] = 'research'
    original, observed = llm.chat, []
    def fixture(messages, **kwargs):
        observed.append(messages)
        if len(observed) == 1:
            state = handle({'cmd': 'steer_status', 'session_id': sid}, store=store)['steering']
            reply = handle({'cmd': 'steer_add', 'session_id': sid, 'expected_turn_id': state['turn_id'],
                            'steer_request_id': 'frozen-steer', 'request': 'Check counterevidence'}, store=store)
            assert reply['ok']
        return {'content': 'Fixture response', 'tool_calls': [], 'usage': {'prompt_tokens': 2, 'completion_tokens': 1}}
    try:
        with _enabled('experimental_inflight_steering'):
            llm.chat = fixture
            outcome = AdaptiveDriver(runtime).start(sid, request, 'research', system_prompt='Isolated release self-check.')
            assert outcome['status'] == 'done' and outcome['steps'] == 2 and outcome['tokens'] == 6
            assert observed[-1][-1]['content'] == 'Check counterevidence'
            assert sum(event['type'] == 'user_message' for event in store.read(sid)) == 1
            return 'same-turn correction applied with original budget; 2 fixture responses, 0 provider calls'
    finally:
        llm.chat = original


def review():
    from . import evidence_reviews
    try:
        with _enabled('experimental_evidence_reviews'):
            record = _call('notebook_save', record={'kind': 'hypothesis', 'title': 'Frozen review',
                'question': 'Does this fixture contain counterevidence?', 'synthetic': True})['record']
            job = _call('review_start', record_id=record['id'], expected_version=record['version'],
                        mode='local', review_request_id='frozen-review')['job']
            _until(lambda: _call('review_get', job_id=job['id'])['job'], lambda value: value['status'] == 'ready')
            assert _call('notebook_get', id=record['id'])['record']['version'] == 1
            _call('review_accept', job_id=job['id'])
            _call('review_accept', job_id=job['id'])
            accepted = _call('notebook_get', id=record['id'])['record']
            assert accepted['version'] == 2 and len(accepted['observations']) == 1
            old = _call('review_start', record_id=record['id'], expected_version=2,
                        mode='local', review_request_id='frozen-old-review')['job']
            _until(lambda: _call('review_get', job_id=old['id'])['job'], lambda value: value['status'] == 'ready')
            _call('notebook_save', record={'id': record['id'], 'limitations': 'Changed source'}, expected_version=2)
            from .bridge import handle
            assert handle({'cmd': 'review_accept', 'job_id': old['id']})['ok'] is False
            return 'background read-only review; explicit note saved once; stale record acceptance refused'
    finally:
        evidence_reviews.close()


def history():
    from . import config
    from .agent_runtime import SessionStore
    from .session_history import history as read_history, message, index_path
    store, sid = SessionStore(config.DATA_DIR / 'sessions'), 'frozen-history'
    for index in range(85):
        store.append(sid, 'user_message', {'text': f'History {index} 反例 api_key=frozen-hidden'})
    value = read_history(sid, store=store)
    assert value['total'] == 85 and value['page'] == 3 and 'frozen-hidden' not in json.dumps(value)
    warm = read_history(sid, store=store, query='History', page=1)
    assert warm['total'] == 85 and warm['decoded_events'] == 0
    store.append(sid, 'steer_applied', {'text': 'Long message ' * 2500})
    tail = read_history(sid, store=store)
    assert tail['decoded_events'] == 1 and tail['messages'][-1]['truncated']
    chunk = message(sid, tail['messages'][-1]['id'], store=store, generation=tail['generation'])
    assert chunk['next_offset'] == 20000
    assert index_path(store, sid).is_file()
    return {'messages': tail['total'], 'search_backend': warm['search_method'],
            'sqlite_cache': 'created and reused', 'long_message_chunk': True}
