"""Capture only an isolated frozen application's own Windows window.

Uses installed psutil/Pillow for QA, the bundled Flet client, and workspace
scratch data. Does not install the product or touch an existing application.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows HWND verification only')
    import psutil
    from PIL import ImageGrab, ImageStat
    exe = args.exe.resolve(strict=True)
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / '_testtmp'
    scratch.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='frozen-gui-', dir=scratch))
    data = stage / 'data'
    data.mkdir()
    (data / 'settings.json').write_text(json.dumps({'llm_enabled': False, 'mcp_enabled': False,
        'plugins_enabled': False, 'default_offline': True, 'git_archive': False}), encoding='utf-8')
    client = stage / 'client'
    with zipfile.ZipFile(exe.parent / '_internal/flet_desktop/app/flet-windows.zip') as archive:
        for member in archive.namelist():
            if not (client / member).resolve().is_relative_to(client.resolve()):
                raise ValueError('Unsafe bundled client archive path')
        archive.extractall(client)
    env = {key: value for key, value in os.environ.items() if not key.startswith(('GEZHI_LLM_', 'GEZHI_EMB_'))}
    env.update(GEZHI_DATA_DIR=str(data), FLET_VIEW_PATH=str(client / 'flet'))
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wt.LPARAM]
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    proc = subprocess.Popen([str(exe)], cwd=stage, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    process = psutil.Process(proc.pid)
    children, handle = [], 0
    report = {'ok': False, 'exe': str(exe), 'data_isolated': True, 'uses_bundled_client': True,
              'stage': str(stage), 'launched_pid': proc.pid,
              'verification_scope': 'owned_window_capture_only', 'ui_visual_review': 'required'}
    start = time.monotonic()
    try:
        while time.monotonic() - start < 40 and not handle:
            if proc.poll() is not None:
                raise RuntimeError('Frozen application exited before its window appeared')
            children = process.children(recursive=True)
            owned = {proc.pid, *(child.pid for child in children)}
            found = []
            @callback_type
            def callback(hwnd, _):
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in owned and user32.IsWindowVisible(hwnd):
                    title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
                    user32.GetWindowTextW(hwnd, title, len(title))
                    if 'SciZhi' in title.value:
                        found.append(int(hwnd))
                return True
            user32.EnumWindows(callback, 0)
            if found:
                handle = found[0]
            else:
                time.sleep(.2)
        if not handle:
            raise RuntimeError('No owned application window within 40 seconds')
        report['window_appeared_seconds'] = round(time.monotonic() - start, 3)
        user32.ShowWindow(handle, 9)
        time.sleep(6)
        # Pillow's HWND capture targets this window directly and does not need
        # foreground activation. Revalidate ownership immediately before capture
        # in case the original window was closed or its handle was recycled.
        children = process.children(recursive=True)
        owned = {proc.pid, *(child.pid for child in children)}
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if proc.poll() is not None or owner.value not in owned or not user32.IsWindowVisible(handle):
            raise RuntimeError('Application window ownership changed; refusing capture')
        title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(handle) + 1)
        user32.GetWindowTextW(handle, title, len(title))
        if 'SciZhi' not in title.value:
            raise RuntimeError('Application window title changed; refusing capture')
        report.update(window_pid=owner.value, window_title=title.value,
                      capture_method='Pillow ImageGrab.grab(window=owned_hwnd)')
        shot = ImageGrab.grab(window=handle)
        if min(shot.size) < 300 or max(ImageStat.Stat(shot).stddev) < 4:
            raise RuntimeError('Application window is blank or too small')
        shot.save(output / 'frozen-window.png')
        # This confirms a capture was produced, not that the UI is correct.
        # A reviewer must inspect frozen-window.png for the actual interface.
        report.update(ok=True, capture_ok=True, size=list(shot.size), screenshot='frozen-window.png')
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if handle:
            user32.PostMessageW(handle, 0x0010, 0, 0)
        try:
            proc.wait(12)
        except subprocess.TimeoutExpired:
            for child in reversed(children):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            proc.terminate()
            proc.wait(5)
        (output / 'frozen-window-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
