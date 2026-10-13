"""Real release checks for the local editing, output and shell experience.

Product imports are lazy: desktop_launcher chooses its isolated data directory
and disables network before invoking checks(). No model or Python trampoline is
needed, including when these checks execute inside the windowed frozen EXE.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


def _until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise ValueError('Experience self-check did not reach its expected boundary')
        time.sleep(.01)


def composer_drafts(data_root):
    from .composer_drafts import DraftStore, DraftConflict
    root, scope = Path(data_root) / 'composer_drafts', 'session:selfcheck-draft'
    store = DraftStore(root)
    first = store.save(scope, '未发送草稿\n中文🧪', agent='research', mode='plan', expected_revision='')
    peer = DraftStore(root)
    assert peer.load(scope) == first
    second = peer.save(scope, '修订后的草稿', agent='research', mode='plan', expected_revision=first['revision'])
    try:
        store.save(scope, 'stale writer', expected_revision=first['revision'])
    except DraftConflict as conflict:
        assert conflict.current == second
    else:
        raise ValueError('Stale draft revision was accepted')
    cleared = peer.clear(scope, expected_revision=second['revision'])
    reopened = DraftStore(root).load(scope)
    assert reopened == cleared and not reopened['text'] and reopened['mode'] == 'plan'
    assert cleared['revision'] != second['revision']
    return {'utf8_reopened': True, 'cas_conflict': True, 'clear_reopened': True}


def tool_outputs(runtime):
    from .agent_runtime import ToolCall
    sid, name = 'selfcheck-output', 'selfcheck.large-output'
    tail, calls = '分页中文🧪末尾验证', []
    def large(_):
        calls.append(True)
        return {'ok': True, 'text': '证据' * 10000 + tail}
    runtime._register(name, 'read', 'Isolated release fixture', large)
    try:
        archived = runtime._execute_call(sid, ToolCall('selfcheck-large', name, {}, 'read'))
        assert archived['ok'] and archived['truncated'] and len(calls) == 1
        ref = archived['output_ref']
        found = runtime._execute_call(sid, ToolCall('selfcheck-find', 'tool_output.search',
            {'output_id': ref['output_id'], 'query': tail}, 'read'))
        assert found['ok'] and len(found['matches']) == 1
        offset, text, pages = found['matches'][0]['offset'], '', 0
        while offset is not None:
            page = runtime._execute_call(sid, ToolCall(f'selfcheck-page-{pages}', 'tool_output.read',
                {'output_id': ref['output_id'], 'offset': offset, 'limit': 4}, 'read'))
            assert page['ok'] and '\ufffd' not in page['text']
            assert len(page['text'].encode('utf-8')) <= 4
            text += page['text']
            offset, pages = page['next_offset'], pages + 1
            assert pages < 30
        assert text.startswith(tail)
        denied = runtime._execute_call('selfcheck-output-other', ToolCall('selfcheck-denied', 'tool_output.read',
            {'output_id': ref['output_id']}, 'read'))
        assert denied['ok'] is False and len(calls) == 1
        return {'runtime_archived': True, 'tail_verified': True, 'utf8_pages': pages,
                'cross_session_denied': True, 'original_tool_calls': len(calls)}
    finally:
        runtime.tools.pop(name, None)


def shell_owned_execution(data_root):
    from . import process_owner
    root = Path(data_root) / 'selfcheck-owned-shell'
    root.mkdir()
    command = 'echo 冻结中文 & exit /b 7' if os.name == 'nt' else "printf '冻结中文\\n'; exit 7"
    owned = process_owner.spawn_owned(command, root)
    try:
        _until(lambda: owned.poll() is not None)
        output, error = owned.stdout.read(), owned.stderr.read()
        assert output.decode('utf-8').strip() == '冻结中文' and not error
        assert owned.poll() == 7
        _until(owned.tree_empty)
    finally:
        owned.close()
    if os.name == 'nt':
        powershell = Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        if not powershell.is_file():
            raise FileNotFoundError('System PowerShell is required for the isolated cancellation fixture')
        sleeper = subprocess.list2cmdline([str(powershell), '-NoProfile', '-NonInteractive', '-Command', 'Start-Sleep -Seconds 20'])
        command = 'echo OWNED_READY & ' + sleeper + ' & echo unexpected > owner-late.txt'
    else:
        command = "printf 'OWNED_READY\\n'; sleep 20; printf unexpected > owner-late.txt"
    owned = process_owner.spawn_owned(command, root)
    try:
        # The child emits only one small line; its pending output cannot fill a pipe.
        assert owned.stdout.readline().decode('utf-8').strip() == 'OWNED_READY'
        if os.name == 'nt':
            import ctypes
            def has_descendants():
                account = process_owner._Accounting()
                process_owner._check(process_owner._query_job_info(owned._job, 1,
                    ctypes.byref(account), ctypes.sizeof(account), None))
                return account.ActiveProcesses >= 3  # Two owned cmd layers plus PowerShell.
            _until(has_descendants)
        assert not owned.tree_empty()
        owned.stop()
        _until(owned.tree_empty)
        assert owned.poll() is not None and owned.poll() != 0
        assert not (root / 'owner-late.txt').exists()
        return {'utf8_echo': True, 'exit_code': 7, 'tree_empty_after_cancel': True,
                'cancel_exit_code': owned.poll(), 'system_sleeper': True,
                'python_trampoline': False, 'frozen': bool(getattr(sys, 'frozen', False))}
    finally:
        owned.close()


def shell_stream_capture(runtime, data_root):
    sid = 'selfcheck-stream'
    runtime.sessions.append(sid, 'user_message', {'text': 'Isolated stream check', 'mode': 'agent'})
    frames = []
    result = runtime.tools['shell.run'].handler({'__session_id': sid, '__tool_call_id': 'selfcheck-stream-echo',
        'command': 'echo 流式中文' if os.name == 'nt' else "printf '流式中文\\n'", 'timeout_seconds': 5,
        '__on_output': lambda channel, text: frames.append((channel, text))})
    assert result['ok'] and result['exit_code'] == 0 and result['capture_complete'] and result['tree_empty']
    assert '流式中文' in result['stdout'] and '流式中文' in ''.join(text for _, text in frames)
    journal = [e for e in runtime.sessions.read(sid) if e['type'] == 'shell_output']
    assert journal and all(e['payload']['tool_call_id'] == 'selfcheck-stream-echo' for e in journal)
    cancelled = threading.Event()
    cancelled.set()
    result = runtime.tools['shell.run'].handler({'__session_id': sid, '__cancel_event': cancelled,
        'command': 'echo unexpected > precancel-marker.txt', 'timeout_seconds': 5})
    assert result['cancelled'] and not result['ok']
    marker = Path(data_root) / 'sessions' / sid / 'workspace' / 'precancel-marker.txt'
    assert not marker.exists()
    return {'live_utf8': True, 'capture_complete': True, 'journal_stream_event': True,
            'pre_cancel_no_effect': True, 'tree_empty': result['tree_empty']}


def interaction_controls():
    from . import settings
    from .ui.control_tower import ReasoningControl
    before = settings.load().get('llm_reasoning_effort', 'auto')
    control = ReasoningControl(SimpleNamespace(update=lambda: None), value='auto',
        motion=lambda: False, save=settings.save)
    heights = []
    try:
        assert all(bar.opacity == 0 for bar in control.bars)
        for value in ('low', 'medium', 'high'):
            control.choose(value)
            assert control.value == value and settings.load()['llm_reasoning_effort'] == value
            heights.append(max(bar.height for bar in control.bars))
            assert all(slot.height == 24 and slot.width == 3 for slot in control.bar_slots)
            assert all(bar.animate is None and bar.animate_opacity is None for bar in control.bars)
        assert heights == [12, 18, 24]
        return {'actual_flet_controls': True, 'distinct_heights': True, 'reduced_motion': True,
                'real_settings_saved': True, 'native_pixel_check': False}
    finally:
        settings.save({'llm_reasoning_effort': before})


def checks(runtime, data_root):
    from . import config
    data_root = Path(data_root).resolve()
    if config.DATA_DIR.resolve() != data_root or runtime.sessions.root.resolve() != data_root / 'sessions':
        raise ValueError('Experience checks require the isolated self-check data root')
    return (
        ('composer_drafts', lambda: composer_drafts(data_root)),
        ('tool_outputs', lambda: tool_outputs(runtime)),
        ('shell_owned_execution', lambda: shell_owned_execution(data_root)),
        ('shell_stream_capture', lambda: shell_stream_capture(runtime, data_root)),
        ('interaction_controls', interaction_controls),
    )
