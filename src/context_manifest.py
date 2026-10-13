"""Session-bound context provenance. Public records contain metadata only."""
from __future__ import annotations

import re

MAX_SOURCES = 128


def session_scope(store, session_id: str) -> dict:
    """Resolve a journal-owned project, including durable sidechain ancestry.

    No caller-supplied project selector is accepted. A missing registered
    project fails closed rather than falling back to application preferences.
    """
    sid = session_id
    visited = set()
    while sid and sid not in visited:
        visited.add(sid)
        snapshot = store.snapshot(sid)
        project_id = snapshot.get('project_id')
        if project_id:
            from .project_workspace import resolve_path
            return {'project_id': project_id, 'project_root': resolve_path(project_id)}
        store._path(sid)  # also restores persisted sidechain identity
        sid = store._sidechain_parents.get(sid) or snapshot.get('parent_id')
    return {'project_id': '', 'project_root': None}


def public_manifest(value):
    """Whitelist and bound saved metadata without rereading any context source."""
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        return None
    from .workbench import redact

    def text(item, limit=200):
        return redact(item[:limit]) if isinstance(item, str) else ''

    def count(item):
        return item if type(item) is int and 0 <= item <= 10**9 else None

    result = {'schema_version': 1}
    for key in ('status', 'basis', 'captured_at', 'workspace_mode', 'project_id',
                'agent', 'stage', 'prompt_version'):
        if key in value:
            result[key] = text(value[key])
    budget = value.get('budget')
    if isinstance(budget, dict):
        result['budget'] = {'unit': 'characters'}
        for key in ('limit_chars', 'used_chars', 'notice_chars'):
            result['budget'][key] = count(budget.get(key))
    sources = value.get('sources')
    result['sources'] = []
    if isinstance(sources, list):
        for source in sources[:MAX_SOURCES]:
            if not isinstance(source, dict):
                continue
            row = {}
            for key in ('kind', 'name', 'scope', 'status', 'reason', 'id', 'updated_at'):
                if key in source:
                    row[key] = text(source[key])
            if 'path' in source:
                row['path'] = text(source['path'], 800)
            for key in ('used_chars', 'total_chars', 'cursor', 'next_cursor'):
                if key in source:
                    row[key] = count(source[key])
            if type(source.get('truncated')) is bool:
                row['truncated'] = source['truncated']
            digest = source.get('sha256')
            if isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest):
                row['sha256'] = digest
            result['sources'].append(row)
        result['omitted_sources'] = max(0, len(sources) - MAX_SOURCES) + (count(value.get('omitted_sources')) or 0)
    notices = value.get('notices')
    result['notices'] = [text(n, 400) for n in notices[:12] if isinstance(n, str)] if isinstance(notices, list) else []
    return result


def assemble_session_system(store, sid, title, agent, workspace_mode):
    """Build one prefix and its snapshot together; never infer a past assembly."""
    from . import prompts
    scope = session_scope(store, sid)
    assembled = prompts.agent_system(title, '工具执行由权限策略决定，不得绕过审批。',
        workspace_mode=workspace_mode, agent_role=agent, return_manifest=True, **scope)
    # Extensions and test doubles may still implement the historical str API.
    if not isinstance(assembled, tuple):
        return assembled, None
    core, manifest = assembled
    return core, public_manifest(manifest)


def record_skill_read(state, result):
    """Record the bounded page actually returned to this model conversation."""
    manifest = state.get('context_manifest')
    if not isinstance(manifest, dict) or result.get('ok') is not True or not isinstance(result.get('body'), str):
        return
    cursor, total = result.get('cursor'), result.get('total_chars')
    if type(cursor) is not int or type(total) is not int:
        return
    row = {'kind': 'skill_body', 'name': result.get('name', ''), 'scope': 'application',
           'path': result.get('source', ''), 'sha256': result.get('sha256'),
           'cursor': cursor, 'next_cursor': result.get('next_cursor'),
           'used_chars': len(result['body']), 'total_chars': total,
           'status': 'loaded' if cursor == 0 and not result.get('truncated') else 'partial',
           'reason': 'skills.load'}
    rows = manifest.setdefault('sources', [])
    if row not in rows:
        rows.append(row)
    state['context_manifest'] = public_manifest(manifest)
