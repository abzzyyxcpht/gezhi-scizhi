"""Read-only session observations and explicit offline context maintenance.

The journal owns execution status. Metrics describe the latest saved driver,
never guessed current settings; private prompts/provider blocks are not exposed.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
from datetime import datetime, timezone
import json
import re
import threading

from .session_lock import session_lock
from .state_store import write_json
from . import context_compaction

_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_CACHE = OrderedDict()
_RESEARCH_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_IDLE = {'done', 'paused'}


def _store_for(sid, store):
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        raise ValueError('会话 ID 无效')
    if store is None:
        from . import config
        from .agent_runtime import SessionStore
        store = SessionStore(config.DATA_DIR / 'sessions')
    if not store._path(sid).is_file():
        raise ValueError('会话不存在')
    return store


def _signature(path):
    try:
        info = path.stat()
        return info.st_mtime_ns, info.st_size, info.st_ino
    except FileNotFoundError:
        return None


def _load_state(path, sid):
    state = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(state, dict) or state.get('schema_version') != 1
            or state.get('session_id') != sid or not isinstance(state.get('messages'), list)
            or any(not isinstance(message, dict) for message in state['messages'])):
        raise ValueError('执行存档格式无效')
    return state


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _completeness(items):
    if any(item.get('usage_complete') is False for item in items):
        return False
    if all(item.get('usage_complete') is True and _count(item.get('tokens')) is not None
           and _count(item.get('model_calls', item.get('steps'))) is not None for item in items):
        return True
    return None


def _idle_reason(snapshot, state):
    if snapshot.get('status') not in _IDLE or state.get('status') not in _IDLE:
        return '请等待当前运行结束；仅完成或静止暂停的会话可整理。'
    if any(state.get(key) for key in ('pending', 'queue', 'active_call', 'active_calls',
                                      'model_inflight', 'unresolved')):
        return '仍有模型请求、审批或工具待处理，请先完成当前操作。'
    return ''


def _derive(state):
    from .agent_driver import AdaptiveDriver, AGENT_STEP_MAX_TOKENS
    from .context_manifest import public_manifest
    from .workbench import redact
    identity = state.get('model_identity') or {}
    tokens, calls = _count(state.get('tokens')), _count(state.get('model_calls', state.get('steps')))
    limit, steps = _count(state.get('token_budget')), _count(state.get('max_steps'))
    previous = state.get('turn_usage') or []
    usage = [item for item in previous if isinstance(item, dict)] + [state]
    total_tokens = [_count(item.get('tokens')) for item in usage]
    total_calls = [_count(item.get('model_calls', item.get('steps'))) for item in usage]
    known_tokens = [n for n in total_tokens if n is not None]
    known_calls = [n for n in total_calls if n is not None]
    message_tokens = AdaptiveDriver._estimate_tokens(state['messages'])
    schemas = state.get('tool_schemas')
    schema_tokens = (AdaptiveDriver._estimate_tokens(json.dumps(schemas, ensure_ascii=False))
                     if isinstance(schemas, list) and schemas else 0 if schemas == [] else None)
    input_tokens = message_tokens + schema_tokens if schema_tokens is not None else None
    # The saved budget and the driver constant bound the visible response.
    # Provider reasoning/cap defaults are not saved here and must not be guessed.
    reserve = min(AGENT_STEP_MAX_TOKENS, max(0, limit - tokens)) if limit is not None and tokens is not None else None
    return {**({'workspace_mode': state['workspace_mode']}
               if state.get('workspace_mode') in {'general', 'research'} else {}),
            'model': {'model': redact(str(identity['model'])) if identity.get('model') else None,
                      'protocol': str(identity.get('protocol') or '') or None},
            'context': {'tokens': message_tokens,
                        'schema_tokens': schema_tokens, 'input_tokens': input_tokens,
                        'output_reserve_tokens': reserve,
                        'output_reserve_source': 'driver_visible_output_bound' if reserve is not None else None,
                        'pressure_tokens': input_tokens + reserve if input_tokens is not None and reserve is not None else None,
                        'limit_tokens': _count(state.get('context_limit_tokens')),
                        'measurement': 'heuristic_tokens',
                        'compressions': _count(state.get('compressions', 0)),
                        'messages': len(state['messages'])},
            'context_manifest': public_manifest(state.get('context_manifest')),
            'budget': {'token_limit': limit, 'remaining_tokens': max(0, limit - tokens)
                       if limit is not None and tokens is not None else None,
                       'model_calls': calls, 'max_steps': steps,
                       'remaining_steps': max(0, steps - calls) if steps is not None and calls is not None else None},
            'tokens': tokens, 'usage_complete': _completeness([state]),
            'session_usage': {'tokens': sum(known_tokens) if known_tokens else None,
                              'model_calls': sum(known_calls) if known_calls else None,
                              'usage_complete': _completeness(usage)},
            '_idle_reason': _idle_reason({'status': state.get('status')}, state)}


def _metrics(path, sid):
    key = str(path.resolve())
    for _ in range(2):
        signature = _signature(path)
        if signature is None:
            return None, None
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached and cached[0] == signature:
                _CACHE.move_to_end(key)
                return copy.deepcopy(cached[1]), signature
        value = _derive(_load_state(path, sid))
        if signature != _signature(path):
            continue
        with _CACHE_LOCK:
            _CACHE[key] = (signature, value)
            _CACHE.move_to_end(key)
            while len(_CACHE) > 64:
                _CACHE.popitem(last=False)
        return copy.deepcopy(value), signature
    raise OSError('执行存档正在更新')


def _rewound(store, sid):
    recent = store.event_page(sid, limit=64, tail=True)['events']
    for event in reversed(recent):
        if event.get('type') == 'driver_started':
            return False
        if event.get('type') == 'completed' and event.get('payload', {}).get('reason') == 'rewound':
            return True
    return False


def _load_research_contexts(path):
    """Read request observations, exposing only recent, bounded metadata.

    These are independent research-tool requests, not additions to the main
    driver's frozen prefix. Raw prompts, responses and binding paths stay private.
    """
    from .context_manifest import public_manifest
    from .workbench import redact
    with path.open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('观察记录超过读取上限')
    value = json.loads(raw.decode('utf-8'))
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or not isinstance(value.get('entries'), list)):
        raise ValueError('观察记录格式无效')
    entries = value['entries']
    omitted = _count(value.get('omitted_entries')) or 0
    result = {'schema_version': 1, 'entries': [],
              'omitted_entries': omitted + max(0, len(entries) - 4)}
    for entry in entries[-4:]:
        if not isinstance(entry, dict):
            raise ValueError('观察条目格式无效')
        manifest = public_manifest(entry.get('context_manifest'))
        if manifest:
            sources = manifest['sources']
            manifest['omitted_sources'] = (manifest.get('omitted_sources', 0)
                                            + max(0, len(sources) - 24))
            manifest['sources'] = sources[:24]
        row = {'context_manifest': manifest}
        for key in ('request_id', 'node', 'prompt_key', 'observed_at'):
            field = entry.get(key)
            row[key] = redact(field)[:160] if isinstance(field, str) else ''
        outcome = entry.get('outcome')
        row['outcome'] = outcome if outcome in {'returned', 'error'} else 'unknown'
        result['entries'].append(row)
    return result


def _research_contexts(path):
    """Cached read-only view; never load source files to reconstruct history."""
    key = str(path.resolve())
    try:
        for _ in range(2):
            signature = _signature(path)
            if signature is None:
                return None
            with _CACHE_LOCK:
                cached = _RESEARCH_CACHE.get(key)
                if cached and cached[0] == signature:
                    _RESEARCH_CACHE.move_to_end(key)
                    return copy.deepcopy(cached[1])
            value = _load_research_contexts(path)
            if signature != _signature(path):
                continue
            with _CACHE_LOCK:
                _RESEARCH_CACHE[key] = (signature, value)
                _RESEARCH_CACHE.move_to_end(key)
                while len(_RESEARCH_CACHE) > 64:
                    _RESEARCH_CACHE.popitem(last=False)
            return copy.deepcopy(value)
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return {'schema_version': 1, 'entries': [], 'omitted_entries': 0,
            'issue': '工具内部模型请求记录暂不可读，请稍后重试。'}


def session_status(session_id, *, store=None):
    store = _store_for(session_id, store)
    snapshot = store.snapshot(session_id)
    path = store.root / f'{session_id}.driver.json'
    value, signature, issue = None, None, ''
    try:
        if _rewound(store, session_id):
            issue = '会话刚回滚，请先发送新一轮消息建立执行上下文。'
        else:
            value, signature = _metrics(path, session_id)
    except (OSError, ValueError, TypeError, KeyError):
        issue = '执行存档暂不可读，已保留日志状态；请稍后重试。'
    pending = snapshot.get('pending') or {}
    waiting = {'waiting_approval': '等待审批：' + str(pending.get('tool') or '工具调用'),
               'waiting_task': '等待科研任务完成或人工确认', 'waiting_children': '等待子任务结束',
               'paused': '会话已暂停', 'cancelling': '正在停止当前执行',
               'error': '执行失败，请查看执行记录', 'blocked': '操作已被阻止，请查看审批记录'}
    result = {'session_id': session_id, 'status': snapshot['status'],
              'agent': snapshot.get('agent'), 'mode': snapshot.get('mode'),
              'workspace_mode': snapshot.get('workspace_mode'), 'context_manifest': None,
              'driver': 'adaptive' if value or snapshot.get('driver') == 'adaptive' else 'planned',
              'model': {'model': None, 'protocol': None}, 'context': None, 'budget': None,
              'tokens': None, 'usage_complete': None, 'session_usage': None,
              'waiting_reason': waiting.get(snapshot['status'], ''), 'updated_at': None}
    if value:
        reason = value.pop('_idle_reason')
        result.update(value)
        result['updated_at'] = datetime.fromtimestamp(signature[0] / 1e9, tz=timezone.utc).isoformat()
        if snapshot['status'] not in _IDLE:
            reason = '请等待当前运行结束；运行中不会整理上下文。'
    else:
        reason = issue or '没有可整理的模型上下文；规则流程无需整理。'
        if not issue and isinstance(snapshot.get('plan'), dict):
            from .context_manifest import public_manifest
            result['context_manifest'] = public_manifest(snapshot['plan'].get('context_manifest'))
    result.update(can_compact=not bool(reason), compact_unavailable_reason=reason)
    result['research_contexts'] = _research_contexts(store.root / f'{session_id}.research-contexts.json')
    from .execution_plan import read_plan
    try:
        result['execution_plan'] = read_plan(store, session_id)
    except (OSError, ValueError, TypeError, KeyError):
        result['execution_plan'] = {'session_id': session_id, 'has_plan': False,
                                    'error': '执行计划暂不可读，请稍后重试'}

    return result


def _compacted_messages(messages):
    # Retain at least eight recent messages and move the cut backwards to keep
    # complete assistant/tool groups. The original request and system are exact.
    boundary = len(messages) - 8
    while boundary > 2 and messages[boundary].get('role') == 'tool':
        boundary -= 1
    if boundary < 4 or not messages or messages[0].get('role') != 'system':
        return None
    middle = messages[2:boundary]
    if not any(message.get('role') != 'user' for message in middle):
        return None
    if context_compaction.summary_room(messages[2:]) <= 0:
        return None
    lines = ['[手动上下文整理] 以下为早期记录的确定性摘录，可能省略细节；完整对话仍在历史中。']
    for item in middle:
        role = item.get('role') or 'record'
        if role == 'user' and not context_compaction.is_internal_summary(item):
            # User turns are copied verbatim below; do not make this summary a
            # second lossy representation of their instructions.
            text = '[用户消息已逐字保留]'
        elif context_compaction.is_internal_summary(item):
            # The shared helper carries this entire body verbatim, outside the
            # new deterministic excerpt. Never nest or truncate old summaries.
            continue
        else:
            text = str(item.get('content') or '')
        tools = ','.join(str((call.get('function') or call).get('name') or '')
                         for call in item.get('tool_calls') or [])
        lines.append(f'{role}: {text[:140]}' + (f' 工具: {tools}' if tools else ''))
    summary = '\n'.join(lines)
    compacted = context_compaction.compact_messages(
        messages, boundary, summary)
    if compacted is None:
        return None
    candidate, archived, _ = compacted
    return candidate, archived


def compact_session(session_id, *, store=None):
    from .agent_driver import AdaptiveDriver
    store = _store_for(session_id, store)
    path = store.root / f'{session_id}.driver.json'
    observed = _signature(path), _signature(store._path(session_id))
    status = session_status(session_id, store=store)
    if observed != (_signature(path), _signature(store._path(session_id))):
        raise ValueError('会话已变化，请刷新状态后重新整理。')
    if not status['can_compact']:
        raise ValueError(status['compact_unavailable_reason'])
    with session_lock(store.root / f'{session_id}.chat-turn'), session_lock(store._path(session_id)):
        # A click may have waited behind another turn: never compact the newer
        # turn implicitly, even if it completed before we acquired the lock.
        if observed != (_signature(path), _signature(store._path(session_id))):
            raise ValueError('会话已变化，请刷新状态后重新整理。')
        state = _load_state(path, session_id)
        reason = _idle_reason(store.snapshot(session_id), state)
        if reason or _rewound(store, session_id):
            raise ValueError(reason or '会话刚回滚，请先继续对话。')
        before = AdaptiveDriver._estimate_tokens(state['messages'])
        result = {'session_id': session_id, 'changed': False, 'before_tokens': before,
                  'after_tokens': before, 'removed_messages': 0, 'method': 'deterministic',
                  'model_calls': 0, 'message': '当前记录较短或无法缩减，无需整理；未调用模型。'}
        compacted = _compacted_messages(state['messages'])
        if compacted is None:
            return result
        messages, archived = compacted
        after = AdaptiveDriver._estimate_tokens(messages)
        if after >= before:
            return result
        before_messages = len(state['messages'])
        preserved_users = len(context_compaction.preserved_user_messages(archived))
        state['messages'] = messages
        state.setdefault('context_archive', []).append(copy.deepcopy(archived))
        state['compressions'] = state.get('compressions', 0) + 1
        state['last_compression_summary'] = messages[2]['content']
        write_json(path, state)
        result.update(changed=True, after_tokens=after, removed_messages=before_messages - len(messages),
                      message='已整理模型上下文，完整对话保留；确定性摘录可能省略细节，未调用模型。')
        try:
            store.append(session_id, 'context_compressed', {
                'method': 'manual_deterministic', 'before_tokens': before, 'after_tokens': after,
                'from_messages': before_messages, 'to_messages': len(messages),
                'compressed_count': before_messages - len(messages),
                'archived_count': len(archived), 'preserved_user_count': preserved_users})
        except OSError:
            result['message'] = '上下文已整理，但审计记录写入失败；请检查存储，未调用模型。'
        return result
