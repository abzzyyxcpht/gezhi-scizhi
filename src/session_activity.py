"""Bounded, read-only activity and child discovery from existing journals.

Child metadata locates conversations; journal projections own their status.
Observation never registers a sidechain, resumes work, or grants permission.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import re
import stat

from .session_controls import _count, _store_for
from .workbench import redact

MAX_ACTIVITY_LIMIT = 100
MAX_CHILD_DEPTH = 4
MAX_METADATA_BYTES = 64 * 1024
MAX_DIRECTORY_SCAN = 500
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_STATUSES = {'unknown', 'draft', 'planned', 'queued', 'running', 'done', 'partial',
             'waiting_approval', 'waiting_task', 'waiting_children', 'paused',
             'cancelling', 'cancelled', 'interrupted', 'error', 'blocked'}
_TERMINAL = {'done', 'partial', 'cancelled', 'interrupted', 'error', 'blocked'}
_TOOL_EVENTS = {'tool_call', 'tool_result', 'tool_skipped', 'permission_requested',
                'permission_granted', 'permission_denied'}
_STAGE_LABELS = {'plan_created': '执行计划', 'driver_started': '模型执行',
                 'assistant_message': '模型步骤', 'driver_paused': '执行等待',
                 'context_compressed': '上下文整理', 'cancel_requested': '停止请求',
                 'completed': '执行结束', 'error': '执行异常',
                 'stage_started': '阶段执行', 'stage_completed': '阶段完成',
                 'node_started': '阶段执行', 'node_completed': '阶段完成'}


def _text(value, limit=240):
    if not isinstance(value, str):
        return ''
    return _CONTROL.sub('', redact(value))[:limit]


def _status(value):
    return value if isinstance(value, str) and value in _STATUSES else 'unknown'


def _safe_path(root, path):
    """Reject outside paths and links/reparse points before any content read."""
    root, path = root.absolute(), path.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError('会话路径无效') from exc
    if any(part in {'.', '..'} for part in relative.parts):
        raise ValueError('会话路径无效')
    for candidate in (root, *(root.joinpath(*relative.parts[:i])
                              for i in range(1, len(relative.parts) + 1))):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('会话路径包含链接')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('会话路径无效')
    return path


def _journal_path(store, sid, parent=None):
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        raise ValueError('会话 ID 无效')
    path = store._path(sid)
    relative = path.absolute().relative_to(store.root.absolute()).parts
    normal = relative == (f'{sid}.jsonl',)
    sidechain = (len(relative) == 3 and _ID.fullmatch(relative[0])
                 and relative[1:] == ('children', f'{sid}.jsonl'))
    if not (normal or sidechain) or (parent is not None and relative != (
            parent, 'children', f'{sid}.jsonl')):
        raise ValueError('会话目录关系无效')
    return _safe_path(store.root, path)


def _observe(store, sid, limit, parent=None):
    path = _journal_path(store, sid, parent)
    if not path.is_file():
        return {'status': 'unknown'}, [], False
    try:
        snapshot = store.snapshot(sid)
        events = store.event_page(sid, limit=limit, tail=True)['events']
        if not isinstance(snapshot, dict):
            raise ValueError('会话投影无效')
        return snapshot, events, len(events) >= limit
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {'status': 'unknown'}, [], False


def _event_id(event):
    # Persisted timestamps/identities stay stable when this same suffix is read.
    identity = json.dumps([event.get('type'), event.get('timestamp'), event.get('payload')],
                          sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]


def _items(events, limit, session_status, turn_id=None):
    rows = {}
    # The full projection retains the current epoch when its marker has fallen
    # outside this bounded suffix, keeping live tool identities stable.
    turn = _text(turn_id, 128) or 'suffix'
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        kind = event.get('type')
        payload = event.get('payload')
        if not isinstance(payload, dict):
            continue
        if kind in {'user_message', 'turn_started'}:
            turn = _text(payload.get('turn_id'), 128) or _event_id(event)
            continue
        if kind in _TOOL_EVENTS:
            call_id = _text(payload.get('tool_call_id') or payload.get('id'), 128)
            key = f'tool:{turn}:' + (call_id or _event_id(event))
            old = rows.get(key, {})
            label = _text(payload.get('tool') or payload.get('name'), 120) or old.get('label') or '工具调用'
            state, summary = 'running', ''
            if kind == 'tool_result':
                result = payload.get('result')
                result = result if isinstance(result, dict) else {}
                state = _status(result.get('status'))
                if result.get('ok') is False and state in {'done', 'running', 'queued', 'unknown'}:
                    state = 'error'
                elif state == 'unknown':
                    state = 'error' if result.get('ok') is False else 'done' if result.get('ok') is True else 'unknown'
                summary = _text(result.get('error') or result.get('message'))
            elif kind == 'permission_requested':
                state, summary = 'waiting_approval', '等待工具审批'
            elif kind == 'permission_denied':
                state, summary = 'blocked', '审批已拒绝'
            elif kind == 'tool_skipped':
                state, summary = 'blocked', '工具未执行'
            elif kind == 'permission_granted':
                state, summary = 'queued', '审批已允许，等待执行记录'
            rows[key] = {'id': key, 'type': 'tool', 'label': label,
                         'status': state, 'summary': summary, '_order': index}
        elif kind in {'subagent_started', 'subagent_status', 'subagent_completed'}:
            child = _text(payload.get('child'), 128)
            key = 'agent:' + (child or _event_id(event))
            state = _status(payload.get('status'))
            if kind == 'subagent_started':
                state = 'running'
            # subagent_completed alone carries no concrete stop reason; a
            # following subagent_status supplies it. Do not infer success.
            rows[key] = {'id': key, 'type': 'agent',
                         'label': _text(payload.get('agent'), 120) or '子任务',
                         'status': state, 'summary': child, '_order': index}
        elif kind in _STAGE_LABELS:
            state = 'done'
            if kind in {'driver_started', 'stage_started', 'node_started'}:
                state = 'running'
            elif kind == 'error':
                state = 'error'
            elif kind == 'completed':
                state = _status(payload.get('status'))
            elif kind == 'cancel_requested':
                state = 'cancelling'
            elif kind == 'driver_paused':
                state = _status(payload.get('status'))
            label = _STAGE_LABELS[kind]
            summary = _text(payload.get('message') or payload.get('error'))
            if kind == 'assistant_message':
                step = _count(payload.get('step'))
                label += f' {step}' if step is not None else ''
                # Assistant content/native tool arguments are deliberately private.
                summary = '模型返回已记录'
            rows['stage:' + _event_id(event)] = {
                'id': 'stage:' + _event_id(event), 'type': 'stage', 'label': label,
                'status': state, 'summary': summary, '_order': index}
    values = sorted(rows.values(), key=lambda row: row['_order'])
    truncated = len(values) > limit
    values = values[-limit:]
    for row in values:
        row.pop('_order')
        if row['type'] == 'tool' and row['status'] in {'running', 'queued'} and session_status in _TERMINAL:
            row.update(status='unknown', summary='未记录工具结果')
        elif row['type'] == 'stage' and row['status'] == 'running' and session_status in _TERMINAL:
            row.update(status='unknown', summary='会话已结束，未记录该阶段完成')
    return values, truncated


def _child_measurements(events, truncated):
    steps, tokens, reported = None, 0, False
    complete = not truncated
    for event in events:
        payload = event.get('payload')
        if not isinstance(payload, dict):
            continue
        if event.get('type') == 'assistant_message':
            steps = _count(payload.get('step'))
        if event.get('type') not in {'assistant_message', 'context_summary_usage'}:
            continue
        usage = payload.get('usage')
        if not isinstance(usage, dict):
            complete = False
            continue
        prompt, completion = _count(usage.get('prompt_tokens')), _count(usage.get('completion_tokens'))
        if usage.get('usage_reported') is not True or prompt is None or completion is None:
            complete = False
            continue
        reported = True
        tokens += prompt + completion
    return steps, tokens if reported and complete else None


def _child_entries(store, parent, maximum):
    directory = _safe_path(store.root, store.root / parent / 'children')
    if not directory.is_dir():
        return [], False
    entries, truncated = [], False
    with _directory_entries(directory) as iterator:
        for count, path in enumerate(iterator):
            if count >= MAX_DIRECTORY_SCAN:
                truncated = True
                break
            if not path.name.endswith('.meta.json'):
                continue
            sid = path.name.removesuffix('.meta.json')
            if _ID.fullmatch(sid):
                entries.append((sid, path))
                if len(entries) > maximum:
                    truncated = True
                    break
    return sorted(entries[:maximum], key=lambda entry: entry[0]), truncated


def _directory_entries(directory):
    import os
    # scandir closes even when a bounded traversal stops early.
    return os.scandir(directory)


def _children(store, sid, limit, event_limit):
    result, truncated, visited = [], False, {sid}
    pending = deque([(sid, 0)])
    while pending:
        parent, depth = pending.popleft()
        try:
            entries, omitted = _child_entries(store, parent, limit - len(result) + 1)
        except (OSError, ValueError):
            truncated = True
            continue
        truncated |= omitted
        if depth >= MAX_CHILD_DEPTH:
            truncated |= bool(entries)
            continue
        for child, entry in entries:
            if child in visited:
                truncated = True
                continue
            if len(result) >= limit:
                return result, True
            visited.add(child)
            metadata = {}
            try:
                path = _safe_path(store.root, store.root / parent / 'children' / entry.name)
                with path.open('rb') as stream:
                    raw = stream.read(MAX_METADATA_BYTES + 1)
                if len(raw) > MAX_METADATA_BYTES:
                    raise ValueError('子任务目录记录过大')
                metadata = json.loads(raw)
                if (not isinstance(metadata, dict) or metadata.get('schema_version') != 1
                        or metadata.get('child') != child or metadata.get('parent') != parent):
                    raise ValueError('子任务目录关系无效')
                snapshot, events, bounded = _observe(store, child, event_limit, parent)
            except (OSError, ValueError, TypeError, KeyError):
                metadata, snapshot, events, bounded = {}, {'status': 'unknown'}, [], False
            state = _status(snapshot.get('status'))
            steps, tokens = _child_measurements(events, bounded)
            permission = snapshot.get('pending')
            permission = permission if isinstance(permission, dict) else {}
            result.append({'session_id': child, 'parent_id': parent,
                'agent': _text(snapshot.get('agent') or metadata.get('agent'), 120),
                'request': _text(snapshot.get('request') or metadata.get('request'), 400),
                'status': state,
                'pending': {'tool': _text(permission.get('tool'), 120),
                            'id': _text(permission.get('id'), 128)} if permission else None,
                'steps': steps, 'tokens': tokens, 'depth': depth + 1})
            # Invalid metadata is a diagnostic leaf, never a navigation authority.
            if metadata:
                pending.append((child, depth + 1))
    return result, truncated


def session_activity(session_id, *, store=None, limit=40):
    """Return recent actual work and at most ``limit`` descendant observations."""
    if type(limit) is not int or not 1 <= limit <= MAX_ACTIVITY_LIMIT:
        raise ValueError('活动条数须为 1–100')
    # Validate the resolved path before the shared existence check.
    if not isinstance(session_id, str) or not _ID.fullmatch(session_id):
        raise ValueError('会话 ID 无效')
    if store is None:
        from . import config
        from .agent_runtime import SessionStore
        root = config.DATA_DIR / 'sessions'
        if not root.is_dir():
            raise ValueError('会话不存在')
        store = SessionStore(root)
    _journal_path(store, session_id)
    store = _store_for(session_id, store)
    event_limit = min(500, max(41, limit * 4 + 1))
    snapshot, events, bounded = _observe(store, session_id, event_limit)
    state = _status(snapshot.get('status'))
    items, omitted = _items(events, limit, state, snapshot.get('turn_id'))
    children, child_omitted = _children(store, session_id, limit, event_limit)
    return {'session_id': session_id, 'status': state, 'items': items,
            'children': children, 'truncated': bool(bounded or omitted or child_omitted)}
