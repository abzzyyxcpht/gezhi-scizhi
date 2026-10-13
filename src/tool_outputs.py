"""Complete, immutable tool text with bounded, session-scoped read access.

Offsets and read limits are UTF-8 byte counts. Search is literal and case
sensitive, and returns non-overlapping matches. References contain no paths.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from .session_lock import session_lock
from .state_store import write_json


if os.name == "nt":
    import ctypes
    import msvcrt

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in
                    ("creation_time", "access_time", "write_time", "change_time")] + [("attributes", ctypes.c_ulong)]

    _get_basic_info = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
    _get_basic_info.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong)
    _get_basic_info.restype = ctypes.c_int


MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_READ_BYTES = 4000
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z", re.ASCII)
_OUTPUT_ID = re.compile(r"out_[0-9a-f]{32}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REFERENCE_KEYS = ("output_id", "tool", "call_id", "kind", "total_bytes", "sha256", "created_at")


class ToolOutputError(ValueError):
    """A malformed request, unsafe path, or damaged archive; never partial success."""

    def __init__(self, message: str, code: str = "corrupt_output") -> None:
        super().__init__(message)
        self.code = code


def _integer(value: int, name: str, minimum: int, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ToolOutputError(f"{name} must be an integer {bounds}", f"invalid_{name}")


def _identifier(value: str, pattern: re.Pattern, name: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ToolOutputError(f"Invalid {name}", f"invalid_{name}")


def _label(value: str, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or "\0" in value:
        raise ToolOutputError(f"{name} must contain 1..{maximum} characters without NUL", f"invalid_{name}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolOutputError(f"Invalid UTF-8 {name}", f"invalid_{name}") from exc


def _identity(call_id: str, kind: str, digest: str) -> str:
    return "out_" + hashlib.sha256(f"{call_id}\0{kind}\0{digest}".encode("utf-8")).hexdigest()[:32]


def _signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _content_signature(stream: BinaryIO) -> tuple:
    signature = _signature(os.fstat(stream.fileno()))
    if os.name == "nt":
        # Python/CRT st_ctime on Windows can be creation time. NT change time
        # invalidates a cached digest even if a writer restores the old mtime.
        basic = _FileBasicInfo()
        if not _get_basic_info(msvcrt.get_osfhandle(stream.fileno()), 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (*signature[:4], basic.change_time)
    return signature


def _safe_path(path: Path, *, directory: bool = False) -> None:
    """Check lexical ancestors before resolving/opening, including NT junctions."""
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ToolOutputError("Tool output paths cannot contain symbolic links or reparse points", "unsafe_path")
        expected_directory = candidate != path or directory
        if expected_directory and not stat.S_ISDIR(info.st_mode):
            raise ToolOutputError("Tool output parent is not a directory", "unsafe_path")
        if not expected_directory and not stat.S_ISREG(info.st_mode):
            raise ToolOutputError("Tool output is not a regular file", "unsafe_path")


def _mkdir(path: Path) -> None:
    _safe_path(path, directory=True)
    # Create one level at a time and check after each creation, before descending.
    for candidate in (*reversed(path.parents), path):
        candidate.mkdir(exist_ok=True)
        _safe_path(candidate, directory=True)


@contextmanager
def _open_binary(path: Path) -> Iterator[BinaryIO]:
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        _safe_path(path)
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ToolOutputError("Tool output is not a regular file", "unsafe_path")
        # Windows CRT fstat can report creation time for st_ctime, while
        # Path.stat reports change time. Compare common fields across APIs.
        if _signature(os.fstat(stream.fileno()))[:4] != _signature(path.stat())[:4]:
            raise ToolOutputError("Tool output changed while opening")
        yield stream


class ToolOutputStore:
    def __init__(self, session_root: Path | str) -> None:
        # Do not resolve symlinks away: the lexical path is checked on every use.
        self.root = Path(os.path.abspath(os.fspath(session_root))) / ".tool-outputs"
        self._verified: OrderedDict[tuple, None] = OrderedDict()
        self._cache_guard = threading.Lock()

    def _directory(self, session_id: str, *, create: bool = False) -> Path:
        _identifier(session_id, _SESSION_ID, "session_id")
        path = self.root / session_id
        if create:
            _mkdir(path)
        else:
            _safe_path(path, directory=True)
        return path

    @contextmanager
    def _lock(self, directory: Path) -> Iterator[None]:
        anchor = directory / ".index"
        _safe_path(anchor.with_name(anchor.name + ".approval.lock"))
        with session_lock(anchor):
            _safe_path(directory, directory=True)
            _safe_path(anchor.with_name(anchor.name + ".approval.lock"))
            yield

    def publish(self, session_id: str, call_id: str, tool: str, text: str, *, kind: str = "json") -> dict:
        _identifier(session_id, _SESSION_ID, "session_id")
        _label(call_id, "call_id", 200)
        _label(tool, "tool", 160)
        if kind not in ("json", "text"):
            raise ToolOutputError("kind must be json or text", "invalid_kind")
        if not isinstance(text, str):
            raise ToolOutputError("Tool output text must be a string", "invalid_text")
        if len(text) > MAX_OUTPUT_BYTES:
            raise ToolOutputError("Tool output exceeds the 16 MiB UTF-8 limit", "output_too_large")
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolOutputError("Tool output is not valid UTF-8", "invalid_text") from exc
        if len(data) > MAX_OUTPUT_BYTES:
            raise ToolOutputError("Tool output exceeds the 16 MiB UTF-8 limit", "output_too_large")
        digest = hashlib.sha256(data).hexdigest()
        output_id = _identity(call_id, kind, digest)
        directory = self._directory(session_id, create=True)
        with self._lock(directory):
            metadata_path = directory / f"{output_id}.json"
            _safe_path(metadata_path)
            if metadata_path.exists():
                with self._verified_output(directory, session_id, output_id) as (ref, _):
                    if (ref["tool"], ref["call_id"], ref["kind"], ref["sha256"]) != (tool, call_id, kind, digest):
                        raise ToolOutputError("Existing output identity conflicts with this publication")
                    return ref
            ref = {"output_id": output_id, "tool": tool, "call_id": call_id, "kind": kind,
                   "total_bytes": len(data), "sha256": digest,
                   "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
            target = directory / f"{output_id}.txt"
            _safe_path(target)
            descriptor, name = tempfile.mkstemp(prefix=f".{output_id}.", suffix=".tmp", dir=directory)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                _safe_path(target)
                for attempt in range(6):
                    try:
                        os.replace(temporary, target)
                        break
                    except OSError as exc:
                        if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 5:
                            raise
                        time.sleep(.01 * 2 ** attempt)
                _safe_path(metadata_path)
                # The metadata is the commit marker. A failed commit leaves only
                # an undiscoverable data orphan, recoverable by an explicit retry.
                write_json(metadata_path, {"schema": 1, "session_id": session_id, **ref})
            finally:
                temporary.unlink(missing_ok=True)
            return ref

    def _metadata(self, directory: Path, session_id: str, output_id: str) -> dict:
        try:
            with _open_binary(directory / f"{output_id}.json") as stream:
                raw = stream.read(8193)
        except FileNotFoundError as exc:
            raise FileNotFoundError("Tool output was not found in this session") from exc
        if len(raw) > 8192:
            raise ToolOutputError("Tool output metadata exceeds its size limit")
        try:
            metadata = json.loads(raw.decode("utf-8"))
            if not isinstance(metadata, dict) or type(metadata.get("schema")) is not int or metadata["schema"] != 1:
                raise ValueError("schema")
            if metadata.get("session_id") != session_id or metadata.get("output_id") != output_id:
                raise ValueError("scope")
            _label(metadata.get("call_id"), "call_id", 200)
            _label(metadata.get("tool"), "tool", 160)
            if metadata.get("kind") not in ("json", "text"):
                raise ValueError("kind")
            _integer(metadata.get("total_bytes"), "total_bytes", 0, MAX_OUTPUT_BYTES)
            _identifier(metadata.get("sha256"), _SHA256, "sha256")
            if _identity(metadata["call_id"], metadata["kind"], metadata["sha256"]) != output_id:
                raise ValueError("identity")
            timestamp = metadata["created_at"]
            if not isinstance(timestamp, str) or len(timestamp) > 40 or datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("timestamp")
            return {key: metadata[key] for key in _REFERENCE_KEYS}
        except (ValueError, TypeError, KeyError) as exc:
            raise ToolOutputError("Tool output metadata is invalid or belongs to another session") from exc

    @contextmanager
    def _verified_output(self, directory: Path, session_id: str, output_id: str) -> Iterator[tuple[dict, BinaryIO]]:
        ref = self._metadata(directory, session_id, output_id)
        path = directory / f"{output_id}.txt"
        try:
            with _open_binary(path) as stream:
                signature = _content_signature(stream)
                if signature[2] != ref["total_bytes"]:
                    raise ToolOutputError("Tool output byte count does not match its metadata")
                key = (str(path), signature, ref["sha256"])
                with self._cache_guard:
                    cached = key in self._verified
                    if cached:
                        self._verified.move_to_end(key)
                if not cached:
                    digest = hashlib.sha256()
                    decoder = codecs.getincrementaldecoder("utf-8")("strict")
                    try:
                        while chunk := stream.read(64 * 1024):
                            digest.update(chunk)
                            decoder.decode(chunk)
                        decoder.decode(b"", final=True)
                    except UnicodeDecodeError as exc:
                        raise ToolOutputError("Tool output contains invalid UTF-8") from exc
                    if digest.hexdigest() != ref["sha256"]:
                        raise ToolOutputError("Tool output SHA-256 does not match its metadata")
                    self._unchanged(path, stream, signature)
                    with self._cache_guard:
                        self._verified[key] = None
                        while len(self._verified) > 32:
                            self._verified.popitem(last=False)
                stream.seek(0)
                yield ref, stream
                self._unchanged(path, stream, signature)
        except FileNotFoundError as exc:
            raise ToolOutputError("Tool output data is missing") from exc

    @staticmethod
    def _unchanged(path: Path, stream: BinaryIO, signature: tuple) -> None:
        _safe_path(path)
        if _content_signature(stream) != signature or _signature(path.stat())[:4] != signature[:4]:
            raise ToolOutputError("Tool output changed while being read")

    @staticmethod
    def _offset(stream: BinaryIO, offset: int, total: int) -> None:
        if offset > total:
            raise ToolOutputError("offset is past the end of the tool output", "invalid_offset")
        stream.seek(offset)
        first = stream.read(1)
        if first and first[0] & 0xC0 == 0x80:
            raise ToolOutputError("offset must point to a UTF-8 character boundary", "invalid_offset")
        stream.seek(offset)

    def list(self, session_id: str, *, offset: int = 0, limit: int = 20) -> dict:
        _integer(offset, "offset", 0)
        _integer(limit, "limit", 1, 20)
        directory = self._directory(session_id)
        if not directory.exists():
            return {"items": [], "total": 0, "next_offset": None}
        items, errors = [], []
        with self._lock(directory):
            for path in sorted(directory.glob("*.json")):
                output_id = path.stem
                try:
                    _identifier(output_id, _OUTPUT_ID, "output_id")
                    with self._verified_output(directory, session_id, output_id) as (ref, _):
                        items.append(ref)
                except (ValueError, OSError) as exc:
                    errors.append({"output_id": output_id if _OUTPUT_ID.fullmatch(output_id) else None,
                                   "code": getattr(exc, "code", "unavailable_output"),
                                   "error": str(exc) if isinstance(exc, ToolOutputError) else "Tool output could not be read"})
        items.sort(key=lambda row: (row["created_at"], row["output_id"]))
        end = offset + limit
        result = {"items": items[offset:end], "total": len(items), "next_offset": end if end < len(items) else None}
        if errors:
            result.update(errors=errors[:limit], errors_total=len(errors))
        return result

    def read(self, session_id: str, output_id: str, *, offset: int = 0, limit: int = MAX_READ_BYTES) -> dict:
        _identifier(output_id, _OUTPUT_ID, "output_id")
        _integer(offset, "offset", 0)
        _integer(limit, "limit", 1, MAX_READ_BYTES)
        directory = self._directory(session_id)
        if not directory.exists():
            raise FileNotFoundError("Tool output was not found in this session")
        with self._lock(directory), self._verified_output(directory, session_id, output_id) as (ref, stream):
            self._offset(stream, offset, ref["total_bytes"])
            chunk = stream.read(limit)
            # An incomplete trailing code point stays for the next page. Strict
            # decoding everywhere else prevents silent replacement/loss of data.
            text = codecs.getincrementaldecoder("utf-8")("strict").decode(chunk, final=False)
            consumed = len(text.encode("utf-8"))
            if chunk and not consumed:
                raise ToolOutputError("limit is smaller than the next UTF-8 character; use at least 4 bytes", "invalid_limit")
            end = offset + consumed
            eof = end == ref["total_bytes"]
            return {**ref, "text": text, "offset": offset, "next_offset": None if eof else end,
                    "eof": eof, "encoding": "utf-8"}

    def search(self, session_id: str, output_id: str, query: str, *, offset: int = 0, limit: int = 10) -> dict:
        _identifier(output_id, _OUTPUT_ID, "output_id")
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ToolOutputError("query must contain 1..200 characters", "invalid_query")
        try:
            needle = query.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolOutputError("query must contain valid UTF-8", "invalid_query") from exc
        _integer(offset, "offset", 0)
        _integer(limit, "limit", 1, 10)
        directory = self._directory(session_id)
        if not directory.exists():
            raise FileNotFoundError("Tool output was not found in this session")
        with self._lock(directory), self._verified_output(directory, session_id, output_id) as (ref, stream):
            self._offset(stream, offset, ref["total_bytes"])
            matches, buffer, base = [], b"", offset
            next_offset = None
            while chunk := stream.read(64 * 1024):
                buffer += chunk
                cursor = 0
                while (found := buffer.find(needle, cursor)) != -1:
                    absolute = base + found
                    if len(matches) == limit:
                        next_offset = matches[-1]["offset"] + len(needle)
                        break
                    matches.append({"offset": absolute})
                    cursor = found + len(needle)
                if next_offset is not None:
                    break
                # Retain enough tail to recognize a match spanning read chunks,
                # but never reconsider bytes inside a previously found match.
                retained_from = max(cursor, len(buffer) - len(needle) + 1)
                base += retained_from
                buffer = buffer[retained_from:]
            for match in matches:
                stream.seek(match["offset"])
                preview = codecs.getincrementaldecoder("utf-8")("strict").decode(stream.read(960), final=False)
                match["preview"] = preview[:240]
            return {**ref, "query": query, "matches": matches, "offset": offset,
                    "next_offset": next_offset, "eof": next_offset is None, "encoding": "utf-8",
                    "case_sensitive": True}
