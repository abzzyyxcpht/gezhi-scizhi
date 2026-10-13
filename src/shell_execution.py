"""Drain owned shell pipes with fixed capture budgets and responsive cancellation."""
from __future__ import annotations

import atexit
import codecs
import math
from pathlib import Path
import queue
import re
import tempfile
import threading
import time
import uuid

from .process_owner import spawn_owned
from .workbench import redact

CAPTURE_LIMIT = 16 * 1024 * 1024 - 16384
PREVIEW_BYTES = 8192
CHUNK_BYTES = 32768
LIVE_LINE_CHARS = 4096
_ACTIVE = {}
_ACTIVE_LOCK = threading.Lock()
_DISPLAY_SLOTS = threading.BoundedSemaphore(8)
_ANSI = re.compile(r'\x1b\][\s\S]*?(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')


def cancel_all(*, wait=True, timeout=2.0):
    """Cancel only commands registered by this process, never discover by name."""
    with _ACTIVE_LOCK:
        active = list(_ACTIVE.values())
    for cancel, _ in active:
        cancel.set()
    if wait:
        deadline = time.monotonic() + timeout
        for _, done in active:
            done.wait(max(0, deadline-time.monotonic()))
    return sum(not done.is_set() for _, done in active)


atexit.register(cancel_all)


def _head(text, size):
    return text.encode('utf-8')[:size].decode('utf-8', errors='ignore')


def _tail(text, size):
    return text.encode('utf-8')[-size:].decode('utf-8', errors='ignore')


class _Capture:
    def __init__(self, name, budget, events, spool_dir):
        self.name, self.budget, self.events = name, budget, events
        self.file = tempfile.TemporaryFile(mode='w+b', dir=spool_dir)
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='surrogateescape')
        self.decode_loss = 0
        self.received = self.produced = self.retained = 0
        self.line, self.discard_line, self.live_tail = '', False, ''
        self.display_dropped = 0
        self.error = ''
        self.sealed = False
        self.exhausted = False
        self.lock = threading.Lock()
        self.done = threading.Event()

    def _display(self, text):
        clean = str(redact(_ANSI.sub('', text)))
        clean = re.sub(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]', '', clean)
        if not clean:
            return
        self.live_tail = _tail(self.live_tail + clean, PREVIEW_BYTES)
        try:
            self.events.put_nowait((self.name, clean))
        except queue.Full:
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.display_dropped += 1
            try:
                self.events.put_nowait((self.name, clean))
            except queue.Full:
                self.display_dropped += 1

    def _lines(self, text, final=False):
        # Hold unfinished lines until their credential context is available.
        # Long single lines never accumulate without bound or leak a key suffix.
        for part in re.split(r'([\r\n])', text):
            if part in {'\n', '\r'}:
                if self.line:
                    self._display(self.line + '\n')
                self.line, self.discard_line = '', False
            elif not self.discard_line:
                self.line += part
                if len(self.line) > LIVE_LINE_CHARS:
                    self.line, self.discard_line = '', True
                    self._display('（长行已折叠，命令结束后查看已保存输出）\n')
        if final and self.line:
            self._display(self.line)
            self.line = ''

    def feed(self, data, *, final=False):
        with self.lock:
            if self.sealed:
                return
            self.received += len(data)
            text = self.decoder.decode(data, final=final)
            bad = sum(0xDC80 <= ord(char) <= 0xDCFF for char in text)
            if bad:
                self.decode_loss += bad
                text = ''.join('\ufffd' if 0xDC80 <= ord(char) <= 0xDCFF else char for char in text)
            encoded = text.encode('utf-8')
            self.produced += len(encoded)
            room = 0 if self.exhausted else max(0, self.budget-self.retained)
            prefix = encoded[:room].decode('utf-8', errors='ignore').encode('utf-8')
            if prefix:
                self.file.write(prefix)
                self.retained += len(prefix)
            if len(prefix) < len(encoded):
                self.exhausted = True
            self._lines(text, final=final)

    def drain(self, stream):
        try:
            while True:
                data = stream.read(CHUNK_BYTES)
                if not data:
                    self.feed(b'', final=True)
                    break
                self.feed(data)
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            self.done.set()

    def finish(self):
        with self.lock:
            self.sealed = True
            self.file.seek(0)
            text = str(redact(self.file.read(self.budget).decode('utf-8')))
            self.file.close()
            encoded = text.encode('utf-8')
            redaction_omitted = max(0, len(encoded)-self.budget)
            text = encoded[:self.budget].decode('utf-8', errors='ignore')
            omitted = self.produced-self.retained
            incomplete = bool(omitted or redaction_omitted or self.decode_loss or self.error or not self.done.is_set())
            if incomplete:
                text = ('[输出不完整：已达到捕获预算或管道未完整结束；以下为已保存的前段。]\n' + text
                        + '\n[末尾实时预览，长行可能已折叠]\n' + self.live_tail)
            if len(text.encode('utf-8')) <= PREVIEW_BYTES:
                preview = text
            else:
                preview = _head(text, 3600) + '\n[中间输出省略，查看已保存输出]\n' + _tail(self.live_tail, 3600)
            return {'text': text, 'preview': preview, 'received_bytes': self.received,
                    'decoded_utf8_bytes': self.produced, 'retained_bytes': self.retained,
                    'omitted_utf8_bytes': omitted, 'redaction_omitted_bytes': redaction_omitted,
                    'truncated': incomplete, 'reader_error': self.error,
                    'display_dropped_chunks': self.display_dropped, 'decode_loss_bytes': self.decode_loss}


def run_command(command, cwd, *, timeout=30, cancel_event=None, cancelled=None, on_output=None,
                capture_limit=CAPTURE_LIMIT, spool_dir=None):
    """Return captured streams after stopping descendants and draining both pipes.

    Live callbacks receive redacted complete lines, at most roughly five batches
    per second. They are a bounded preview, never an authoritative full output.
    """
    if type(timeout) not in (float, int) or not 0 < timeout <= 120 or not math.isfinite(timeout):
        raise ValueError('timeout must be >0 and <=120 seconds')
    if type(capture_limit) is not int or not 4 <= capture_limit <= CAPTURE_LIMIT:
        raise ValueError('capture_limit exceeds the bounded output budget')
    local_cancel, completed = threading.Event(), threading.Event()
    identity = uuid.uuid4().hex
    with _ACTIVE_LOCK:
        _ACTIVE[identity] = local_cancel, completed
    events = queue.Queue(maxsize=64)
    deliveries = queue.Queue(maxsize=2)
    delivery_finished = threading.Event()
    captures, owner, threads = {}, None, []
    timed_out = was_cancelled = callback_failed = stopped = False
    tree_empty = True
    failure = ''
    exited = None
    started = time.monotonic()
    last_emit = float('-inf')

    def deliver_loop():
        nonlocal callback_failed
        while not delivery_finished.is_set() or not deliveries.empty():
            try:
                parts = deliveries.get(timeout=.05)
            except queue.Empty:
                continue
            if not callback_failed:
                for channel, text in parts.items():
                    if text:
                        try:
                            on_output(channel, text)
                        except Exception:
                            callback_failed = True
                            break

    def deliver():
        try:
            deliver_loop()
        finally:
            _DISPLAY_SLOTS.release()

    display_acquired = bool(on_output and _DISPLAY_SLOTS.acquire(blocking=False))
    delivery_thread = threading.Thread(target=deliver, daemon=True, name='scizhi-shell-display') if display_acquired else None
    if delivery_thread:
        delivery_thread.start()

    def flush_live(force=False):
        nonlocal last_emit
        now = time.monotonic()
        if not force and now-last_emit < .2:
            return
        parts = {'stdout': '', 'stderr': ''}
        while True:
            try:
                channel, text = events.get_nowait()
            except queue.Empty:
                break
            parts[channel] = _tail(parts[channel]+text, PREVIEW_BYTES)
        if delivery_thread and not callback_failed and any(parts.values()):
            try:
                deliveries.put_nowait(parts)
            except queue.Full:
                try:
                    deliveries.get_nowait()
                except queue.Empty:
                    pass
                try:
                    deliveries.put_nowait(parts)
                except queue.Full:
                    pass
        if any(parts.values()):
            last_emit = now

    def cancellation():
        if local_cancel.is_set() or (cancel_event is not None and cancel_event.is_set()):
            return True
        return bool(cancelled and cancelled())

    try:
        if spool_dir is not None:
            spool_dir = Path(spool_dir)
            spool_dir.mkdir(parents=True, exist_ok=True)
        for name in ('stdout', 'stderr'):
            captures[name] = _Capture(name, capture_limit, events, spool_dir)
        if cancellation():
            was_cancelled = True
            for capture in captures.values():
                capture.done.set()
        else:
            owner = spawn_owned(command, Path(cwd))
            for name, capture in captures.items():
                thread = threading.Thread(target=capture.drain, args=(getattr(owner, name),), daemon=True,
                                          name='scizhi-shell-' + name)
                thread.start()
                threads.append(thread)
            stop_at = None
            while True:
                now = time.monotonic()
                if not stopped:
                    was_cancelled = cancellation()
                    timed_out = not was_cancelled and now-started >= timeout
                    exited = owner.poll()
                    if any(c.error for c in captures.values()):
                        failure = '输出管道读取失败，已停止命令并保留可获得的输出'
                        timed_out = False
                    if was_cancelled or timed_out or exited is not None or failure:
                        # Foreground commands may not leave detached work behind
                        # holding a pipe after their shell has already exited.
                        owner.stop()
                        stopped, stop_at = True, now
                flush_live()
                if owner.poll() is not None and all(c.done.is_set() for c in captures.values()):
                    break
                if stop_at is not None and now-stop_at > 3:
                    failure = '管道未在停止后完整结束，已保留可获得的输出'
                    break
                time.sleep(.02)
            if exited is None:
                exited = owner.poll()
            tree_empty = owner.tree_empty()
    except Exception as exc:
        failure = str(redact(str(exc)))[:500]
        if owner:
            try:
                owner.stop()
                tree_empty = owner.tree_empty()
            except Exception:
                tree_empty = False
    finally:
        try:
            for thread in threads:
                thread.join(timeout=.5)
            flush_live(force=True)
            delivery_finished.set()
            if delivery_thread:
                delivery_thread.join(timeout=.25)
                if delivery_thread.is_alive():
                    # A blocked consumer must not continue delivering queued
                    # stale updates when it eventually returns.
                    while not deliveries.empty():
                        try:
                            deliveries.get_nowait()
                        except queue.Empty:
                            break
            if owner:
                try:
                    owner.close()
                except Exception as exc:
                    failure = '进程资源清理异常：' + str(redact(str(exc)))[:300]
                    tree_empty = False
            streams = {}
            for name, capture in captures.items():
                try:
                    streams[name] = capture.finish()
                except Exception as exc:
                    failure = '输出采集收尾失败：' + str(redact(str(exc)))[:300]
                finally:
                    capture.file.close()
        finally:
            completed.set()
            delivery_finished.set()
            with _ACTIVE_LOCK:
                _ACTIVE.pop(identity, None)
    return {'exit_code': exited, 'cancelled': was_cancelled, 'timed_out': timed_out,
            'tree_empty': tree_empty, 'streams': streams, 'callback_failed': callback_failed,
            'callback_pending': bool(delivery_thread and delivery_thread.is_alive()),
            'callback_capacity_skipped': bool(on_output and not display_acquired),
            'capture_complete': bool(len(streams) == 2 and all(not row['truncated'] for row in streams.values())),
            'error': failure, 'elapsed_seconds': round(time.monotonic()-started, 4)}
