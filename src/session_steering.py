"""Durable input for the active adaptive turn, independent of next-turn queues."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import uuid

from . import settings
from .session_lock import session_lock
from .state_store import write_json
from .workbench import redact

FLAG = 'experimental_inflight_steering'
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_OPEN = {'running', 'waiting_approval', 'waiting_task', 'waiting_children', 'paused'}


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('会话、回合或补充请求 ID 无效')
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def enabled():
    return settings.load().get(FLAG, False) is True


def _path(store, sid):
    return store.root / f'{_id(sid)}.steer.json'


def _read(path, sid):
    row = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(row, dict) or row.get('schema_version') != 1 or row.get('session_id') != sid
            or not isinstance(row.get('items'), list) or type(row.get('revision')) is not int
            or row.get('state') not in {'open', 'closed'}):
        raise ValueError('回合补充存档无效，原文件已保留')
    _id(row.get('turn_id'))
    for item in row['items']:
        if (not isinstance(item, dict) or not isinstance(item.get('request'), str)
                or item.get('state') not in {'pending', 'applied', 'expired'}):
            raise ValueError('回合补充条目无效，原文件已保留')
        _id(item.get('id'))
        _id(item.get('request_id'))
    return row


def _write(path, row):
    row['revision'] += 1
    row['updated_at'] = _now()
    write_json(path, row)


def begin(driver, state):
    """Start an explicitly new adaptive turn; never inherit old pending input."""
    sid = state['session_id']
    state['turn_id'] = uuid.uuid4().hex
    state['applied_steer_ids'] = []
    driver._save(state)
    path = _path(driver.store, sid)
    with session_lock(path):
        _write(path, {'schema_version': 1, 'session_id': sid, 'turn_id': state['turn_id'],
                      'state': 'open', 'revision': 0, 'items': [], 'reason': ''})
    driver.store.append(sid, 'turn_started', {'turn_id': state['turn_id']})


def _expire(row, reason):
    for item in row['items']:
        if item['state'] == 'pending':
            item.update(state='expired', reason=reason, updated_at=_now())


def _retire_stale(path, row):
    options, changed = settings.load(), False
    epoch = options.get('_inflight_steering_epoch', '')
    for item in row['items']:
        if item['state'] == 'pending' and (options.get(FLAG) is not True or item.get('feature_epoch', '') != epoch):
            item.update(state='expired', reason='运行中干预实验已关闭；再次开启不会恢复旧补充', updated_at=_now())
            changed = True
    if changed:
        _write(path, row)


def cancel(store, sid):
    path = _path(store, sid)
    if not path.exists():
        return
    with session_lock(path):
        row = _read(path, sid)
        _expire(row, '当前回合已请求停止')
        row.update(state='closed', reason='cancelled')
        _write(path, row)


def seal(driver, state, status):
    """Atomically choose either pending input or final completion, never both."""
    if not state.get('turn_id'):
        return True
    sid, path = state['session_id'], _path(driver.store, state['session_id'])
    with session_lock(path):
        row = _read(path, sid)
        if row['turn_id'] != state['turn_id']:
            raise ValueError('补充回合身份已改变')
        _retire_stale(path, row)
        if (status == 'done' and row['state'] == 'open' and enabled()
                and not (driver.store.root / f'{sid}.cancel').exists()
                and any(item['state'] == 'pending' for item in row['items'])):
            return False
        _expire(row, f'当前回合已结束：{status}')
        row.update(state='closed', reason=status)
        _write(path, row)
        return True


def has_pending(driver, state):
    if not state.get('turn_id') or not enabled():
        return False
    path = _path(driver.store, state['session_id'])
    with session_lock(path):
        row = _read(path, state['session_id'])
        _retire_stale(path, row)
        return row['turn_id'] == state['turn_id'] and row['state'] == 'open' and any(
            item['state'] == 'pending' for item in row['items'])


def consume(driver, state):
    """Called by the driver while it already owns the runtime effect lock."""
    if not state.get('turn_id'):
        return False
    sid, path = state['session_id'], _path(driver.store, state['session_id'])
    with session_lock(path):
        row = _read(path, sid)
        if row['turn_id'] != state['turn_id']:
            raise ValueError('补充回合身份已改变')
        _retire_stale(path, row)
        if row['state'] != 'open':
            return False
        if not enabled() or (driver.store.root / f'{sid}.cancel').exists():
            if any(item['state'] == 'pending' for item in row['items']):
                _expire(row, '实验已关闭或当前回合已停止')
                _write(path, row)
            return False
        pending = [item for item in row['items'] if item['state'] == 'pending']
        if not pending:
            return False
        # The executing tool is reconciled by the runtime before this boundary.
        if state.get('active_call') or state.get('active_calls'):
            return False
        for call in list(state['queue']):
            unresolved = dict(state['unresolved'])
            driver._observe(state, call, {'ok': False, 'error': 'steered_before_execution',
                                        'non_blocking': True, 'skipped': True, 'executed': False})
            state['unresolved'] = unresolved  # A skipped retry does not resolve an earlier failure.
            driver._save(state)
            driver.store.append(sid, 'tool_skipped', {'id': call['id'], 'name': call['name'],
                                                     'reason': 'steered_before_execution'})
        state.update(pending=None, status='running')
        known = state.setdefault('applied_steer_ids', [])
        applied_events = {event.get('payload', {}).get('steer_id') for event in driver.store.read(sid)
                          if event['type'] == 'steer_applied'}
        for item in pending:
            if item['id'] not in known:
                state['messages'].append({'role': 'user', 'content': item['request']})
                known.append(item['id'])
                driver._save(state)
            if item['id'] not in applied_events:
                driver.store.append(sid, 'steer_applied', {'steer_id': item['id'], 'turn_id': row['turn_id'],
                                                         'text': item['request']})
            item.update(state='applied', updated_at=_now())
        _write(path, row)
        return True


def handle(request, *, store=None):
    try:
        from .agent_runtime import SessionStore
        store = store or SessionStore()
        command = request.get('cmd')
        fields = {'cmd', 'session_id'}
        if command == 'steer_add':
            fields |= {'expected_turn_id', 'steer_request_id', 'request'}
        elif command != 'steer_status':
            raise ValueError('未知回合补充命令')
        if set(request) - fields:
            raise ValueError('回合补充包含不支持的字段')
        sid = _id(request.get('session_id'))
        path = _path(store, sid)
        if not store._path(sid).is_file():
            raise ValueError('请先选择有效会话')
        ticket = None
        with session_lock(path):
            row = _read(path, sid) if path.exists() else None
            if row is not None:
                _retire_stale(path, row)
            snapshot = store.snapshot(sid)
            if command == 'steer_add':
                options = settings.load()
                if options.get(FLAG) is not True:
                    raise ValueError('请先明确开启实验性运行中干预')
                turn, key = _id(request.get('expected_turn_id')), _id(request.get('steer_request_id'))
                text = request.get('request')
                if not isinstance(text, str) or not text.strip() or len(text) > 8000:
                    raise ValueError('补充要求须为 1–8000 字符')
                if row is None or row['turn_id'] != turn:
                    raise ValueError('当前回合已改变，请刷新后重新核对')
                ticket = next((item for item in row['items'] if item['request_id'] == key), None)
                if ticket:
                    if ticket['request'] != text:
                        raise ValueError('此补充标识已用于不同文本')
                else:
                    if row['state'] != 'open' or snapshot['status'] not in _OPEN or (store.root / f'{sid}.cancel').exists():
                        raise ValueError('当前没有可接收补充的模型回合；请使用下一轮队列')
                    if len(row['items']) >= 64 or sum(item['state'] == 'pending' for item in row['items']) >= 20:
                        raise ValueError('本轮补充已达上限（待处理20条，每轮64条）')
                    ticket = {'id': uuid.uuid4().hex, 'request_id': key, 'request': text,
                              'state': 'pending', 'created_at': _now(), 'updated_at': _now(),
                              'feature_epoch': options.get('_inflight_steering_epoch', '')}
                    row['items'].append(ticket)
                    _write(path, row)
                    store.append(sid, 'steer_received', {'steer_id': ticket['id'], 'turn_id': turn, 'text': text})
            view = {'session_id': sid, 'turn_id': row['turn_id'] if row else None, 'enabled': enabled(),
                    'state': row['state'] if row else 'unavailable',
                    'items': [{key: value for key, value in item.items() if key != 'feature_epoch'}
                              for item in row['items']] if row else [],
                    'status': snapshot['status'], 'revision': row['revision'] if row else 0,
                    'resume_required': snapshot['status'] in {'waiting_approval', 'waiting_task', 'waiting_children', 'paused'},
                    'reason': (row or {}).get('reason', '')}
        return redact({'ok': True, 'steering': view, **({'ticket': {key: value for key, value in ticket.items()
                                                                 if key != 'feature_epoch'}} if ticket else {})})
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'ok': False, 'error': redact(str(error))}
