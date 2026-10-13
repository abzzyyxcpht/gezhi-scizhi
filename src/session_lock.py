"""Per-session effect locks shared by local threads and processes."""
from __future__ import annotations

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from weakref import WeakValueDictionary


_registry_guard = threading.Lock()
_thread_locks: WeakValueDictionary = WeakValueDictionary()


@contextmanager
def _session_file_lock(session_log_path: Path | str, suffix: str) -> Iterator[None]:
    """Acquire a durable per-session kernel lock for the requested effect lane."""
    log_path = Path(session_log_path).resolve()
    lock_path = log_path.with_name(log_path.name + suffix)
    key = os.path.normcase(str(lock_path))
    with _registry_guard:
        local_lock = _thread_locks.get(key)
        if local_lock is None:
            local_lock = threading.Lock()
            _thread_locks[key] = local_lock
    with local_lock:
        with lock_path.open("a+b", buffering=0) as stream:
            if os.name == "nt":
                import msvcrt

                # Windows can lock a region beyond EOF. Writing a marker first
                # races a peer which has already locked the newly created file.
                stream.seek(0)
                # LK_LOCK gives up after ten seconds; tool calls can be longer.
                # Retry only contention errors, letting real I/O errors surface.
                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            raise
                        time.sleep(0.025)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def session_lock(session_log_path: Path | str) -> Iterator[None]:
    """Hold one session's lock until approval, tool execution and logging finish.

    A retained lock file identifies the kernel lock; it is deliberately never
    unlinked, since waiters may still hold the old file open. Kernel locks are
    released on process exit, so a crashed owner cannot leave a stale lock.
    """
    with _session_file_lock(session_log_path, ".approval.lock"):
        yield


@contextmanager
def session_terminal_lock(session_log_path: Path | str) -> Iterator[None]:
    """Serialize cancellation requests with terminal completion publication.

    The execution lock intentionally stays available to the control lane while
    a model or tool is running. This short effect lock closes the last race at
    the terminal boundary without blocking cancellation on a long tool call.
    """
    with _session_file_lock(session_log_path, ".terminal.lock"):
        yield
