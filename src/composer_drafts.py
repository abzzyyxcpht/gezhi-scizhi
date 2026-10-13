"""Local unsent composer drafts with atomic, cross-process revision checks."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .session_lock import session_lock
from .state_store import write_json


MAX_TEXT_LENGTH = 32_000
_SCOPE = re.compile(r"(?:session:[A-Za-z0-9_-]{1,128}|project:[A-Za-z0-9_-]{0,128})\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_FIELDS = {"schema", "scope", "revision", "text", "agent", "mode", "updated_at"}


class DraftError(ValueError):
    def __init__(self, message: str, code: str = "invalid_data"):
        super().__init__(message)
        self.code = code


class DraftConflict(DraftError):
    """A stale write was rejected; ``current`` is the saved disk snapshot."""

    def __init__(self, current: dict[str, Any]):
        super().__init__("草稿已在另一窗口更新，请重新载入后重试。", "revision_conflict")
        self.current = dict(current)


def _blank(scope: str) -> dict[str, Any]:
    return {"schema": 1, "scope": scope, "revision": "", "text": "",
            "agent": "research", "mode": "agent", "updated_at": ""}


def _validate_payload(text: str, agent: str, mode: str) -> None:
    if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
        raise DraftError(f"草稿文本必须为不超过 {MAX_TEXT_LENGTH:,} 个字符的字符串；原文未截断。")
    if not isinstance(agent, str) or not _IDENTIFIER.fullmatch(agent):
        raise DraftError("草稿 agent 必须为 1 至 128 位字母、数字、下划线或连字符。")
    if not isinstance(mode, str) or mode not in {"agent", "plan", "review"}:
        raise DraftError("草稿 mode 必须为 agent、plan 或 review。")


class DraftStore:
    """Persist each scope independently; ``root`` is the actual draft directory.

    Callers must pass the revision from ``load`` or the previous successful write.
    Clearing persists a blank row, so stale windows cannot recreate cleared text.
    Drafts never expire automatically and are not execution/session-log events.
    """

    def __init__(self, root: Path | str | None = None):
        if root is None:
            from . import config

            root = Path(config.DATA_DIR) / "composer_drafts"
        self.root = Path(root)

    def _path(self, scope: str) -> Path:
        if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
            raise DraftError("草稿 scope 必须为 session:<安全 ID> 或 project:<安全 ID 或空值>。")
        return self.root / (hashlib.sha256(scope.encode("utf-8")).hexdigest() + ".json")

    @staticmethod
    def _read(path: Path, scope: str) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _blank(scope)
        except UnicodeError:
            raise DraftError("草稿文件编码损坏，已保留原文件。", "corrupt_store") from None
        try:
            row = json.loads(raw)
            if (not isinstance(row, dict) or set(row) != _FIELDS
                    or type(row["schema"]) is not int or row["schema"] != 1
                    or row["scope"] != scope
                    or not isinstance(row["revision"], str) or not row["revision"]):
                raise ValueError
            _validate_payload(row["text"], row["agent"], row["mode"])
            if not isinstance(row["updated_at"], str):
                raise ValueError
            stamp = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError
            return row
        except (ValueError, TypeError, KeyError):
            raise DraftError("草稿文件损坏或版本不兼容，已保留原文件。", "corrupt_store") from None

    def load(self, scope: str) -> dict[str, Any]:
        path = self._path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        with session_lock(path):
            return self._read(path, scope)

    def save(self, scope: str, text: str, agent: str = "research", mode: str = "agent",
             *, expected_revision: str) -> dict[str, Any]:
        _validate_payload(text, agent, mode)
        return self._change(scope, text, agent, mode, expected_revision)

    def clear(self, scope: str, *, expected_revision: str) -> dict[str, Any]:
        return self._change(scope, "", None, None, expected_revision)

    def _change(self, scope: str, text: str, agent: str | None, mode: str | None,
                expected_revision: str) -> dict[str, Any]:
        path = self._path(scope)
        if not isinstance(expected_revision, str):
            raise DraftError("expected_revision 必须为已读取的草稿版本字符串。")
        path.parent.mkdir(parents=True, exist_ok=True)
        with session_lock(path):
            current = self._read(path, scope)
            if expected_revision != current["revision"]:
                raise DraftConflict(current)
            clearing = agent is None
            agent = current["agent"] if agent is None else agent
            mode = current["mode"] if mode is None else mode
            # Every explicit clear invalidates old writers, even if already blank.
            if (not clearing and current["revision"] and current["text"] == text
                    and current["agent"] == agent and current["mode"] == mode):
                return current
            row = {"schema": 1, "scope": scope, "revision": uuid.uuid4().hex,
                   "text": text, "agent": agent, "mode": mode,
                   "updated_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")}
            write_json(path, row)
            return row
