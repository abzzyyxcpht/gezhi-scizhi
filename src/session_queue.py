"""Experimental, durable next-turn delivery. This module never steers a turn.

Queue transactions and dispatcher ownership use separate kernel locks. A live
instance lease closes the enqueue/thread-start recovery race without probing
Windows processes. The normal chat path still owns model/tool permissions.
"""
from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from weakref import WeakValueDictionary

from . import config, settings
from .session_lock import session_lock
from .state_store import write_json
from .workbench import redact

COMMANDS = frozenset({'queue_add', 'queue_list', 'queue_remove', 'queue_discard',
                      'queue_pause', 'queue_resume', 'queue_reorder'})
_ACTIVE = {'queued', 'dispatching', 'needs_review'}
_READY = {'draft', 'done', 'planned'}
_STOPPED = {'cancelled', 'cancelling', 'error', 'blocked'}
_LOCKS = WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()
_MANAGER = None
_MANAGER_GUARD = threading.Lock()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('会话、请求或队列项标识无效')
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _enabled():
    return settings.load().get('experimental_message_queue', False) is True


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


class _Lease:
    """Nonblocking cross-thread/process ownership; lock files are not unlinked."""
    def __init__(self, stream, local):
        self.stream, self.local = stream, local

    @classmethod
    def take(cls, path):
        key = os.path.normcase(str(path.resolve()))
        with _LOCKS_GUARD:
            local = _LOCKS.get(key)
            if local is None:
                local = threading.Lock()
                _LOCKS[key] = local
        if not local.acquire(blocking=False):
            return None
        stream = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open('a+b', buffering=0)
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return cls(stream, local)
        except OSError as exc:
            if stream is not None:
                stream.close()
            local.release()
            if stream is not None and exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return None
            raise

    def close(self):
        if self.stream is None:
            return
        try:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None
            self.local.release()


def _held(path):
    if not path.exists():
        return False
    try:
        lease = _Lease.take(path)
    except OSError:
        return None
    if lease is None:
        return True
    lease.close()
    return False


class QueueBusy(ValueError):
    queue_code = 'queue_busy'


class QueueCancelled(ValueError):
    queue_code = 'queue_cancelled'


def _read_state(path, sid):
    """Validate persisted containers before any mutation; preserve bad bytes."""
    state = json.loads(path.read_text(encoding='utf-8'))
    invalid = '队列存档格式无效；原文件已保留'
    if not isinstance(state, dict) or state.get('schema_version') != 1 or state.get('session_id') != sid:
        raise ValueError(invalid)
    for key in ('revision', 'cancel_epoch', 'control_generation'):
        value = state.get(key, 0)
        if type(value) is not int or value < 0:
            raise ValueError(invalid)
    if 'revision' not in state or not isinstance(state.get('audit'), list):
        raise ValueError(invalid)
    if any(not isinstance(entry, dict) for entry in state['audit']):
        raise ValueError(invalid)
    if not isinstance(state.get('items'), list):
        raise ValueError(invalid)
    for item in state['items']:
        if (not isinstance(item, dict) or not isinstance(item.get('request'), str)
                or not isinstance(item.get('frozen'), dict)
                or item.get('state') not in _ACTIVE | {'delivered', 'removed'}):
            raise ValueError(invalid)
        _id(item.get('id'))
        _id(item.get('request_id'))
    for key in ('paused', 'resume_required'):
        if type(state.get(key)) is not bool:
            raise ValueError(invalid)
    return state


def pause_for_cancel(session_id, *, session_root=None):
    """Pause only an existing queue, synchronously with the real cancel entry."""
    sid = _id(session_id)
    path = Path(session_root or config.DATA_DIR / 'sessions') / f'{sid}.queue.json'
    if not path.is_file():
        return
    with session_lock(path):
        state = _read_state(path, sid)
        state.update(paused=True, resume_required=True, pause_reason='用户已停止当前执行，后续队列已暂停')
        state['cancel_epoch'] = state.get('cancel_epoch', 0) + 1
        state['revision'] += 1
        state['updated_at'] = _now()
        state['audit'].append({'type': 'session_cancelled', 'timestamp': state['updated_at'], 'ticket_id': None})
        write_json(path, state)


def record_queued_message(store, sid, payload, ticket_id):
    """Linearize cancellation against the actual user-message acceptance."""
    path = store.root / f'{_id(sid)}.queue.json'
    with session_lock(path):
        state = _read_state(path, sid)
        ticket = next((item for item in state['items'] if item['id'] == ticket_id), None)
        if not ticket or ticket['state'] != 'dispatching':
            raise ValueError('队列项不再等待本次接纳，不会重复提交')
        if ticket.get('claim_cancel_epoch', 0) != state.get('cancel_epoch', 0):
            raise QueueCancelled('本次派发已被取消；消息保留在暂停队列中')
        if not _enabled():
            raise QueueCancelled('实验功能已关闭，本次派发不会开始')
        event = store.append(sid, 'user_message', {**payload, 'queue_ticket': ticket_id})
        ticket.update(state='delivered', updated_at=_now())
        state['revision'] += 1
        state['updated_at'] = _now()
        state['audit'].append({'type': 'delivered', 'timestamp': state['updated_at'], 'ticket_id': ticket_id})
        write_json(path, state)
        return event


@contextmanager
def queued_turn_lock(session_id):
    """A queued turn must defer, never wait behind a newer manual turn."""
    path = config.DATA_DIR / 'sessions' / f'{_id(session_id)}.chat-turn.approval.lock'
    lease = _Lease.take(path)
    if lease is None:
        raise QueueBusy('会话正在处理其他提交，队列继续等待')
    try:
        yield
    finally:
        lease.close()


def _materials(row):
    manifest, project_path = [], ''
    if row.get('project_id'):
        from .project_workspace import attachment_context, get_project
        project_path = get_project(row['project_id'])['path']
        paths = list(row.get('attachments') or [])
        # Mirror chat_turn: project rules belong to its frozen system prefix,
        # not to implicit live attachments in every queued user message.
        if paths:
            _, manifest = attachment_context(row['project_id'], paths, max_chars=20000)
    return project_path, manifest


def _frozen_context(sid, req, *, row=None, manifest=None, project_path=None):
    from . import session_workspace
    from .agents.specs import ROSTER
    row = row or session_workspace._get(sid)
    if row.get('archived'):
        raise ValueError('已归档会话不能加入后续消息')
    mode = req.get('mode') or row.get('mode') or 'agent'
    agent = req.get('agent') or row.get('agent') or 'research'
    agent = 'critic' if mode == 'review' and agent != 'general' else agent
    if mode not in {'agent', 'plan', 'review'} or agent not in ROSTER:
        raise ValueError('会话模式或智能体无效')
    events = session_workspace._store().read(sid)
    user = next((e.get('payload') or {} for e in reversed(events) if e.get('type') == 'user_message'), None)
    if user is not None:
        if mode != (row.get('mode') or 'agent') or agent != row.get('agent', 'research'):
            raise ValueError('会话角色或模式已变化，请放弃旧队列项并重新加入')
        offline = bool(user.get('offline', True))
        if 'online' in req and (type(req['online']) is not bool or req['online'] != (not offline)):
            raise ValueError('队列必须沿用会话保存的在线或离线模式')
    else:
        online = req.get('online', not settings.load().get('default_offline', True))
        if type(online) is not bool:
            raise ValueError('online 必须为布尔值')
        offline = not online
    identity = None
    if not offline:
        from .agent_driver import AdaptiveDriver
        identity = AdaptiveDriver._identity()
    if manifest is None:
        project_path, manifest = _materials(row)
    return {'agent': agent, 'mode': mode, 'offline': offline, 'project_id': row.get('project_id') or '',
            'project_path': project_path or '', 'attachments': list(row.get('attachments') or []),
            'materials': manifest, 'fork_context_digest': _digest(row.get('fork_context') or ''),
            'model_signature': _digest(identity) if identity is not None else None}


def validate_dispatch(req, row, manifest, project_path):
    """Called under chat-turn lock, using the exact material snapshot being sent."""
    if not _enabled():
        raise ValueError('研究接力队列实验已关闭')
    sid, ticket_id = _id(req.get('session_id')), _id(req.get('queue_ticket'))
    path = config.DATA_DIR / 'sessions' / f'{sid}.queue.json'
    with session_lock(path):
        state = _read_state(path, sid)
        ticket = next((item for item in state['items'] if item['id'] == ticket_id), None)
        if not ticket or ticket['state'] != 'dispatching' or ticket['request'] != req.get('request'):
            raise ValueError('队列项已变化或不属于本次提交')
        if ticket.get('claim_cancel_epoch', 0) != state.get('cancel_epoch', 0):
            raise QueueCancelled('本次派发已被取消，队列继续暂停')
        current = _frozen_context(sid, req, row=row, manifest=manifest, project_path=project_path)
        if current != ticket['frozen']:
            raise ValueError('会话、模型配置或附件材料已变化；请核对后放弃旧项并重新加入')
        from . import session_workspace
        if any(e.get('type') == 'user_message' and e.get('payload', {}).get('queue_ticket') == ticket_id
               for e in session_workspace._store().read(sid)):
            raise ValueError('此队列项已被会话接受，不会重复提交')


class SessionQueue:
    def __init__(self, data_dir=None, *, dispatch=None, poll_interval=.2):
        from .agent_runtime import SessionStore
        self.root = Path(data_dir or config.DATA_DIR) / 'sessions'
        self.store = SessionStore(self.root)
        self.owner_id = uuid.uuid4().hex
        self._owner = _Lease.take(self.root / '.queue-owners' / f'{self.owner_id}.lock')
        self._dispatch = dispatch or self._chat_turn
        self._interval = max(.01, poll_interval)
        self._shutdown = threading.Event()
        self._wake = threading.Event()
        self._guard = threading.RLock()
        self._workers = {}
        self._owned_sessions = set()
        self._control_cursors = {}

    @staticmethod
    def _chat_turn(req):
        from .session_workspace import handle as session_handle
        return session_handle(req)

    def _path(self, sid):
        return self.root / f'{sid}.queue.json'

    def _run_path(self, sid):
        return self.root / f'{sid}.queue-run.lock'

    def _load(self, sid):
        path = self._path(sid)
        if not path.is_file():
            return {'schema_version': 1, 'session_id': sid, 'revision': 0, 'paused': False,
                    'resume_required': False, 'pause_reason': '', 'owner_id': self.owner_id,
                    'worker_state': 'idle', 'waiting_for': '', 'items': [], 'audit': [],
                    'cancel_epoch': 0, 'control_generation': 0, 'control_cursor': None}
        return _read_state(path, sid)

    def _save(self, state, event, ticket_id=None):
        state['revision'] += 1
        state['updated_at'] = _now()
        state['audit'].append({'type': event, 'timestamp': state['updated_at'], 'ticket_id': ticket_id})
        write_json(self._path(state['session_id']), state)

    @staticmethod
    def _pause(state, reason):
        state.update(paused=True, resume_required=True, pause_reason=reason)

    def _recover(self, sid):
        with session_lock(self._path(sid)):
            state = self._load(sid)
            if not any(item['state'] in _ACTIVE for item in state['items']):
                return
            owner = state.get('owner_id')
            alive = _held(self.root / '.queue-owners' / f'{_id(owner)}.lock') if owner else False
            abandoned_dispatch = (any(item['state'] == 'dispatching' for item in state['items'])
                                  and _held(self._run_path(sid)) is False)
            if alive is not False and not abandoned_dispatch:
                return
            for item in state['items']:
                if item['state'] == 'dispatching':
                    item.update(state='needs_review', error='上次派发中断，是否已执行尚不确定；请核查历史后放弃该项。', updated_at=_now())
            if not state.get('resume_required') or state.get('worker_state') != 'stopped':
                self._pause(state, '执行进程已结束，请核查队列并显式恢复')
                state['worker_state'] = 'stopped'
                self._save(state, 'owner_recovered')

    @staticmethod
    def _public_ticket(item, resume_required=False):
        value = {key: item.get(key) for key in ('id', 'request_id', 'state', 'request', 'created_at',
            'updated_at', 'agent', 'mode', 'offline', 'project_id', 'error')}
        value['resume_required'] = item['state'] in _ACTIVE and (resume_required or item['state'] == 'needs_review')
        return redact(value)

    def _public(self, state):
        terminal = [item for item in state['items'] if item['state'] not in _ACTIVE][-50:]
        shown = {item['id'] for item in terminal}
        shown.update(item['id'] for item in state['items'] if item['state'] in _ACTIVE)
        items = [self._public_ticket(item, state['resume_required']) for item in state['items'] if item['id'] in shown]
        active = _held(self._run_path(state['session_id']))
        if active is False and state.get('worker_state') not in {'idle', 'stopped'}:
            active = None
        return redact({'session_id': state['session_id'], 'revision': state['revision'], 'enabled': _enabled(),
            'paused': state['paused'], 'pause_reason': state['pause_reason'],
            'resume_required': state['resume_required'], 'worker_active': active,
            'worker_state': state.get('worker_state', 'unknown'), 'waiting_for': state.get('waiting_for', ''),
            'items': items, 'total_items': len(state['items']), 'history_truncated': len(items) < len(state['items']),
            'counts': {kind: sum(item['state'] == kind for item in state['items'])
                                      for kind in ('queued', 'dispatching', 'needs_review', 'delivered', 'removed')}})

    def handle(self, req):
        try:
            cmd = req.get('cmd')
            if cmd not in COMMANDS:
                raise ValueError('未知队列命令')
            if cmd != 'queue_list' and not _enabled():
                raise ValueError('研究接力队列是实验功能，请先在设置中明确开启')
            sid = _id(req.get('session_id'))
            if not self.store._path(sid).is_file():
                raise ValueError('请先创建或打开一个有效会话')
            self._recover(sid)
            ticket_id, start = None, False
            frozen = None
            if cmd == 'queue_add':
                request = req.get('request')
                if not isinstance(request, str) or not request.strip() or len(request) > 32000:
                    raise ValueError('消息须为 1–32000 字符')
                request_id = _id(req.get('queue_request_id') or req.get('request_id') or uuid.uuid4().hex)
                with session_lock(self._path(sid)):
                    existing = next((item for item in self._load(sid)['items'] if item['request_id'] == request_id), None)
                if existing is None:
                    frozen = _frozen_context(sid, req)
            with session_lock(self._path(sid)):
                state = self._load(sid)
                if cmd == 'queue_list' and not _enabled() and not state['paused'] and state['items']:
                    self._pause(state, '实验功能已关闭，队列记录已保留')
                    self._save(state, 'feature_disabled')
                if cmd == 'queue_add':
                    existing = next((item for item in state['items'] if item['request_id'] == request_id), None)
                    if existing:
                        mode = req.get('mode') or existing['mode']
                        agent = req.get('agent') or existing['agent']
                        agent = 'critic' if mode == 'review' and agent != 'general' else agent
                        if (existing['request'] != request or mode != existing['mode'] or agent != existing['agent']
                                or 'online' in req and req['online'] != (not existing['offline'])):
                            raise ValueError('幂等请求标识已用于不同消息或上下文')
                        ticket_id = existing['id']
                    else:
                        if sum(item['state'] in _ACTIVE for item in state['items']) >= 20:
                            raise ValueError('每个会话最多保留 20 个待处理队列项')
                        ticket_id = uuid.uuid4().hex
                        if not state['items']:
                            state['control_cursor'] = self.store.event_page(sid, limit=1, tail=True)['cursor']
                        state['items'].append({'id': ticket_id, 'request_id': request_id, 'request': request,
                            'state': 'queued', 'created_at': _now(), 'updated_at': _now(), 'error': '',
                            **{key: frozen[key] for key in ('agent', 'mode', 'offline', 'project_id')}, 'frozen': frozen})
                        if not state['paused'] and not state['resume_required']:
                            owner = state.get('owner_id')
                            alive = _held(self.root / '.queue-owners' / f'{_id(owner)}.lock') if owner else False
                            if owner == self.owner_id or state.get('worker_state') == 'idle' or alive is False:
                                state.update(owner_id=self.owner_id, worker_state='starting')
                                start = True
                        self._save(state, 'queued', ticket_id)
                elif cmd in {'queue_remove', 'queue_discard'}:
                    ticket_id = _id(req.get('ticket_id'))
                    item = next((item for item in state['items'] if item['id'] == ticket_id), None)
                    allowed = {'queued'} if cmd == 'queue_remove' else {'queued', 'needs_review'}
                    if not item or item['state'] not in allowed:
                        raise ValueError('只能移除尚未派发的项；待核查项请使用放弃操作')
                    item.update(state='removed', updated_at=_now())
                    self._save(state, 'discarded' if cmd == 'queue_discard' else 'removed', ticket_id)
                elif cmd == 'queue_reorder':
                    ids = req.get('ordered_ids')
                    pending = [item for item in state['items'] if item['state'] == 'queued']
                    if (not isinstance(ids, list) or any(not isinstance(value, str) for value in ids)
                            or sorted(ids) != sorted(item['id'] for item in pending)):
                        raise ValueError('重排必须恰好包含每个待发送项一次')
                    by_id = {item['id']: item for item in pending}
                    iterator = iter(ids)
                    state['items'] = [by_id[next(iterator)] if item['state'] == 'queued' else item for item in state['items']]
                    self._save(state, 'reordered')
                elif cmd == 'queue_pause':
                    self._pause(state, '用户已暂停队列；正在执行的回合保持原样')
                    self._save(state, 'paused')
                elif cmd == 'queue_resume':
                    if any(item['state'] == 'needs_review' for item in state['items']):
                        raise ValueError('有待核查项，请先核查并放弃后重新加入；恢复不会默许变化')
                    state.update(paused=False, resume_required=False, pause_reason='', owner_id=self.owner_id,
                                 worker_state='starting')
                    state['control_generation'] = state.get('control_generation', 0) + 1
                    state['control_cursor'] = self.store.event_page(sid, limit=1, tail=True)['cursor']
                    self._save(state, 'resumed')
                    start = True
                view = self._public(state)
                public_ticket = self._public_ticket(next(item for item in state['items'] if item['id'] == ticket_id),
                                                     state['resume_required']) if ticket_id else None
            if start:
                self._start(sid)
            self._wake.set()
            result = {'ok': True, 'queue': view}
            if ticket_id:
                result['ticket'] = public_ticket
            if cmd == 'queue_add':
                result['idempotency_key_generated'] = not bool(req.get('queue_request_id') or req.get('request_id'))
            return result
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return {'ok': False, 'error': redact(str(exc))}

    def _start(self, sid):
        with self._guard:
            if self._shutdown.is_set():
                raise ValueError('队列服务已停止，请重新打开应用后恢复')
            old = self._workers.get(sid)
            if old and old.is_alive():
                return
            worker = threading.Thread(target=self._run, args=(sid,), name=f'scizhi-queue-{sid}', daemon=True)
            self._workers[sid] = worker
            self._owned_sessions.add(sid)
            try:
                worker.start()
            except Exception:
                self._workers.pop(sid, None)
                with session_lock(self._path(sid)):
                    state = self._load(sid)
                    self._pause(state, '后台队列未能启动，请重试恢复')
                    self._save(state, 'worker_start_failed')
                raise

    def _mark_delivery(self, sid, ticket_id):
        with session_lock(self._path(sid)):
            state = self._load(sid)
            ticket = next(item for item in state['items'] if item['id'] == ticket_id)
            if ticket['state'] == 'dispatching':
                ticket.update(state='delivered', updated_at=_now())
                self._save(state, 'delivered', ticket_id)

    def _execute(self, sid, ticket):
        response, done = {}, threading.Event()
        cursor = self.store.event_page(sid, limit=1, tail=True)['cursor']

        def dispatch():
            try:
                response['value'] = self._dispatch({'cmd': 'chat_turn', 'session_id': sid, 'request': ticket['request'],
                    'agent': ticket['agent'], 'mode': ticket['mode'], 'online': not ticket['offline'],
                    'queue_ticket': ticket['id']})
            except Exception as exc:  # execution failures remain visible and never retry implicitly
                response['value'] = {'ok': False, 'error': str(exc)}
            finally:
                done.set()

        threading.Thread(target=dispatch, name=f'scizhi-queued-turn-{sid}', daemon=True).start()
        accepted = False
        while True:
            try:
                page = self.store.event_page(sid, cursor=cursor, limit=200)
                cursor = page['cursor']
                if any(e.get('type') == 'user_message' and e.get('payload', {}).get('queue_ticket') == ticket['id']
                       for e in page['events']):
                    accepted = True
                    self._mark_delivery(sid, ticket['id'])
            except (OSError, ValueError):
                pass
            if done.is_set():
                break
            time.sleep(self._interval)
        # The return may race the last event page. Recheck the durable journal
        # once at settlement; raw reads remain the recovery source of truth.
        value = response.get('value') or {'ok': False, 'error': '队列执行未返回结果'}
        if value.get('queue_code') in {'queue_busy', 'queue_cancelled'} and not accepted:
            with session_lock(self._path(sid)):
                state = self._load(sid)
                saved = next(item for item in state['items'] if item['id'] == ticket['id'])
                saved.update(state='queued', updated_at=_now())
                state.update(worker_state='waiting', waiting_for='turn_lock')
                if value['queue_code'] == 'queue_cancelled':
                    self._pause(state, value.get('error') or '派发已取消，请核查后恢复')
                self._save(state, 'dispatch_deferred', ticket['id'])
            return True
        if not accepted:
            accepted = any(e.get('type') == 'user_message' and e.get('payload', {}).get('queue_ticket') == ticket['id']
                           for e in self.store.read(sid))
        with session_lock(self._path(sid)):
            state = self._load(sid)
            saved = next(item for item in state['items'] if item['id'] == ticket['id'])
            saved.update(state='delivered' if accepted else 'needs_review', updated_at=_now())
            outcome = (value.get('result') or {}).get('status')
            if not accepted or not value.get('ok') or outcome in _STOPPED:
                saved['error'] = str(value.get('error') or ('会话已停止，请检查执行记录' if accepted else '未确认消息已被会话接受，请核查后放弃此项'))
                self._pause(state, saved['error'])
            self._save(state, 'dispatch_settled', ticket['id'])
        return False

    def _control_stop(self, sid, state):
        generation = state.get('control_generation', 0)
        cached = self._control_cursors.get(sid)
        cursor = cached[1] if cached and cached[0] == generation else state.get('control_cursor')
        for _ in range(4):
            page = self.store.event_page(sid, cursor=cursor, limit=200, tail=True)
            cursor = page['cursor']
            self._control_cursors[sid] = (generation, cursor)
            stopped = page['reset'] or any(e.get('type') in {'cancel_requested', 'permission_denied', 'error'}
                or e.get('type') == 'completed' and e.get('payload', {}).get('status') in _STOPPED for e in page['events'])
            if stopped:
                self._pause(state, '检测到取消、失败或历史回滚，后续队列已暂停')
                self._save(state, 'control_boundary')
                return 'stop'
            if not page['has_more']:
                return 'ready'
        return 'catch_up'

    def _run(self, sid):
        lease = None
        failed = False
        try:
            while not self._shutdown.is_set() and _enabled():
                lease = _Lease.take(self._run_path(sid))
                if lease:
                    break
                self._wake.wait(self._interval)
                self._wake.clear()
            if lease is None:
                return
            while not self._shutdown.is_set():
                with session_lock(self._path(sid)):
                    state = self._load(sid)
                    if state.get('owner_id') != self.owner_id or state['paused'] or state['resume_required']:
                        break
                    if not _enabled():
                        self._pause(state, '实验功能已关闭，队列记录已保留')
                        self._save(state, 'feature_disabled')
                        break
                    control = self._control_stop(sid, state)
                    if control == 'stop':
                        break
                    status = self.store.snapshot(sid)['status']
                    if status in _STOPPED:
                        self._pause(state, f'会话状态为 {status}，后续队列已暂停')
                        self._save(state, 'session_stopped')
                        break
                    pending = next((item for item in state['items'] if item['state'] == 'queued'), None)
                    if pending is None:
                        state.update(worker_state='idle', waiting_for='')
                        self._save(state, 'queue_idle')
                        break
                    if status not in _READY or control == 'catch_up':
                        status = 'control_events' if control == 'catch_up' else status
                        if state.get('waiting_for') != status or state.get('worker_state') != 'waiting':
                            state.update(worker_state='waiting', waiting_for=status)
                            self._save(state, 'waiting')
                        ticket = None
                    else:
                        pending.update(state='dispatching', updated_at=_now())
                        pending['claim_cancel_epoch'] = state.get('cancel_epoch', 0)
                        state.update(worker_state='dispatching', waiting_for='')
                        self._save(state, 'claimed', pending['id'])
                        ticket = copy.deepcopy(pending)
                if ticket:
                    if self._execute(sid, ticket):
                        self._wake.wait(self._interval)
                        self._wake.clear()
                else:
                    self._wake.wait(self._interval)
                    self._wake.clear()
        except Exception as exc:
            failed = True
            try:
                with session_lock(self._path(sid)):
                    state = self._load(sid)
                    for item in state['items']:
                        if item['state'] == 'dispatching':
                            item.update(state='needs_review', updated_at=_now(), error=redact(str(exc)))
                    self._pause(state, f'队列执行中断：{redact(str(exc))}')
                    state['worker_state'] = 'stopped'
                    self._save(state, 'worker_error')
            except (OSError, ValueError, TypeError, KeyError):
                pass  # Retain the original bytes; stale dispatch is reviewed on the next readable access.
        finally:
            if lease:
                lease.close()
            with self._guard:
                if self._workers.get(sid) is threading.current_thread():
                    self._workers.pop(sid, None)
                if self._shutdown.is_set() and not self._workers:
                    self._owner.close()
            # Remove the old worker from the registry before checking again.
            # An add/resume that saw this worker still alive is handed off here;
            # a later add sees no worker and starts its own watcher.
            if not failed and not self._shutdown.is_set() and _enabled():
                try:
                    with session_lock(self._path(sid)):
                        state = self._load(sid)
                        restart = (state.get('owner_id') == self.owner_id and not state['paused']
                                   and not state['resume_required']
                                   and any(item['state'] == 'queued' for item in state['items']))
                    if restart:
                        self._start(sid)
                except (OSError, ValueError, TypeError, KeyError):
                    pass

    def close(self, timeout=1):
        self._shutdown.set()
        self._wake.set()
        for sid in list(self._owned_sessions):
            try:
                with session_lock(self._path(sid)):
                    state = self._load(sid)
                    if state.get('owner_id') == self.owner_id:
                        self._pause(state, '队列服务已停止，请重新打开后显式恢复')
                        state['worker_state'] = 'stopped'
                        self._save(state, 'service_stopped')
            except (OSError, ValueError, TypeError, KeyError):
                # A damaged optional queue must not prevent process shutdown
                # or release of other owned sessions. Keep its bytes intact.
                pass
        deadline = time.monotonic() + timeout
        with self._guard:
            workers = list(self._workers.values())
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(max(0, deadline - time.monotonic()))
        with self._guard:
            if not self._workers:
                self._owner.close()


def handle(req):
    global _MANAGER
    with _MANAGER_GUARD:
        root = config.DATA_DIR / 'sessions'
        if _MANAGER is None or _MANAGER.root != root or _MANAGER._shutdown.is_set():
            _MANAGER = SessionQueue(config.DATA_DIR)
    return _MANAGER.handle(req)


def close():
    global _MANAGER
    with _MANAGER_GUARD:
        manager, _MANAGER = _MANAGER, None
    if manager is not None:
        manager.close()
