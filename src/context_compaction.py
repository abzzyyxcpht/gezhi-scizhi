"""Preserve user instructions while compacting model and tool observations.

Only messages explicitly marked by this module are internal summaries.  A plain
``role=user`` message is always protected as user content, including the
steering inbox and old sessions written before this marker existed.
"""
from __future__ import annotations

import copy
from typing import Any


SUMMARY_KEY = "_context_compaction"
SUMMARY_VERSION = 1
SUMMARY_MAX_CHARS = 2000
SUMMARY_SEPARATOR = "\n\n"


def is_internal_summary(message: Any) -> bool:
    """Return true only for a summary written by this module."""
    if not isinstance(message, dict):
        return False
    marker = message.get(SUMMARY_KEY)
    return (isinstance(marker, dict) and marker.get("kind") == "summary"
            and type(marker.get("version")) is int
            and marker.get("version") == SUMMARY_VERSION)


def preserved_user_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy every user-authored message, retaining exact order and fields.

    Legacy summaries have no marker and are deliberately retained as user
    content.  Guessing that prose is an internal summary would permit a user
    constraint to disappear on a later compaction.
    """
    return [copy.deepcopy(message) for message in messages
            if message.get("role") == "user" and not is_internal_summary(message)]


def summary_message(content: str) -> dict[str, Any]:
    """Build a user-role summary with a private marker.

    Provider adapters already whitelist wire fields, so the marker remains
    local state and is never sent as a provider message property.
    """
    return {
        "role": "user",
        "content": str(content or ""),
        SUMMARY_KEY: {
            "version": SUMMARY_VERSION,
            "kind": "summary",
        },
    }


def prior_summary_text(messages: list[dict[str, Any]]) -> str:
    """Flatten marked summaries in order without changing any prior body.

    No wrapper is added around the cumulative text, so repeated compaction does
    not recursively nest summary headings. Unmarked user prose is never read as
    a summary, even when it looks exactly like one.
    """
    return SUMMARY_SEPARATOR.join(str(message.get("content") or "") for message in messages
                                  if is_internal_summary(message) and message.get("content"))


def summary_room(messages: list[dict[str, Any]], max_chars: int = SUMMARY_MAX_CHARS) -> int:
    """Space for new observations after carrying the full prior summaries."""
    prior = prior_summary_text(messages)
    return max(0, max_chars - len(prior) - (len(SUMMARY_SEPARATOR) if prior else 0))


def compact_messages(
    messages: list[dict[str, Any]], boundary: int, summary: str, *,
    max_summary_chars: int = SUMMARY_MAX_CHARS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]] | None:
    """Replace non-user history before ``boundary`` while preserving user turns.

    ``boundary`` must point at a safe retained tail (normally an assistant tool
    call).  The archived middle is returned byte-for-byte by deep copy for the
    durable private archive.  The returned candidate always starts with the
    original system and first user messages and ends with the untouched tail.

    All prior marked summary bodies are copied verbatim into one cumulative
    summary. Its body, plus any untouched tail summaries, must fit the total
    character limit. If not, return None; never silently cut old facts or rely
    on a model to reproduce them. Callers leave the original messages active.
    """
    if not isinstance(messages, list) or not 2 < boundary <= len(messages):
        raise ValueError("压缩边界无效")
    middle = messages[2:boundary]
    tail = messages[boundary:]
    prior = prior_summary_text(middle)
    combined = SUMMARY_SEPARATOR.join(part for part in (prior, str(summary or "")) if part)
    tail_summary_chars = sum(len(str(message.get("content") or "")) for message in tail
                             if is_internal_summary(message))
    if len(combined) + tail_summary_chars > max_summary_chars:
        return None
    users = preserved_user_messages(middle)
    marker = summary_message(combined)
    candidate = [copy.deepcopy(message) for message in messages[:2]]
    candidate.append(marker)
    candidate.extend(users)
    candidate.extend(copy.deepcopy(tail))
    return candidate, copy.deepcopy(middle), {
        "archived_count": len(middle), "preserved_user_count": len(users),
    }
