"""Persistent background review inbox; only explicit version-checked acceptance writes notes."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config, settings, research_notebook
from .session_lock import session_lock
from .session_queue import _Lease, _held
from .state_store import write_json
from .workbench import redact

FLAG = 'experimental_evidence_reviews'
COMMANDS = {'review_start', 'review_list', 'review_get', 'review_cancel', 'review_resume',
            'review_accept', 'review_dismiss', 'review_events'}
_FIELDS = {'review_start': {'record_id', 'expected_version', 'review_request_id', 'mode'},
           'review_list': {'record_id'}, 'review_events': {'job_id', 'cursor', 'limit', 'tail'},
           **{name: {'job_id'} for name in ('review_get', 'review_cancel', 'review_resume', 'review_accept', 'review_dismiss')}}
_ACTIVE = {'queued', 'running', 'cancelling'}
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_MANAGER = None
_GUARD = threading.Lock()


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('复核任务或请求标识无效')
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _record(identifier):
    result = research_notebook.handle({'cmd': 'notebook_get', 'id': identifier})
    if not result['ok']:
        raise ValueError(result['error'])
    return result['record']


class EvidenceReviews:
    def __init__(self, data_dir=None, *, runner=None):
        self.root = Path(data_dir or config.DATA_DIR) / 'evidence_reviews'
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'sessions').mkdir(exist_ok=True)
        (self.root / 'snapshots').mkdir(exist_ok=True)
        self.owner_id = uuid.uuid4().hex
        self.owner = _Lease.take(self.root / 'owners' / f'{self.owner_id}.lock')
        self.runner = runner
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.workers = {}

    def _path(self, job_id):
        return self.root / f'{_id(job_id)}.json'

    def _load(self, job_id):
        row = json.loads(self._path(job_id).read_text(encoding='utf-8'))
        if (not isinstance(row, dict) or row.get('id') != job_id or row.get('schema_version') != 1
                or row.get('status') not in _ACTIVE | {'interrupted', 'ready', 'error', 'cancelled', 'accepted', 'dismissed'}
                or row.get('mode') not in {'local', 'model'} or type(row.get('revision')) is not int
                or type(row.get('record_version')) is not int or not isinstance(row.get('snapshot_hash'), str)):
            raise ValueError('复核存档格式无效，原文件已保留')
        _id(row.get('record_id'))
        _id(row.get('owner_id'))
        if row.get('session_id') != job_id:
            raise ValueError('复核执行会话身份不匹配')
        return row

    def _input(self, row):
        snapshot = row.get('snapshot')
        if snapshot is None:
            snapshot = json.loads((self.root / 'snapshots' / f'{row["id"]}.json').read_text(encoding='utf-8'))
        if not isinstance(snapshot, dict) or _hash(snapshot) != row['snapshot_hash']:
            raise ValueError('冻结研究材料已损坏或改变，未重新执行')
        return {**copy.deepcopy(row), 'snapshot': snapshot}

    def _save(self, row):
        row['revision'] += 1
        row['updated_at'] = _now()
        write_json(self._path(row['id']), row)

    def _recover(self, job_id):
        with session_lock(self._path(job_id)):
            row = self._load(job_id)
            if row['status'] in _ACTIVE and _held(self.root / 'owners' / f'{_id(row["owner_id"])}.lock') is False:
                row.update(status='interrupted', phase='执行进程已结束，请核对后显式恢复', run_token='')
                self._save(row)
            return row

    def _public(self, row, *, detail=True, records=None, digests=None):
        try:
            current = records[row['record_id']] if records is not None else _record(row['record_id'])
            version = row.get('accepted_version', row['record_version'])
            digest = row.get('accepted_hash', row['snapshot_hash'])
            current_digest = digests[row['record_id']] if digests is not None else _hash(current)
            stale = current['version'] != version or current_digest != digest
        except (ValueError, KeyError):
            stale = True
        view = {key: row.get(key) for key in ('id', 'request_id', 'record_id', 'record_version', 'title', 'mode', 'status',
            'phase', 'created_at', 'updated_at', 'revision', 'error', 'session_id', 'accepted_version')}
        view.update(stale=stale, enabled=settings.load().get(FLAG, False) is True,
                    can_accept=row['status'] == 'ready' and not stale,
                    scientific_verification='not_performed')
        if detail:
            view['result'] = row.get('result')
        return redact(view)

    def _start_thread(self, job_id):
        with self.lock:
            if self.stop.is_set():
                raise ValueError('复核服务已停止，请重新打开后恢复')
            if job_id in self.workers and self.workers[job_id].is_alive():
                return
            worker = threading.Thread(target=self._work, args=(job_id,), name=f'evidence-review-{job_id}', daemon=True)
            self.workers[job_id] = worker
            try:
                worker.start()
            except Exception:
                self.workers.pop(job_id, None)
                with session_lock(self._path(job_id)):
                    row = self._load(job_id)
                    row.update(status='interrupted', phase='后台线程未启动，请显式恢复')
                    self._save(row)
                raise

    def _work(self, job_id):
        lease = slot = None
        token = None
        try:
            while not self.stop.is_set() and lease is None:
                lease = _Lease.take(self.root / f'{job_id}.run.lock')
                if lease is None:
                    self.stop.wait(.05)
            if lease is None:
                return
            while not self.stop.is_set() and slot is None:
                with session_lock(self._path(job_id)):
                    row = self._load(job_id)
                    if row['status'] not in {'queued', 'running'} or row['owner_id'] != self.owner_id:
                        return
                    if settings.load().get(FLAG, False) is not True:
                        row.update(status='interrupted', phase='实验已关闭，未派发任务')
                        self._save(row)
                        return
                for index in range(2):
                    slot = _Lease.take(self.root / f'.slot-{index}.lock')
                    if slot:
                        break
                if slot is None:
                    self.stop.wait(.1)
            if slot is None:
                return
            with session_lock(self._path(job_id)):
                row = self._load(job_id)
                if row['status'] != 'queued' or row['owner_id'] != self.owner_id:
                    return
                token = row['run_token']
                row.update(status='running', phase='读取冻结研究记录')
                self._save(row)
                frozen = self._input(row)

            def cancelled():
                if self.stop.is_set():
                    return True
                with session_lock(self._path(job_id)):
                    current = self._load(job_id)
                    return current['run_token'] != token or current['status'] == 'cancelling'

            def progress(message):
                with session_lock(self._path(job_id)):
                    current = self._load(job_id)
                    if current['run_token'] == token and current['status'] == 'running' and current['phase'] != message:
                        current['phase'] = message
                        self._save(current)

            from .evidence_review_runner import local_review, model_review
            if frozen['mode'] == 'model':
                from .agent_driver import AdaptiveDriver
                if _hash(AdaptiveDriver._identity()) != frozen['model_signature']:
                    raise ValueError('复核模型配置已改变，请核对后创建新任务')
            result = (self.runner(frozen, cancelled, progress) if self.runner else
                      local_review(frozen, cancelled, progress) if frozen['mode'] == 'local' else
                      model_review(frozen, self.root, cancelled, progress))
            if not isinstance(result, dict) or not isinstance(result.get('text'), str):
                raise ValueError('复核器没有返回可检查的报告')
            with session_lock(self._path(job_id)):
                current = self._load(job_id)
                if current['run_token'] != token:
                    return
                interrupted = self.stop.is_set()
                cancelled_now = current['status'] == 'cancelling'
                status = ('cancelled' if cancelled_now or result.get('status') == 'cancelled' else
                          'ready' if result.get('status') == 'done' else
                          'interrupted' if interrupted else
                          'error' if result.get('status') not in {None, 'done'} else 'ready')
                current.update(status=status, result=redact(result),
                    error=result.get('error') or '', phase={'ready': '等待研究者核查，尚未采纳', 'cancelled': '已取消',
                        'interrupted': '应用已关闭，结果需要核查', 'error': '复核未完整完成，请查看执行记录'}[status])
                self._save(current)
        except Exception as error:
            try:
                with session_lock(self._path(job_id)):
                    row = self._load(job_id)
                    if row['status'] in _ACTIVE and (token is None or row['run_token'] == token):
                        row.update(status='cancelled' if row['status'] == 'cancelling' else 'error', error=redact(str(error)), phase='复核已停止')
                        self._save(row)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        finally:
            if slot:
                slot.close()
            if lease:
                lease.close()
            with self.lock:
                self.workers.pop(job_id, None)
                if self.stop.is_set() and not self.workers:
                    self.owner.close()

    def _start(self, req):
        if settings.load().get(FLAG, False) is not True:
            raise ValueError('请先明确开启后台证据复核实验')
        mode = req.get('mode')
        if mode not in {'local', 'model'}:
            raise ValueError('请选择 local 完整性检查或 model 模型复核')
        key = _id(req.get('review_request_id'))
        record_id = _id(req.get('record_id'))
        expected = req.get('expected_version')
        if type(expected) is not int or expected < 1:
            raise ValueError('必须提供记录的当前版本')
        job_id = 'rv_' + hashlib.sha256(key.encode()).hexdigest()[:28]
        signature = {'record_id': record_id, 'version': expected, 'mode': mode}
        with session_lock(self.root / '.registry'):
            if self._path(job_id).exists():
                row = self._recover(job_id)
                if row['request_signature'] != signature:
                    raise ValueError('复核请求标识已用于不同记录、版本或方式')
                return row
            snapshot = _record(record_id)
            if snapshot['version'] != expected:
                raise ValueError('研究记录已变更，请刷新版本后重新创建复核')
            identity = None
            if mode == 'model':
                from . import llm
                from .agent_driver import AdaptiveDriver
                if not llm.available('planning'):
                    raise ValueError('模型尚未配置或已关闭；可明确选择本地完整性检查')
                identity = AdaptiveDriver._identity()
            active = sum(self._recover(path.stem)['status'] in _ACTIVE for path in self.root.glob('rv_*.json'))
            if active >= 20:
                raise ValueError('最多保留20个尚未结束的后台复核任务')
            row = {'schema_version': 1, 'id': job_id, 'request_id': key, 'request_signature': signature,
                'record_id': record_id, 'record_version': expected, 'title': snapshot['title'], 'mode': mode,
                'snapshot_hash': _hash(snapshot),
                'model_signature': _hash(identity) if identity is not None else None, 'model_identity': identity,
                'status': 'queued', 'phase': '等待后台执行槽', 'owner_id': self.owner_id,
                'run_token': uuid.uuid4().hex, 'revision': 0, 'created_at': _now(), 'error': '',
                'result': None, 'session_id': job_id}
            write_json(self.root / 'snapshots' / f'{job_id}.json', snapshot)
            self._save(row)
        self._start_thread(job_id)
        return row

    def _accept(self, job_id):
        with session_lock(self._path(job_id)):
            row = self._load(job_id)
            if row['status'] == 'accepted':
                return row
            if row['status'] != 'ready':
                raise ValueError('只有已完成且尚未采纳的复核可以保存备注')
            result = row['result']
            note = (f'【复核备注 {job_id} · 原记录版本 {row["record_version"]} · {row["mode"]}】\n'
                    '仅保存复核建议，未验证来源真实性或科学支持。\n' + result['text'][:17500])
            if len(result['text']) > 17500:
                note += '\n【备注长度限制；完整报告保留在复核收件箱。】'
            current = _record(row['record_id'])
            if note not in current['observations']:
                if current['version'] != row['record_version'] or _hash(current) != row['snapshot_hash']:
                    raise ValueError('原研究记录已变更，不能采纳旧版本复核；请创建新的复核任务')
                saved = research_notebook.handle({'cmd': 'notebook_save', 'expected_version': current['version'],
                    'record': {'id': row['record_id'], 'observations': [*current['observations'], note]}})
                if not saved['ok']:
                    raise ValueError(saved['error'])
                current = saved['record']
            row.update(status='accepted', phase='已由用户保存为复核备注', accepted_version=current['version'],
                       accepted_hash=_hash(current))
            self._save(row)
            return row

    def handle(self, req):
        try:
            cmd = req.get('cmd')
            if cmd not in COMMANDS:
                raise ValueError('未知复核命令')
            if set(req) - {'cmd'} - _FIELDS[cmd]:
                raise ValueError('复核请求含不支持的字段')
            if cmd == 'review_start':
                return {'ok': True, 'job': self._public(self._start(req))}
            if cmd == 'review_list':
                rows, warnings = [], []
                for path in self.root.glob('rv_*.json'):
                    try:
                        rows.append(self._recover(path.stem))
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        warnings.append({'job_id': path.stem, 'error': redact(str(error))})
                if req.get('record_id') is not None:
                    rows = [row for row in rows if row['record_id'] == _id(req['record_id'])]
                rows.sort(key=lambda row: (row['created_at'], row['id']), reverse=True)
                current = research_notebook.handle({'cmd': 'notebook_list'})
                if not current['ok']:
                    raise ValueError(current['error'])
                records = {record['id']: record for record in current['records']}
                digests = {identifier: _hash(records[identifier]) for identifier in {row['record_id'] for row in rows[:100]} if identifier in records}
                return {'ok': True, 'jobs': [self._public(row, detail=False, records=records, digests=digests) for row in rows[:100]],
                        'total': len(rows), 'truncated': len(rows) > 100, 'warnings': warnings,
                        'enabled': settings.load().get(FLAG, False) is True}
            job_id = _id(req.get('job_id'))
            row = self._recover(job_id)
            if cmd == 'review_accept':
                row = self._accept(job_id)
            elif cmd == 'review_events':
                from .agent_runtime import SessionStore
                return {'ok': True, **redact(SessionStore(self.root / 'sessions').event_page(row['session_id'],
                    cursor=req.get('cursor'), limit=req.get('limit', 100), tail=req.get('tail', False)))}
            elif cmd in {'review_cancel', 'review_dismiss', 'review_resume'}:
                with session_lock(self._path(job_id)):
                    row = self._load(job_id)
                    if cmd == 'review_cancel':
                        if row['status'] in _ACTIVE:
                            row.update(status='cancelled' if row['status'] == 'queued' else 'cancelling', phase='请求停止复核')
                            (self.root / 'sessions' / f'{row["session_id"]}.cancel').touch()
                    elif cmd == 'review_dismiss':
                        if row['status'] in _ACTIVE or row['status'] == 'accepted':
                            raise ValueError('运行中的复核请先取消；已采纳的记录不能从收件箱撤销')
                        row.update(status='dismissed', phase='用户已忽略该复核')
                    else:
                        if settings.load().get(FLAG, False) is not True:
                            raise ValueError('请先开启后台复核实验')
                        if row['status'] != 'interrupted':
                            raise ValueError('只能明确恢复中断的复核；失败或取消请重新创建任务')
                        row.update(status='queued', phase='用户已要求恢复', owner_id=self.owner_id, run_token=uuid.uuid4().hex)
                    self._save(row)
                if cmd == 'review_resume':
                    self._start_thread(job_id)
            return {'ok': True, 'job': self._public(row)}
        except (OSError, ValueError, KeyError, TypeError) as error:
            return {'ok': False, 'error': redact(str(error))}

    def close(self):
        self.stop.set()
        with self.lock:
            workers = list(self.workers.values())
            ids = list(self.workers)
        for job_id in ids:
            try:
                (self.root / 'sessions' / f'{job_id}.cancel').touch()
            except OSError:
                pass
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(timeout=.5)
        with self.lock:
            if not self.workers:
                self.owner.close()


def handle(req):
    global _MANAGER
    with _GUARD:
        root = config.DATA_DIR / 'evidence_reviews'
        if _MANAGER is None or _MANAGER.root != root or _MANAGER.stop.is_set():
            if _MANAGER is not None:
                _MANAGER.close()
            _MANAGER = EvidenceReviews()
    return _MANAGER.handle(req)


def close():
    global _MANAGER
    with _GUARD:
        manager, _MANAGER = _MANAGER, None
    if manager:
        manager.close()
