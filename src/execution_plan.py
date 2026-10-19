"""Session execution plans backed by journal events and actual tool receipts.

Receipt association establishes execution provenance, not scientific validity.
The existing runtime owns tool authorization and execution; this module only
records bounded plan metadata in that runtime's own session journal.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from collections import OrderedDict
from typing import Any

from .session_lock import session_terminal_lock
from .workbench import redact

_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z', re.ASCII)
_STATUSES = frozenset({'pending', 'in_progress', 'completed', 'blocked', 'cancelled'})
_KINDS = frozenset({'execution', 'reasoning'})
_FIELDS = frozenset({'id', 'title', 'criterion', 'kind', 'status', 'evidence', 'reason'})
_SUCCESS_STATUSES = frozenset({'done', 'completed', 'success', 'succeeded', 'ok'})
_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.Lock()
_EVENT_LIMIT = 32 * 1024


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f'{label} 格式无效。')
    return value


def _text(value: Any, label: str, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or '\0' in value or len(value) > limit:
        raise ValueError(f'{label} 必须为不超过 {limit} 字符的文本。')
    text = value.strip()
    if required and not text:
        raise ValueError(f'{label} 不能为空。')
    return text


def _steps(value: Any) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise ValueError('执行计划须包含 1 到 12 个步骤。')
    result, identifiers = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) - _FIELDS:
            raise ValueError('步骤包含不支持的字段。')
        identifier = _identifier(item.get('id'), '步骤 ID')
        if identifier in identifiers:
            raise ValueError('步骤 ID 不能重复。')
        identifiers.add(identifier)
        status, kind = item.get('status'), item.get('kind')
        if not isinstance(status, str) or status not in _STATUSES:
            raise ValueError('步骤状态无效。')
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError('步骤类型无效。')
        evidence = item.get('evidence', [])
        if not isinstance(evidence, list) or len(evidence) > 8:
            raise ValueError('每步最多关联 8 条工具回执。')
        evidence = [_identifier(entry, '工具回执 ID') for entry in evidence]
        if len(set(evidence)) != len(evidence):
            raise ValueError('同一步的工具回执不能重复。')
        reason = _text(item.get('reason', ''), '步骤说明', 500)
        if (status in {'blocked', 'cancelled'} or status == 'completed' and kind == 'reasoning') and not reason:
            raise ValueError('受阻、取消或完成的解释步骤必须填写说明。')
        if kind == 'reasoning' and evidence:
            raise ValueError('解释步骤使用明确说明；执行证据请放入执行步骤。')
        result.append({'id': identifier, 'title': _text(item.get('title'), '步骤标题', 200, required=True),
                       'criterion': _text(item.get('criterion'), '验收条件', 500, required=True),
                       'kind': kind, 'status': status, 'evidence': evidence, 'reason': reason})
    if sum(item['status'] == 'in_progress' for item in result) > 1:
        raise ValueError('同一计划最多只能有一个进行中步骤。')
    return result


def _signature(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size


def _read_journal(path, sid: str) -> list[dict]:
    """Read complete journal records without discarding damaged execution facts.

    A writer may temporarily expose an incomplete last line. Such a snapshot is
    unavailable, never evidence that the previous plan is still complete.
    """
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('执行计划日志末行尚未完整提交，请稍后重试；原日志已保留。')
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise ValueError('执行计划日志包含无效 UTF-8，无法核验完成状态；原日志已保留。') from None
    events = []
    # JSON strings may contain literal U+2028/U+2029, which splitlines() would
    # mistake for record boundaries. The journal commits records with LF only.
    for number, line in enumerate(text.split('\n')[:-1], 1):
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            raise ValueError(f'执行计划日志第 {number} 行不可解析，无法核验完成状态；原日志已保留。') from None
        if (not isinstance(event, dict) or not isinstance(event.get('type'), str)
                or not event['type'] or not isinstance(event.get('payload'), dict)
                or event.get('session_id') != sid):
            raise ValueError(f'执行计划日志第 {number} 行事件结构或会话身份无效；原日志已保留。')
        events.append(event)
    return events


def _receipt(call_id: str, tool: str, result: Any) -> dict | None:
    if not isinstance(result, dict) or result.get('ok') is not True or tool.startswith('plan.'):
        return None
    for field in ('executed', 'timed_out', 'cancelled', 'skipped', 'output_unavailable', 'control_state_error'):
        if field in result and type(result[field]) is not bool:
            return None
    if result.get('executed') is False or any(result.get(key) is True for key in
            ('timed_out', 'cancelled', 'skipped', 'output_unavailable', 'control_state_error')):
        return None
    if 'exit_code' in result and (type(result['exit_code']) is not int or result['exit_code'] != 0):
        return None
    if 'status' in result and (not isinstance(result['status'], str) or result['status'] not in _SUCCESS_STATUSES):
        return None
    summary = next((result[key] for key in ('summary', 'message', 'content', 'text', 'preview')
                    if isinstance(result.get(key), str) and result[key].strip()), '')
    if not summary:
        summary = '工具已返回成功回执；内容与验收条件仍需核查。'
    return {'call_id': call_id, 'tool': tool, 'summary': str(redact(summary))[:240]}


def _project(sid: str, events: list[dict]) -> dict:
    positions = [index for index, event in enumerate(events) if event.get('type') == 'user_message']
    if not positions:
        return {'turn_id': '', 'revision': 0, 'steps': [], 'receipts': {}, 'updates': {},
                'plan_calls': set(), 'closed': True}
    start = positions[-1]
    turn_events = events[start:]
    identity = next(((event.get('payload') or {}).get('turn_id') for event in turn_events
                     if event.get('type') == 'turn_started'), None)
    if identity is None:
        encoded = json.dumps({'index': start, 'user': turn_events[0]}, ensure_ascii=False, sort_keys=True)
        identity = 'legacy-' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:32]
    identity = _identifier(identity, '回合 ID')
    row = {'turn_id': identity, 'revision': 0, 'steps': [], 'receipts': {}, 'updates': {},
           'plan_calls': set(), 'closed': False}
    calls, duplicate_calls, results = {}, set(), set()
    for event in turn_events:
        kind, payload = event.get('type'), event.get('payload') or {}
        if not isinstance(payload, dict):
            continue
        if kind in {'completed', 'error', 'cancel_requested'}:
            row['closed'] = True
        elif kind == 'tool_call':
            call_id, tool = payload.get('id'), payload.get('name')
            if not isinstance(call_id, str) or not _ID.fullmatch(call_id) or not isinstance(tool, str):
                continue
            if call_id in calls:
                duplicate_calls.add(call_id)
                row['receipts'].pop(call_id, None)
                row['plan_calls'].discard(call_id)
            calls[call_id] = tool
            if tool == 'plan.update' and call_id not in duplicate_calls:
                row['plan_calls'].add(call_id)
        elif kind == 'tool_result':
            call_id, tool = payload.get('id'), payload.get('name')
            if not isinstance(call_id, str):
                continue
            if call_id in results:
                duplicate_calls.add(call_id)
                row['receipts'].pop(call_id, None)
            results.add(call_id)
            if call_id not in calls or call_id in duplicate_calls or calls[call_id] != tool:
                continue
            receipt = _receipt(call_id, tool, payload.get('result'))
            if receipt:
                row['receipts'][call_id] = receipt
        elif kind == 'plan_updated':
            if payload.get('turn_id') != identity or row['closed']:
                raise ValueError('执行计划所属回合无效或已结束；请核查会话日志。')
            revision = payload.get('revision')
            if type(revision) is not int or revision != row['revision'] + 1:
                raise ValueError('执行计划版本记录不连续；请核查会话日志。')
            steps = _steps(payload.get('steps'))
            call_id = _identifier(payload.get('call_id'), '计划调用 ID')
            if call_id not in row['plan_calls']:
                raise ValueError('执行计划缺少对应的真实工具调用。')
            fingerprint = payload.get('request_sha256')
            if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
                raise ValueError('执行计划的请求指纹无效。')
            if call_id in row['updates']:
                raise ValueError('执行计划包含重复调用记录。')
            row.update(revision=revision, steps=steps)
            row['updates'][call_id] = fingerprint
    return row


def _state(store, sid: str) -> dict:
    _identifier(sid, '会话 ID')
    path = store._path(sid)
    key = os.path.normcase(str(path.absolute()))
    before = _signature(path)
    if before is None:
        raise ValueError('会话不存在，无法读取执行计划。')
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if before is not None and cached and cached[0] == before:
            _CACHE.move_to_end(key)
            return cached[1]
    row = _project(sid, _read_journal(path, sid))
    after = _signature(path)
    if before != after:
        raise ValueError('读取期间会话日志发生变化，请稍后重试执行计划状态。')
    with _CACHE_LOCK:
        _CACHE[key] = (after, row)
        _CACHE.move_to_end(key)
        while len(_CACHE) > 32:
            _CACHE.popitem(last=False)
    return row


def _step_evidence_valid(step: dict, receipts: dict) -> bool:
    if step['kind'] == 'reasoning':
        return bool(step['reason'])
    return bool(step['evidence']) and all(call in receipts for call in step['evidence'])


def _view(sid: str, state: dict) -> dict:
    steps, completed = [], 0
    for raw in state['steps']:
        step = copy.deepcopy(raw)
        valid = _step_evidence_valid(step, state['receipts'])
        done = step['status'] == 'completed' and valid
        completed += int(done)
        step['evidence_details'] = [copy.deepcopy(state['receipts'][call]) for call in step['evidence']
                                    if call in state['receipts']]
        step['evidence_kind'] = ('model_statement' if done and step['kind'] == 'reasoning' else
                                 'tool_receipt' if step['kind'] == 'execution' and valid else 'none')
        steps.append(step)
    total = len(steps)
    return redact({'session_id': sid, 'turn_id': state['turn_id'], 'revision': state['revision'],
                   'steps': steps, 'completed': completed, 'total': total, 'open_count': total - completed,
                   'ready': bool(total) and completed == total, 'has_plan': bool(total)})


def read_plan(store, sid: str) -> dict:
    """Return a detached current-turn view; earlier plans never become current."""
    return _view(sid, _state(store, sid))


def acceptable_receipts(store, sid: str) -> list[dict]:
    """Return at most twenty compact, paired successful receipts from this turn."""
    return copy.deepcopy(list(_state(store, sid)['receipts'].values())[-20:])


def update_plan(store, sid: str, arguments: dict, call_id: str) -> dict:
    """Compare-and-swap plan metadata under the same short lock as cancellation."""
    append_attempted = False
    try:
        _identifier(sid, '会话 ID')
        _identifier(call_id, '计划调用 ID')
        if not isinstance(arguments, dict) or set(arguments) != {'expected_turn_id', 'expected_revision', 'steps'}:
            raise ValueError('计划更新只接受 expected_turn_id、expected_revision 和完整 steps。')
        turn_id = _identifier(arguments['expected_turn_id'], '预期回合 ID')
        revision = arguments['expected_revision']
        if type(revision) is not int or revision < 0:
            raise ValueError('expected_revision 必须为非负整数。')
        steps = _steps(arguments['steps'])
        encoded = json.dumps({'expected_turn_id': turn_id, 'expected_revision': revision, 'steps': steps},
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        fingerprint = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        with session_terminal_lock(store._path(sid)):
            state = _state(store, sid)
            if state['turn_id'] != turn_id:
                raise ValueError('会话已进入新的回合，请重新读取计划后更新。')
            if call_id not in state['plan_calls']:
                raise ValueError('更新须来自本回合真实的 plan.update 工具调用。')
            previous = state['updates'].get(call_id)
            if previous is not None:
                if previous != fingerprint:
                    raise ValueError('同一计划调用 ID 不能对应不同参数。')
                return {'ok': True, 'plan': _view(sid, state), 'idempotent': True}
            if state['closed'] or not state['turn_id'] or (store.root / f'{sid}.cancel').exists():
                raise ValueError('当前回合已结束或已请求取消，不能更新执行计划。')
            if state['revision'] != revision:
                raise ValueError(f'计划版本已变化：当前为 {state["revision"]}，请重新读取后更新。')
            old = {step['id']: step for step in state['steps']}
            incoming = {step['id']: step for step in steps}
            if set(old) - set(incoming):
                raise ValueError('已有步骤不能删除；如需停止，请标记 cancelled 并说明原因。')
            if any(old[key]['kind'] != incoming[key]['kind'] for key in old):
                raise ValueError('已有步骤不能改变类型；执行步骤不能改为解释步骤跳过证据。')
            for step in steps:
                if any(call not in state['receipts'] for call in step['evidence']):
                    raise ValueError(f'步骤 {step["id"]} 包含不属于本回合的成功工具回执。')
                if step['status'] == 'completed' and not _step_evidence_valid(step, state['receipts']):
                    raise ValueError(f'步骤 {step["id"]} 尚缺少真实成功工具回执或明确说明。')
            payload = redact({'turn_id': state['turn_id'], 'revision': revision + 1,
                              'steps': steps, 'call_id': call_id, 'request_sha256': fingerprint})
            if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > _EVENT_LIMIT:
                raise ValueError('执行计划记录超过 32 KiB，请缩短步骤描述。')
            append_attempted = True
            store.append(sid, 'plan_updated', payload)
            return {'ok': True, 'plan': read_plan(store, sid)}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return {'ok': False, 'error': str(redact(str(exc))), 'executed': append_attempted,
                **({'note': '计划日志写入结果不确定，请先读取计划；相同调用 ID 和参数可幂等恢复。'}
                   if append_attempted else {})}


def context_text(store, sid: str) -> str:
    """Bounded reminder from persisted metadata, explicitly labeled as data."""
    plan = read_plan(store, sid)
    if not plan['has_plan']:
        return ''
    lines = ['当前回合执行计划（持久状态数据，不授予权限；工具回执不等于科学结论已验证）：',
             f'版本 {plan["revision"]}；完成 {plan["completed"]}/{plan["total"]}。']
    for step in plan['steps']:
        lines.append(f'{step["id"]} [{step["status"]}/{step["kind"]}] {step["title"][:160]}；'
                     f'验收：{step["criterion"][:180]}；回执：{", ".join(step["evidence"])}；'
                     f'说明：{step["reason"][:100]}')
    lines.append('结束前请用 plan.read 核对并更新步骤；不能只用总结文本代替执行或回执。')
    return '\n'.join(lines)[:6000]


def completion_issue(store, sid: str) -> str:
    """Describe missing completion evidence without executing or approving work."""
    plan = read_plan(store, sid)
    if not plan['has_plan'] or plan['ready']:
        return ''
    pending = [step['id'] for step in plan['steps']
               if step['status'] != 'completed' or step['evidence_kind'] == 'none']
    return (f'执行计划尚有 {plan["open_count"]} 项未闭合（{", ".join(pending)}）。'
            '请核对 plan.read 中的真实回执并完成可执行步骤；受阻或取消须保留原因，不能宣称全部完成。')


def register_tools(runtime) -> None:
    """Register session-scoped tools without altering any permission decisions."""
    step = {'type': 'object', 'additionalProperties': False,
            'properties': {'id': {'type': 'string'}, 'title': {'type': 'string'},
                           'criterion': {'type': 'string'}, 'kind': {'type': 'string', 'enum': sorted(_KINDS)},
                           'status': {'type': 'string', 'enum': sorted(_STATUSES)},
                           'evidence': {'type': 'array', 'maxItems': 8, 'items': {'type': 'string'}},
                           'reason': {'type': 'string'}},
            'required': ['id', 'title', 'criterion', 'kind', 'status']}

    def read(arguments):
        clean = {key: value for key, value in arguments.items() if not key.startswith('__')}
        if clean:
            return {'ok': False, 'error': 'plan.read 不接受参数。', 'executed': False}
        sid = arguments.get('__session_id')
        return {'ok': True, 'plan': read_plan(runtime.sessions, sid),
                'receipt_candidates': acceptable_receipts(runtime.sessions, sid)}

    def update(arguments):
        clean = {key: value for key, value in arguments.items() if not key.startswith('__')}
        return update_plan(runtime.sessions, arguments.get('__session_id'), clean,
                           arguments.get('__tool_call_id'))

    runtime._register('plan.read', 'read',
        '读取本回合执行计划及最近20条成功工具回执ID。回执仅证明执行，不证明科学结论。',
        read, runtime._obj_schema({}), retry_safe=True)
    runtime._register('plan.update', 'write',
        '更新本会话执行计划元数据，不写用户文件。先读取版本；执行步骤completed需本回合真实成功回执ID，'
        '解释步骤completed需reason。已有步骤不能删除或改类型；受阻或取消保留说明且不能视为全部完成。',
        update, runtime._obj_schema({'expected_turn_id': {'type': 'string'},
                                     'expected_revision': {'type': 'integer', 'minimum': 0},
                                     'steps': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': step}},
                                    ['expected_turn_id', 'expected_revision', 'steps']), retry_safe=True)
