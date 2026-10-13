"""Explicit session scope and metadata receipts for internal research requests.

Bindings are private JSON values passed by the runtime or persisted TaskContext;
there is no process-global current project. Receipts contain no prompt bodies and
are separate from the main driver's frozen-prefix manifest.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import uuid

MAX_ENTRIES = 16
_MAX_FILE_BYTES = 8 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def binding_for_session(store, session_id: str) -> dict | None:
    """Derive scope only from the runtime's durable session identity."""
    if not session_id:
        return None  # Standalone legacy research tasks have no owning session.
    if not isinstance(session_id, str) or not _ID.fullmatch(session_id):
        raise ValueError("科研上下文会话身份无效")
    if not store._path(session_id).is_file():
        raise ValueError("科研上下文所属会话不存在")
    from .context_manifest import session_scope
    from .workspace_modes import mode_for_agent, normalize_mode
    scope = session_scope(store, session_id)
    snapshot = store.snapshot(session_id)
    return {"project_id": scope["project_id"],
            "workspace_mode": normalize_mode(snapshot.get("workspace_mode") or mode_for_agent(snapshot.get("agent"))),
            "source_session_id": session_id, "session_root": str(store.root.resolve())}


def _binding(value: dict | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("科研上下文绑定无效")
    project_id, sid, root = (value.get(key) for key in ("project_id", "source_session_id", "session_root"))
    if (not isinstance(project_id, str) or len(project_id) > 200
            or not isinstance(sid, str) or not _ID.fullmatch(sid)
            or not isinstance(root, str) or not Path(root).is_absolute()):
        raise ValueError("科研上下文绑定身份无效")
    from .workspace_modes import normalize_mode
    return {"project_id": project_id, "workspace_mode": normalize_mode(value.get("workspace_mode")),
            "source_session_id": sid, "session_root": root}


def prompt_options(context_scope: dict | None, agent_role: str) -> dict:
    """Resolve the trusted project at request assembly; missing projects fail closed."""
    binding = _binding(context_scope)
    options = {"agent_role": agent_role}
    if binding is not None:
        from .project_workspace import resolve_path
        project_id = binding["project_id"]
        options.update(project_id=project_id, workspace_mode=binding["workspace_mode"],
                       project_root=resolve_path(project_id) if project_id else None)
    return options


def _public_entry(value: dict) -> dict:
    from .context_manifest import public_manifest
    from .workbench import redact
    return {**{key: redact(str(value.get(key) or ""))[:200]
               for key in ("request_id", "node", "prompt_key", "outcome", "observed_at")},
            "context_manifest": public_manifest(value.get("context_manifest"))}


def _record(binding: dict, entry: dict) -> None:
    from .session_lock import session_lock
    from .state_store import write_json
    path = Path(binding["session_root"]) / f"{binding['source_session_id']}.research-contexts.json"
    # This dedicated metadata lane never waits on the parent execution lock.
    with session_lock(path):
        data = {"schema_version": 1, "entries": [], "omitted_entries": 0}
        if path.is_file():
            with path.open("rb") as stream:
                raw = stream.read(_MAX_FILE_BYTES + 1)
            if len(raw) > _MAX_FILE_BYTES:
                raise ValueError("科研上下文记录超过读取上限")
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
                raise ValueError("科研上下文记录格式无效")
        entries = [_public_entry(row) for row in data["entries"] if isinstance(row, dict)]
        entries.append(_public_entry(entry))
        omitted = data.get("omitted_entries")
        omitted = omitted if type(omitted) is int and omitted >= 0 else 0
        omitted += max(0, len(entries) - MAX_ENTRIES)
        write_json(path, {"schema_version": 1, "entries": entries[-MAX_ENTRIES:], "omitted_entries": omitted})


def chat_json(messages, *, node: str, prompt_key: str, tier: str,
              context_scope: dict | None, context_manifest: dict | None):
    """Record only an invoked model operation, after return or failure.

    ``returned`` means the JSON operation returned, including a None result; it
    does not assert successful research. Provider errors and request bodies are
    never copied to the public receipt. An assembled but uncalled prompt has no
    receipt. A process killed during the call may leave no receipt.
    """
    from . import llm
    binding = _binding(context_scope)
    outcome = "error"
    try:
        result = llm.chat_json(messages, node=node, prompt_key=prompt_key, tier=tier)
        outcome = "returned"
        return result
    finally:
        if binding is not None:
            try:
                _record(binding, {"request_id": uuid.uuid4().hex, "node": node, "prompt_key": prompt_key,
                                  "outcome": outcome, "observed_at": datetime.now(timezone.utc).isoformat(),
                                  "context_manifest": context_manifest})
            except Exception as exc:  # Observation failures must never retry a paid request.
                logging.getLogger(__name__).warning("科研上下文记录未保存（%s）；模型调用结果保持不变。",
                                                    type(exc).__name__)
