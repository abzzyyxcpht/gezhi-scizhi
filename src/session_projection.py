"""Incremental display projection of complete session journal records.

The reducer preserves the historical full-log semantics, including resolved
call IDs surviving user-message boundaries. It stores no transcript and is
never a source of permission or recovery decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .session_events import _decode_cursor, read_event_page


@dataclass
class SessionProjection:
    state: dict[str, Any]
    resolved: set[str] = field(default_factory=set)
    terminal: bool = False
    events: int = 0
    last_type: str = ''
    updated_at: str = ''
    cursor: str | None = None
    signature: tuple[int, int, int] | None = None

    @classmethod
    def empty(cls, session_id: str) -> SessionProjection:
        return cls({'session_id': session_id, 'status': 'unknown', 'request': '',
                    'agent': 'research', 'mode': '', 'plan': None, 'pending': None})

    def fork(self) -> SessionProjection:
        # Nested plans/pending arguments are only replaced, never mutated by
        # the reducer. A new top-level state keeps already-published views stable.
        return SessionProjection(dict(self.state), set(self.resolved), self.terminal,
                                 self.events, self.last_type, self.updated_at,
                                 self.cursor, self.signature)

    def apply(self, event: dict[str, Any]) -> None:
        state = self.state
        kind, payload = event['type'], event.get('payload') or {}
        if kind == 'session_created':
            state.update(status='draft', project_id=payload.get('project_id', ''),
                         parent_id=payload.get('parent_id', ''),
                         agent=payload.get('agent') or state['agent'])
            if payload.get('workspace_mode'):
                state['workspace_mode'] = payload['workspace_mode']
            if payload.get('mode'):
                state['mode'] = payload['mode']
        elif kind == 'user_message':
            state.update(request=str(payload.get('text') or ''),
                         agent=str(payload.get('agent') or state['agent']),
                         mode=str(payload.get('mode') or ''),
                         plan=None, pending=None, content='')
            state['status'] = 'planned' if state['mode'] == 'plan' else 'running'
            if payload.get('workspace_mode'):
                state['workspace_mode'] = payload['workspace_mode']
            self.terminal = False
        elif kind == 'agent_selected':
            state['agent'] = str(payload.get('agent') or state['agent'])
            # Workspace purpose is persisted independently from the selected
            # agent role.  A specialist agent may run inside a general
            # workspace and switching the role must not rewrite the user's
            # workspace selection. Legacy journals retain their original shape;
            # callers derive a display default when the field was never saved.
        elif kind == 'plan_created':
            plan = payload.get('plan') or {}
            state['plan'] = plan
            state['request'] = state['request'] or str(plan.get('request') or '')
            state['mode'] = state['mode'] or str(plan.get('mode') or '')
            if not self.terminal:
                state['status'] = 'planned' if state['mode'] == 'plan' else 'running'
        elif kind == 'permission_requested':
            call_id = str(payload.get('tool_call_id') or '')
            if call_id and call_id not in self.resolved and not self.terminal:
                pending = {'id': call_id, 'tool': str(payload.get('tool') or ''),
                           'capability': str(payload.get('capability') or ''),
                           'arguments': dict(payload.get('arguments') or {})}
                action = payload.get('action')
                if isinstance(action, dict):
                    # 三档门动作预览随日志恢复：跨进程审批不丢目标/影响/可逆性。
                    pending['action'] = dict(action)
                state['pending'] = pending
                state['status'] = 'waiting_approval'
        elif kind in {'permission_granted', 'permission_denied', 'tool_call', 'tool_result', 'tool_skipped'}:
            call_id = str(payload.get('tool_call_id') or payload.get('id') or '')
            self.resolved.add(call_id)
            if state['pending'] and state['pending']['id'] == call_id:
                state['pending'] = None
            if kind == 'permission_denied':
                state.update(status='blocked', pending=None)
                self.terminal = True
            elif kind == 'tool_result' and not payload.get('recoverable') and (payload.get('result') or {}).get('ok') is False:
                state.update(status='error', pending=None)
                self.terminal = True
            elif not self.terminal and not state['pending']:
                state['status'] = 'running'
        elif kind == 'driver_paused' and not self.terminal:
            state['status'] = str(payload.get('status') or 'paused')
            state['task_id'] = payload.get('task_id')
        elif kind == 'driver_started':
            state['driver'] = 'adaptive'
        elif kind == 'turn_started':
            state['turn_id'] = payload.get('turn_id')
        elif kind == 'steer_applied' and not self.terminal:
            state['status'] = 'running'
        elif kind == 'assistant_message':
            state['content'] = str(payload.get('content') or '')
        elif kind == 'cancel_requested' and not self.terminal:
            state['status'] = 'cancelling'
        elif kind in {'error', 'completed'}:
            state['status'] = 'error' if kind == 'error' else str(payload.get('status') or 'done')
            state['pending'] = None
            self.terminal = True
        self.events += 1
        self.last_type = kind
        self.updated_at = event.get('timestamp', '')

    def view(self) -> dict[str, Any]:
        return {'state': self.state, 'exists': self.signature is not None,
                'events': self.events, 'last_type': self.last_type, 'updated_at': self.updated_at}


def project_events(session_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compatibility entry point for callers projecting an explicit full log."""
    projection = SessionProjection.empty(session_id)
    for event in events:
        projection.apply(event)
    return projection.state


def read_projection(path: Path, session_id: str,
                    previous: SessionProjection | None = None) -> SessionProjection:
    """Read only committed additions and publish a separate candidate view."""
    cursor = previous.cursor if previous else None
    projection = None
    while True:
        page = read_event_page(path, session_id, cursor=cursor, limit=500)
        if page['reset'] or projection is None and previous is None:
            projection = SessionProjection.empty(session_id)
        elif projection is None:
            projection = previous.fork()
        for event in page['events']:
            projection.apply(event)
        projection.cursor = page['cursor']
        position = _decode_cursor(projection.cursor)
        signature = position.signature
        projection.signature = (signature[3], signature[2], signature[1]) if signature[0] != -1 else None
        if not page['has_more']:
            return projection
        if cursor == page['cursor']:
            raise OSError('会话投影游标未前进，请重试读取')
        cursor = page['cursor']
