"""Own a shell command and its descendants without global process discovery.

Windows 10 / Server 2016 or later is required. JOB_LIST binds a fresh, unnamed
kill-on-close Job Object *inside* CreateProcess, including during owner crashes.
There is deliberately no fallback to starting an uncontained process.

POSIX uses a new session/process group. Descendants that deliberately call
setsid/setpgid can escape that group; this is cancellation, not a sandbox.
Call stop() to end the owned tree, drain stdout/stderr, then close() to release
resources. close() also stops any descendants left after the shell exits.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO, Protocol


class OwnedProcess(Protocol):
    pid: int
    stdout: BinaryIO
    stderr: BinaryIO

    def poll(self) -> int | None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
    def tree_empty(self) -> bool: ...


class _PosixProcess:
    def __init__(self, command: str, cwd: Path):
        self._process = subprocess.Popen(
            command, cwd=cwd, shell=True, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, close_fds=True,
        )
        self.pid = self._process.pid
        self.stdout = self._process.stdout
        self.stderr = self._process.stderr
        self._lock = threading.RLock()
        self._closed = False
        self._tree_was_empty = False

    def poll(self) -> int | None:
        with self._lock:
            return self._process.poll()

    def tree_empty(self) -> bool:
        with self._lock:
            if self._tree_was_empty:
                return True
            self._process.poll()  # Reap the group leader before checking.
            try:
                os.killpg(self.pid, 0)
            except ProcessLookupError:
                self._tree_was_empty = True
                return True
            return False

    def stop(self) -> None:
        with self._lock:
            if self._closed or self.tree_empty():
                return
            try:
                os.killpg(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                if self.tree_empty():
                    return
                time.sleep(0.01)
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._process.wait(timeout=5)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self.stop()
            finally:
                self._closed = True
                self.stdout.close()
                self.stderr.close()


if os.name == "nt":
    import ctypes
    import msvcrt
    import _winapi
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _SIZE_T = ctypes.c_size_t
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_LIST = 0x0002000D
    _HANDLE_LIST = 0x00020002
    _CREATE_SUSPENDED = 0x00000004
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _spawn_lock = threading.Lock()

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", _SIZE_T),
            ("MaximumWorkingSetSize", _SIZE_T),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", _SIZE_T),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", _SIZE_T), ("JobMemoryLimit", _SIZE_T),
            ("PeakProcessMemoryUsed", _SIZE_T), ("PeakJobMemoryUsed", _SIZE_T),
        ]

    class _Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _StartupInfo(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _StartupInfoEx(ctypes.Structure):
        _fields_ = [("StartupInfo", _StartupInfo),
                    ("lpAttributeList", wintypes.LPVOID)]

    class _ProcessInformation(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    def _native(name, arguments, result):
        function = getattr(_kernel32, name)
        function.argtypes = arguments
        function.restype = result
        return function

    _create_job_object = _native("CreateJobObjectW", [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE)
    _set_job_info = _native("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL)
    _query_job_info = _native("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID], wintypes.BOOL)
    _terminate_job = _native("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
    _initialize_attributes = _native("InitializeProcThreadAttributeList", [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_SIZE_T)], wintypes.BOOL)
    _update_attribute = _native("UpdateProcThreadAttribute", [wintypes.LPVOID, wintypes.DWORD, _SIZE_T, wintypes.LPVOID, _SIZE_T, wintypes.LPVOID, wintypes.LPVOID], wintypes.BOOL)
    _delete_attributes = _native("DeleteProcThreadAttributeList", [wintypes.LPVOID], None)
    _create_process = _native("CreateProcessW", [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR, ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInformation)], wintypes.BOOL)
    _resume_thread = _native("ResumeThread", [wintypes.HANDLE], wintypes.DWORD)

    def _check(success):
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

    def _create_job():
        job = _create_job_object(None, None)  # Unnamed, non-inheritable handle.
        _check(job)
        try:
            limits = _ExtendedLimit()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            _check(_set_job_info(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            return job
        except BaseException:
            _winapi.CloseHandle(job)
            raise

    def _create_windows_process(command, cwd, job, child_handles):
        """Atomically assign Job ownership; return only a suspended process."""
        size = _SIZE_T()
        _initialize_attributes(None, 2, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = ctypes.create_string_buffer(size.value)
        _check(_initialize_attributes(attributes, 2, 0, ctypes.byref(size)))
        try:
            handles = (wintypes.HANDLE * 3)(*child_handles)
            jobs = (wintypes.HANDLE * 1)(job)
            _check(_update_attribute(attributes, 0, _HANDLE_LIST, handles,
                                     ctypes.sizeof(handles), None, None))
            _check(_update_attribute(attributes, 0, _JOB_LIST, jobs,
                                     ctypes.sizeof(jobs), None, None))
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = _winapi.STARTF_USESTDHANDLES | _winapi.STARTF_USESHOWWINDOW
            startup.StartupInfo.wShowWindow = _winapi.SW_HIDE
            startup.StartupInfo.hStdInput = child_handles[0]
            startup.StartupInfo.hStdOutput = child_handles[1]
            startup.StartupInfo.hStdError = child_handles[2]
            startup.lpAttributeList = ctypes.addressof(attributes)
            # Match subprocess's absolute shell resolution, never cwd/PATH.
            shell = os.environ.get("ComSpec")
            if not shell or not os.path.isabs(shell):
                shell = os.path.join(os.environ.get("SystemRoot", ""), "System32", "cmd.exe")
            if not os.path.isabs(shell):
                raise FileNotFoundError("An absolute Windows command processor path is required")
            # This child console uses UTF-8; never change the parent's console.
            # Python honors redirected-stream encoding separately from chcp.
            # cmd caches its pipe code page on startup. A nested command shell
            # must start after chcp to make built-ins such as echo use UTF-8 too.
            args = ctypes.create_unicode_buffer(f'"{shell}" /d /s /c "chcp 65001 >nul & "{shell}" /d /s /c "{command}""')
            environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
            environment_block = ctypes.create_unicode_buffer(''.join(
                f'{key}={value}\0' for key, value in sorted(environment.items(), key=lambda item: item[0].upper())) + '\0')
            result = _ProcessInformation()
            flags = (_CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW | _EXTENDED_STARTUPINFO_PRESENT
                     | 0x400)  # CREATE_UNICODE_ENVIRONMENT
            _check(_create_process(shell, args, None, None, True, flags,
                                   environment_block, os.fspath(cwd), ctypes.byref(startup), ctypes.byref(result)))
            return result.hProcess, result.hThread, result.dwProcessId
        finally:
            _delete_attributes(attributes)

    class _WindowsProcess:
        def __init__(self, pid, process_handle, job, stdout, stderr):
            self.pid = pid
            self.stdout = stdout
            self.stderr = stderr
            self._process_handle = process_handle
            self._job = job
            self._returncode = None
            self._closed = False
            self._tree_was_empty = False
            self._lock = threading.RLock()

        def poll(self) -> int | None:
            with self._lock:
                if self._returncode is None and self._process_handle is not None:
                    if _winapi.WaitForSingleObject(self._process_handle, 0) == _winapi.WAIT_OBJECT_0:
                        self._returncode = _winapi.GetExitCodeProcess(self._process_handle)
                return self._returncode

        def tree_empty(self) -> bool:
            with self._lock:
                if self._job is None:
                    return self._tree_was_empty
                accounting = _Accounting()
                _check(_query_job_info(self._job, 1, ctypes.byref(accounting),
                                      ctypes.sizeof(accounting), None))
                self._tree_was_empty = accounting.ActiveProcesses == 0
                return self._tree_was_empty

        def stop(self) -> None:
            with self._lock:
                if self._closed:
                    return
                if not self.tree_empty():
                    _check(_terminate_job(self._job, 1))
                deadline = time.monotonic() + 5
                if _winapi.WaitForSingleObject(self._process_handle, 5000) != _winapi.WAIT_OBJECT_0:
                    raise TimeoutError("Owned Windows process did not exit after termination")
                while not self.tree_empty():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Owned Windows Job did not become empty after termination")
                    time.sleep(0.01)
                self.poll()

        def close(self) -> None:
            with self._lock:
                if self._closed:
                    return
                try:
                    self.stop()
                finally:
                    # The last Job handle is a kernel-enforced kill fallback.
                    if self._job is not None:
                        _winapi.CloseHandle(self._job)
                        self._job = None
                    self.poll()
                    if self._process_handle is not None:
                        _winapi.CloseHandle(self._process_handle)
                        self._process_handle = None
                    self._closed = True
                    self.stdout.close()
                    self.stderr.close()

    def _spawn_windows(command: str, cwd: Path) -> _WindowsProcess:
        job = _create_job()
        fds = []
        streams = []
        process_handle = thread_handle = None
        try:
            stdin_fd = os.open(os.devnull, os.O_RDONLY)
            fds.append(stdin_fd)
            out_read, out_write = os.pipe()
            fds.extend((out_read, out_write))
            err_read, err_write = os.pipe()
            fds.extend((err_read, err_write))
            handles = [msvcrt.get_osfhandle(fd) for fd in (stdin_fd, out_write, err_write)]
            # All module spawns share the narrow inheritable-handle window;
            # HANDLE_LIST prevents sibling jobs from inheriting these pipes.
            with _spawn_lock:
                try:
                    for handle in handles:
                        os.set_handle_inheritable(handle, True)
                    process_handle, thread_handle, pid = _create_windows_process(command, cwd, job, handles)
                finally:
                    for handle in handles:
                        os.set_handle_inheritable(handle, False)
            for fd in (stdin_fd, out_write, err_write):
                os.close(fd)
                fds.remove(fd)
            for fd in (out_read, err_read):
                stream = os.fdopen(fd, "rb", buffering=0)
                fds.remove(fd)
                streams.append(stream)
            resumed = _resume_thread(thread_handle)
            if resumed != 1:
                if resumed == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                raise OSError(f"Unexpected primary thread suspend count: {resumed}")
            _winapi.CloseHandle(thread_handle)
            thread_handle = None
            return _WindowsProcess(pid, process_handle, job, *streams)
        except BaseException:
            # Even ResumeThread/setup errors retain kernel ownership.
            _terminate_job(job, 1)
            _winapi.CloseHandle(job)
            if process_handle is not None:
                try:
                    _winapi.TerminateProcess(process_handle, 1)
                except OSError:
                    pass
                _winapi.WaitForSingleObject(process_handle, 5000)
                _winapi.CloseHandle(process_handle)
            if thread_handle is not None:
                _winapi.CloseHandle(thread_handle)
            for stream in streams:
                stream.close()
            for fd in fds:
                os.close(fd)
            raise


def spawn_owned(command: str, cwd: Path) -> OwnedProcess:
    """Launch a shell command with binary pipes and ownership from first run."""
    if not isinstance(command, str) or not command or "\x00" in command:
        raise ValueError("command must be a non-empty string without NUL bytes")
    if os.name == "nt":
        return _spawn_windows(command, Path(cwd))
    return _PosixProcess(command, Path(cwd))
