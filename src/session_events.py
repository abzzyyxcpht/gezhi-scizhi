"""Bounded JSONL event pages for displays; approval/recovery keep raw reads.

Cursors contain file identity and byte boundaries, never filesystem paths.
Journal rewrites reset readers; normal appends preserve their position. Only
newline-terminated records are committed, including when UTF-8 is mid-write.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re


MAX_CURSOR_LENGTH = 2048
MAX_PAGE_EVENTS = 500
_BLOCK_BYTES = 64 * 1024
_ANCHOR_BYTES = 256
_MISSING = (-1, -1, 0, -1)
_EMPTY_HASH = hashlib.sha256(b'').hexdigest()
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')


@dataclass(frozen=True)
class _Cursor:
    sid: str
    signature: tuple[int, int, int, int]
    offset: int
    head_size: int
    head_hash: str
    anchor_hash: str
    drained: bool


def _encode_cursor(value: _Cursor) -> str:
    fields = [1, value.sid, *value.signature, value.offset, value.head_size,
              value.head_hash, value.anchor_hash, int(value.drained)]
    return base64.urlsafe_b64encode('|'.join(map(str, fields)).encode('ascii')).decode('ascii').rstrip('=')


def _decode_cursor(value: str | None) -> _Cursor | None:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_CURSOR_LENGTH:
        raise ValueError('事件游标无效')
    try:
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        fields = raw.decode('ascii').split('|')
        if len(fields) != 11 or fields[0] != '1' or not _ID.fullmatch(fields[1]):
            raise ValueError
        signature = tuple(int(v) for v in fields[2:6])
        offset, head_size = int(fields[6]), int(fields[7])
        if (any(n < -1 or n > 2**127 for n in signature) or signature[2] < 0
                or not 0 <= offset <= signature[2]
                or not 0 <= head_size <= min(signature[2], _ANCHOR_BYTES)
                or not _HASH.fullmatch(fields[8]) or not _HASH.fullmatch(fields[9])
                or fields[10] not in {'0', '1'}):
            raise ValueError
        return _Cursor(fields[1], signature, offset, head_size,
                       fields[8], fields[9], fields[10] == '1')
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise ValueError('事件游标无效') from exc


def _signature(stat) -> tuple[int, int, int, int]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _read_at(stream, start: int, length: int) -> bytes:
    stream.seek(start)
    return stream.read(length)


def _fingerprints(stream, size: int, offset: int) -> tuple[str, str]:
    head = _read_at(stream, 0, size)
    anchor = _read_at(stream, max(0, offset - _ANCHOR_BYTES), min(offset, _ANCHOR_BYTES))
    return hashlib.sha256(head).hexdigest(), hashlib.sha256(anchor).hexdigest()


def _matches(stream, previous: _Cursor, sid: str, signature: tuple) -> bool:
    old = previous.signature
    if (previous.sid != sid or old[:2] != signature[:2] or signature[2] < old[2]
            or (signature[2] == old[2] and signature[3] != old[3])):
        return False
    head_hash, anchor_hash = _fingerprints(stream, previous.head_size, previous.offset)
    if (head_hash, anchor_hash) != (previous.head_hash, previous.anchor_hash):
        return False
    # A forged cursor must not begin halfway through a multibyte JSON record.
    return previous.offset == 0 or _read_at(stream, previous.offset - 1, 1) == b'\n'


def _decode_event(line: bytes) -> dict | None:
    """Separate event decoding from cursor parsing for performance checks."""
    try:
        event = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    return event if isinstance(event, dict) else None


def _read_forward(stream, size: int, offset: int, limit: int) -> tuple[list, int, bool]:
    events = []
    stream.seek(offset)
    position = offset
    while position < size and len(events) < limit:
        line = stream.readline(size - position)
        position += len(line)
        if not line.endswith(b'\n'):
            return events, offset, False
        offset = position
        event = _decode_event(line)
        if event is not None:
            events.append(event)
    # Peek for a complete record, without decoding or consuming its cursor.
    line = stream.readline(size - position) if position < size else b''
    return events, offset, line.endswith(b'\n')


def _complete_end(stream, size: int) -> int:
    end = size
    while end:
        start = max(0, end - _BLOCK_BYTES)
        block = _read_at(stream, start, end - start)
        newline = block.rfind(b'\n')
        if newline >= 0:
            return start + newline + 1
        end = start
    return 0


def _reverse_lines(stream, end: int):
    pending = b''
    trailing = True
    while end:
        start = max(0, end - _BLOCK_BYTES)
        lines = (_read_at(stream, start, end - start) + pending).split(b'\n')
        pending = lines[0]
        for line in reversed(lines[1:]):
            if trailing:
                trailing = False  # The suffix after the final newline is empty.
                continue
            yield line
        end = start
    if pending:
        yield pending


def _read_tail(stream, size: int, limit: int) -> tuple[list, int, bool]:
    offset = _complete_end(stream, size)
    events = []
    for line in _reverse_lines(stream, offset):
        event = _decode_event(line)
        if event is not None:
            events.append(event)
            if len(events) == limit:
                break
    return list(reversed(events)), offset, False


def read_event_page(path: Path, session_id: str, cursor: str | None = None,
                    limit: int = 200, tail: bool = False) -> dict:
    """Return {events, cursor, reset, has_more} from complete JSONL records.

    tail selects the latest limit valid events only on initial/reset reads.
    A valid cursor always continues forwards. Same-file appends are checked at
    the consumed boundary; atomic replacement, shrink and same-size rewrites
    reset the page. Cursor validation never chooses or opens a different path.
    """
    if not isinstance(session_id, str) or not _ID.fullmatch(session_id):
        raise ValueError('会话 ID 无效')
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_EVENTS:
        raise ValueError('事件分页大小须为 1–500')
    if type(tail) is not bool:
        raise ValueError('tail 必须为布尔值')
    previous = _decode_cursor(cursor)
    forced_reset = False
    for _ in range(3):
        try:
            stream = path.open('rb')
        except FileNotFoundError:
            missing = _Cursor(session_id, _MISSING, 0, 0, _EMPTY_HASH, _EMPTY_HASH, True)
            return {'events': [], 'cursor': _encode_cursor(missing), 'has_more': False,
                    'reset': bool(forced_reset or previous and previous != missing)}
        with stream:
            signature = _signature(os.fstat(stream.fileno()))
            valid = previous is not None and not forced_reset and _matches(stream, previous, session_id, signature)
            reset = forced_reset or previous is not None and not valid
            if valid and previous.signature == signature and previous.drained:
                events, offset, has_more = [], previous.offset, False
            elif not valid and tail:
                events, offset, has_more = _read_tail(stream, signature[2], limit)
            else:
                events, offset, has_more = _read_forward(stream, signature[2], previous.offset if valid else 0, limit)
            head_size = min(signature[2], _ANCHOR_BYTES)
            head_hash, anchor_hash = _fingerprints(stream, head_size, offset)
            current = _Cursor(session_id, signature, offset, head_size, head_hash, anchor_hash, not has_more)
        # Release the file before verification so Windows atomic writers are
        # not held up. If its path changed during/after the read, retry on the
        # current journal instead of returning events from the removed one.
        try:
            after = _signature(path.stat())
        except FileNotFoundError:
            forced_reset = True
            continue
        if (after[:2] != signature[:2] or after[2] < signature[2]
                or after[2] == signature[2] and after[3] != signature[3]):
            forced_reset = True
            continue
        return {'events': events, 'cursor': _encode_cursor(current),
                'reset': bool(reset), 'has_more': has_more}
    raise OSError('会话记录正在改写，请重试读取')
