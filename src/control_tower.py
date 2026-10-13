"""Read-only, redacted operational view shared by every SciZhi client."""
from __future__ import annotations

import copy
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_usage_lock = threading.RLock()
_usage_signature: tuple = ()
_usage_value: dict[str, Any] = {}


def _signature(path: Path) -> tuple:
    try:
        stat = path.stat()
        return str(path.resolve()), stat.st_mtime_ns, stat.st_size, stat.st_ino
    except FileNotFoundError:
        return str(path.resolve()), None


def clear_usage_cache() -> None:
    global _usage_signature, _usage_value
    with _usage_lock:
        _usage_signature, _usage_value = (), {}


def cached_usage() -> dict[str, Any]:
    """Avoid parsing unchanged logs on every control-tower refresh."""
    from . import logging_util
    global _usage_signature, _usage_value
    with _usage_lock:
        signature = (_signature(logging_util._MODEL_LOG), _signature(logging_util._TOOL_LOG))
        if signature != _usage_signature:
            usage = logging_util.usage_summary()
            model, tool = usage['model'], usage['tool']
            _usage_value = {'model_calls': model['calls'], 'total_tokens': model['total_tokens'],
                'tool_calls': tool['calls'], 'failures': model['failures'] + tool['failures'],
                'cache_hit_rate': model.get('cache_hit_rate'),
                'usage_unknown_calls': model.get('usage_unknown_calls', 0),
                'avg_latency_ms': model['avg_latency_ms']}
            # A concurrent append invalidates the next read, even during aggregation.
            _usage_signature = signature
        return copy.deepcopy(_usage_value)


def snapshot(session_store=None) -> dict[str, Any]:
    from . import service, settings
    from .workbench import redact
    if session_store is None:
        from .bridge import _runtime
        session_store = _runtime().sessions
    prefs = settings.load()
    warnings = []
    tasks = [{**row, 'id': row['task_id'], 'title': row.get('topic', '')}
             for row in service.list_tasks()]
    swarms = [{**row, 'id': row['plan_id'], 'title': row.get('request', '')}
              for row in service.list_swarms()]
    swarm_parents = {row.get('session_id') for row in swarms}
    sessions, pending = [], []
    for row in session_store.list():
        sid = row['session_id']
        state = session_store.snapshot(sid)
        sessions.append({**row, 'id': sid, 'title': row.get('summary', ''),
                         'swarm_parent': sid in swarm_parents,
                         'steps': len((state.get('plan') or {}).get('steps') or [])})
        if state.get('pending'):
            pending.append({**state['pending'], 'session_id': sid})
    # Ordinary Agent spawn/fanout sidechains also need an actionable approval
    # inbox; SessionStore.list deliberately excludes them from top-level rows.
    for path in session_store.root.glob('*/children/*.meta.json'):
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
            child = meta.get('child')
            if not isinstance(child, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', child):
                continue
            child_state = session_store.snapshot(child)
            if child_state.get('pending'):
                pending.append({**child_state['pending'], 'session_id': child,
                                'parent_id': meta.get('parent', '')})
        except (OSError, ValueError, TypeError, AttributeError):
            warnings.append('一条子会话记录暂时不可读，请刷新后检查。')
    for swarm in swarms:
        pending.extend(swarm.get('pending') or [])
    # Parent and sidechain approvals may appear in both projections.
    pending = list({(p.get('session_id'), p.get('id')): p for p in pending}.values())
    counts = dict(running=0, waiting=0, done=0, errors=0, planned=0, cancelled=0)
    rows = tasks + [s for s in sessions if not s['swarm_parent']] + swarms
    for row in rows:
        state = row.get('status')
        if state in {'running', 'pending', 'queued', 'cancelling'}:
            counts['running'] += 1
        elif state in {'waiting_approval', 'waiting_human', 'waiting_task', 'waiting_children', 'paused', 'interrupted'}:
            counts['waiting'] += 1
        elif state in {'done', 'completed'}:
            counts['done'] += 1
        elif state in {'error', 'failed', 'partial', 'blocked'}:
            counts['errors'] += 1
        elif state == 'planned':
            counts['planned'] += 1
        elif state == 'cancelled':
            counts['cancelled'] += 1
    usage = cached_usage()
    if usage['usage_unknown_calls']:
        warnings.append('部分模型调用未返回用量；token 总数仅统计已报告部分。')
    return redact({'tasks': tasks, 'sessions': sessions, 'swarms': swarms,
        'pending': pending, 'summary': counts, 'usage': usage, 'warnings': warnings,
        'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'limits': {'max_parallel': prefs.get('subagent_max_parallel', 4),
                   'reasoning_effort': prefs.get('llm_reasoning_effort', 'auto')}})


def format_snapshot(value: dict[str, Any]) -> str:
    from .textfmt import clip
    summary, usage = value['summary'], value['usage']
    lines = ['Tower · 控制塔',
        f"运行 {summary['running']}  待处理 {summary['waiting']}  完成 {summary['done']}  异常 {summary['errors']}",
        f"模型 {usage['model_calls']} 次 / {usage['total_tokens']:,} tokens · 工具 {usage['tool_calls']} 次",
        f"并行上限 {value['limits']['max_parallel']} · 思考强度 {value['limits']['reasoning_effort']}"]
    for kind, label in [('tasks', '任务'), ('sessions', '会话'), ('swarms', 'Swarm')]:
        lines.append(f'\n{label}')
        for row in value[kind][:20]:
            lines.append(f"  {row['id']}  {row.get('status', '')}  {clip(row['title'], 64)}")
        if not value[kind]:
            lines.append('  暂无记录')
    if value['pending']:
        lines.append('\n待审批（逐项批准或拒绝）')
        for row in value['pending']:
            lines.append(f"  {row.get('tool', '')}  /approve {row['session_id']} {row['id']}")
    lines.extend(value['warnings'])
    lines.append('\n/swarm plan <目标> 规划团队 · /swarm start <计划ID> 启动 · /swarm get <计划ID> 进度')
    return '\n'.join(lines)
