"""Capture only this helper's native Flet Page, without editing app source.

Windows / Flet 0.86. Pillow >= 11.2.1 for optional HWND mode. Example:
    python scripts/capture_desktop.py --sizes 1440x900,1100x760

Each run gets its own GEZHI_DATA_DIR and an offline demonstration task. Existing
application windows and real research data are never used. PNGs and a manifest
are written to a new timestamped directory under --out. The manifest records
requested and actual sizes; OS/DPI constraints can change the effective size.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.wintypes as wt
from datetime import datetime
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import uuid


ROOT = Path(__file__).resolve().parent.parent
SCREENS = {"home": "新任务", "agent": "智能体", "monitor": "研究进度", "notebook": "研究记录",
           'settings-code': '设置', 'navigation-drawer': '智能体', 'project-code': '项目',
           "general-workspace": "智能体", "research-mode": "智能体", "mode-commands": "智能体",
           "model-control": "智能体", "activity-tree": "智能体", "command-search": "新任务",
           "steer-pending": "智能体",
           **{name: '智能体' for name in ('review-inbox', 'review-result', 'review-stale', 'review-accepted')},
           "project-files": "项目", "project-planning": "项目", "project-git": "项目", "project-handoff": "项目",
           "chat-compose": "智能体", "chat-side": "智能体",
           "chat-history": "智能体", "chat-search": "智能体", "chat-input": "智能体", "chat-rewind": "智能体",
           "session-status": "智能体", "session-compacted": "智能体",
           "context-inspector": "智能体", "context-inspector-collapsed": "智能体",
           "draft-restored": "智能体", "draft-conflict": "智能体",
           "tool-output-read": "智能体", "tool-output-search": "智能体",
           "reasoning-auto": "智能体", "reasoning-low": "智能体", "reasoning-medium": "智能体",
           **{f"reasoning-{effort}": "智能体" for effort in ('off', 'minimal', 'xhigh', 'max')},
           "queue-paused": "智能体", "queue-many": "智能体",
           "evidence-brief": "智能体", "evidence-draft": "智能体",
           "onboarding": "智能体", "onboarding-config": "智能体", "onboarding-demo": "智能体",
           "results": "文献与成果", "settings": "设置", "data": "数据分析",
           "home-options": "新任务", "agent-plan": "智能体",
           "tower": "智能体", "swarm-plan": "智能体", "swarm-approval": "智能体", "reasoning": "智能体",
           "agent-approval": "智能体", "agent-policy": "智能体",
           "workflows-panel": "新任务", "tasks-panel": "新任务", "loop-panel": "新任务",
           "remote-panel": "新任务", "commands-panel": "新任务", "settings-features": "设置",
           "model-options": "设置", "model-connections": "设置", "model-profiles": "设置", "model-routes": "设置",
           "model-usage": "设置", "skills-manager": "设置", "research-audit": "文献与成果",
           **{"settings-" + name: "设置" for name in ("general", "appearance", "memory", "notebook", "mcp", "hooks", "commands", "automations", "browser", "subagents", "shortcuts")}}
DEMO_TOPIC = "大模型科研智能体 · 离线界面验收样例"
AGENT_REQUEST = "调研科研智能体的工具调用与证据可追溯性，整理综述框架"


def walk_controls(root):
    """Walk public visual children only, avoiding parent/session references."""
    pending, seen = [root], set()
    while pending:
        control = pending.pop()
        if control is None or id(control) in seen:
            continue
        seen.add(id(control))
        if getattr(control, "visible", True) is False:
            continue
        yield control
        for name in ("controls", "tabs", "actions"):
            children = getattr(control, name, None)
            if isinstance(children, (list, tuple)):
                pending.extend(reversed(children))
        for name in ("content", "leading", "trailing"):
            child = getattr(control, name, None)
            if child is not None and not isinstance(child, (str, int, float, bool)):
                pending.append(child)


def agent_scroller(page):
    """Locate the activity area; the request stays outside its scroll view."""
    return next(c for c in walk_controls(page) if type(c).__name__ == 'Column'
        and getattr(c, 'scroll', None) and any(type(child).__name__ == 'ExpansionTile'
            and getattr(getattr(child, 'title', None), 'value', None) == '执行选项'
            for child in getattr(c, 'controls', [])))


async def click_text(page, text: str) -> None:
    """Invoke the same on_click callback as the native sidebar/history control."""
    for control in walk_controls(page):
        handler = getattr(control, "on_click", None)
        if not callable(handler):
            continue
        if getattr(control, 'tooltip', None) != text and not any(
                getattr(c, "value", None) == text or getattr(c, "content", None) == text
                for c in walk_controls(control)):
            continue
        if getattr(control, "disabled", False):
            raise RuntimeError(f"Requested control is disabled: {text}")
        result = handler(SimpleNamespace(control=control, page=page, data=None))
        if inspect.isawaitable(result):
            await result
        return
    drawer = getattr(page, 'drawer', None)
    for index, destination in enumerate(getattr(drawer, 'controls', []) or []):
        if getattr(destination, 'label', None) != text:
            continue
        # Exercise the same native drawer callbacks used below the docked breakpoint.
        await page.show_drawer()
        drawer.selected_index = index
        result = drawer.on_change(SimpleNamespace(control=drawer, page=page, data=None))
        if inspect.isawaitable(result):
            await result
        return
    raise RuntimeError(f"No clickable control contains exact text: {text}")


def set_field(page, label: str, value: str) -> None:
    """Set a visible input/dropdown as the fixture's interaction prerequisite."""
    control = next((c for c in walk_controls(page) if getattr(c, "label", None) == label), None)
    if control is None:
        raise RuntimeError(f"No visible input has label: {label}")
    control.value = value
    page.update()


async def navigate(page, label: str) -> None:
    """Select a navigation destination, not a toolbar sharing the same tooltip."""
    drawer = getattr(page, 'drawer', None)
    for index, destination in enumerate(getattr(drawer, 'controls', []) or []):
        if getattr(destination, 'label', None) == label:
            await page.show_drawer()
            await asyncio.sleep(.35)  # Let the native drawer mount before selecting its destination.
            drawer.selected_index = index
            result = drawer.on_change(SimpleNamespace(control=drawer, page=page, data=None))
            if inspect.isawaitable(result):
                await result
            return
    await click_text(page, label)


def has_text(page, text: str) -> bool:
    return any(getattr(c, "value", None) == text for c in walk_controls(page))


async def wait_for(predicate, description: str, timeout: float = 12.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(f"Timed out waiting for {description}")
        await asyncio.sleep(0.1)


def open_dialogs(page) -> list:
    # Flet 0.86 exposes pop_dialog(), but its current dialog stack has no public getter.
    return [d for d in page._dialogs.controls if d.open]


async def close_own_dialogs(page) -> None:
    """This is the isolated helper Page; every dialog in it belongs to this run."""
    while page.pop_dialog() is not None:
        pass
    await asyncio.sleep(0.25)


async def close_capture_page(page, ui) -> None:
    """Run real desktop cleanup before destroying this helper's native client."""
    if ui.get('finish_native_close'):
        await ui['finish_native_close'](force=True)
    else:
        import flet as ft
        await page.window.on_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))


async def expand_section(page, label: str, expanded: bool = True) -> None:
    for control in walk_controls(page):
        if type(control).__name__ != 'ExpansionTile':
            continue
        title = getattr(control, 'title', None)
        if (title if isinstance(title, str) else getattr(title, 'value', None)) != label:
            continue
        control.expanded = expanded
        callback = getattr(control, 'on_change', None)
        if callable(callback):
            result = callback(SimpleNamespace(control=control, page=page, data='true' if expanded else 'false'))
            if inspect.isawaitable(result):
                await result
        page.update()
        await asyncio.sleep(.2)
        return
    raise RuntimeError(f'No expandable section: {label}')


async def prepare_draft_state(page, screen: str, ui: dict | None) -> dict:
    """Exercise real draft callbacks; text injection does not test OS typing/IME."""
    if not ui:
        raise RuntimeError('Draft QA requires the helper-captured desktop locals')
    from src.composer_drafts import DraftStore

    drafts, field = ui['drafts'], ui['agent_request']

    async def edit(text: str) -> None:
        field.value = text
        result = field.on_change(SimpleNamespace(control=field, page=page, data=text))
        if inspect.isawaitable(result):
            await result
        page.update()

    # Create through the native controls and navigate through their production
    # callback. Every size/scenario uses fresh sessions in this run's data dir.
    await click_text(page, '新建会话')
    session_a = ui['agent_state']['session_id']
    if not session_a:
        raise RuntimeError('Draft fixture did not select its first new session')
    logs = {session_a: ui['get_runtime'](session_a).sessions.read(session_a)}
    text_a = '未发送的多行研究草稿 A\n请核对支持与反驳材料的来源、阅读范围和适用条件。\n保留失败记录，等待研究者确认后再执行。'
    await edit(text_a)
    await click_text(page, '新建会话')
    session_b = ui['agent_state']['session_id']
    if not session_a or not session_b or session_a == session_b:
        raise RuntimeError('Draft fixture must create two distinct sessions')
    logs[session_b] = ui['get_runtime'](session_b).sessions.read(session_b)
    text_b = '会话 B 的独立草稿\n先整理对照实验设计，不要替换会话 A 的输入。'
    await edit(text_b)
    ui['select_agent_session'](session_a)
    if field.value != text_a:
        raise RuntimeError('Session A draft was not restored after switching from B')
    ui['select_agent_session'](session_b)
    if field.value != text_b:
        raise RuntimeError('Session B draft was not restored after switching from A')
    ui['select_agent_session'](session_a)
    if not drafts.flush():
        raise RuntimeError('Restored draft did not flush to the isolated local store')

    disk = DraftStore(drafts.store.root)
    scope_a, scope_b = drafts.key(session_a), drafts.key(session_b)
    await wait_for(lambda: disk.load(scope_a)['text'] == text_a
        and disk.load(scope_b)['text'] == text_b, 'both session drafts persisted')
    saved_a = disk.load(scope_a)
    if field.value != text_a or not saved_a['revision']:
        raise RuntimeError('Persisted A draft and visible restored input disagree')
    for label in ('执行选项', '研究与会话工具', '开始使用'):
        await expand_section(page, label, False)
    columns = [c for c in walk_controls(page) if type(c).__name__ == 'Column'
        and getattr(c, 'scroll', None) and field in getattr(c, 'controls', [])]
    if columns:
        await columns[0].scroll_to(offset=0, duration=0)

    result = {'fixture': True, 'session_a': session_a, 'session_b': session_b,
        'scope': scope_a, 'session_round_trip_restored': True, 'persisted_readback': True,
        'multiline_chinese': True, 'real_control_callbacks': True, 'real_model_calls': 0,
        'request_dispatched': False, 'process_restart_tested': False,
        'os_keyboard_or_ime_tested': False}
    if screen == 'draft-conflict':
        # A second store instance models another writer using the real CAS
        # contract. This is not a second native window or second OS process.
        remote_text = '另一窗口保存的版本\n请先补齐样本划分与随机种子，再继续整理文献。'
        remote = disk.save(scope_a, remote_text, agent=saved_a['agent'], mode=saved_a['mode'],
                           expected_revision=saved_a['revision'])
        local_text = '当前窗口尚未发送的修订\n新增要求：保留反驳证据，不要覆盖本机已保存的另一版本。'
        await edit(local_text)
        if drafts.flush():
            raise RuntimeError('Stale local draft unexpectedly overwrote the independent writer')
        if field.value != local_text or disk.load(scope_a) != remote:
            raise RuntimeError('Conflict did not preserve both the local input and saved version')
        await wait_for(lambda: drafts.resolve_button.visible, 'draft conflict action visible')
        await click_text(page, '检查草稿')
        await wait_for(lambda: any(getattr(getattr(d, 'title', None), 'value', None) == '比较本地草稿'
            for d in open_dialogs(page)), 'draft comparison dialog')
        dialog = next(d for d in open_dialogs(page)
            if getattr(getattr(d, 'title', None), 'value', None) == '比较本地草稿')
        values = {getattr(c, 'label', None): getattr(c, 'value', None) for c in walk_controls(dialog)
            if getattr(c, 'label', None) in {'当前未发送输入', '本机已保存草稿'}}
        if values != {'当前未发送输入': local_text, '本机已保存草稿': remote_text}:
            raise RuntimeError('Comparison dialog does not show the two verified versions')
        result.update({'revision_conflict': True, 'comparison_dialog_open': True,
            'local_input_preserved': True, 'saved_version_preserved': True,
            'independent_store_same_process': True, 'resolution_clicked': False,
            'original_revision': saved_a['revision'], 'saved_revision': remote['revision']})
    if any(ui['get_runtime'](sid).sessions.read(sid) != logs[sid] for sid in logs):
        raise RuntimeError('Draft-only interaction unexpectedly changed a session event log')
    result['session_logs_unchanged'] = True
    return result


async def prepare_tool_output_state(page, screen: str, data_dir: Path, ui: dict | None) -> dict:
    """Archive a real offline tool result, then exercise the native reader callbacks."""
    if not ui or 'select_agent_session' not in ui:
        raise RuntimeError('Tool output QA requires the helper-captured desktop locals')
    from src.agent_runtime import AgentRuntime, ToolCall
    from src.bridge import handle
    from src.ui.tool_outputs import ToolOutputsDialog

    created = handle({'cmd': 'chat_new', 'project_id': ''})
    if created.get('ok') is not True or not created.get('session_id'):
        raise RuntimeError('Tool output fixture could not create its isolated session')
    sid = created['session_id']
    runtime = AgentRuntime(session_root=data_dir / 'sessions', offline=True, llm_enabled=False)
    needle = '尾部反驳证据'
    fixture_text = ('方法记录\n' +
        '观察记录：固定样本与随机种子；本行仅用于离线界面验收，不代表科学证据。\n' * 600 +
        needle + '：该结论未获独立验证。')
    runtime._register('fixture.large', 'read', '只在隔离截图进程生成长输出',
        lambda args: {'ok': True, 'text': fixture_text, 'synthetic': True}, runtime._obj_schema({}))
    call_id = 'qa-large-' + uuid.uuid4().hex[:12]
    receipt = runtime._execute_call(sid, ToolCall(call_id, 'fixture.large', {}, 'read'))
    ref = receipt.get('output_ref')
    if not isinstance(ref, dict) or not ref.get('output_id') or receipt.get('output_unavailable'):
        raise RuntimeError('Actual tool execution did not produce an archived output reference')
    output_id = ref['output_id']

    def api(command, **arguments):
        reply = handle({'cmd': command, 'session_id': sid, **arguments})
        if reply.get('ok') is not True or reply.get('output', {}).get('session_id') != sid:
            raise RuntimeError(f'Tool output fixture bridge failed: {command}')
        return reply['output']

    # Verify all archived bytes through the production reader. The fixture must
    # not claim full recovery from a short preview or manufacture a result row.
    digest, offset, page_count = hashlib.sha256(), 0, 0
    first_page = None
    while True:
        part = api('session_tool_output_read', output_id=output_id, offset=offset, limit=4000)
        if part.get('output_id') != output_id or part.get('offset') != offset or part.get('sha256') != ref['sha256']:
            raise RuntimeError('Archived output identity or byte position changed during verification')
        if first_page is None:
            first_page = part
        payload = part['text'].encode('utf-8')
        if len(payload) > 4000:
            raise RuntimeError('Production output reader exceeded its requested byte budget')
        digest.update(payload)
        offset += len(payload)
        page_count += 1
        if part['next_offset'] is None:
            break
        if part['next_offset'] != offset or not payload or page_count > 1000:
            raise RuntimeError('Production output reader did not advance through contiguous bytes')
    if offset != ref['total_bytes'] or digest.hexdigest() != ref['sha256']:
        raise RuntimeError('Reassembled reader pages disagree with the archived size or SHA-256')
    if needle in first_page['text']:
        raise RuntimeError('Tail search fixture unexpectedly fits in the initial output page')
    journal_before = runtime.sessions.read(sid)
    ui['select_agent_session'](sid)
    if ui['agent_state']['session_id'] != sid:
        raise RuntimeError('Tool output fixture session was not selected in the desktop')
    await expand_section(page, '研究与会话工具')

    # Observe the instance created by the real toolbar callback; do not replace
    # its API, inject controls, or call a fixture-only product entry point.
    captured = {}
    previous_profile = sys.getprofile()

    def capture_reader(frame, event, arg):
        if event == 'return' and frame.f_code is ToolOutputsDialog.__init__.__code__:
            captured['view'] = frame.f_locals['self']

    try:
        sys.setprofile(capture_reader)
        await click_text(page, '工具输出')
    finally:
        sys.setprofile(previous_profile)
    if 'view' not in captured:
        raise RuntimeError('Toolbar did not construct the native tool output browser')
    view = captured['view']
    await wait_for(lambda: any(row['output_id'] == output_id for row in view.items), 'archived output list row')
    index = next(i for i, row in enumerate(view.items) if row['output_id'] == output_id)
    view.list_rows.controls[index].on_click(SimpleNamespace(control=view.list_rows.controls[index], page=page, data=None))
    await wait_for(lambda: view.current is not None and view.current.get('output_id') == output_id
        and view.current.get('offset') == 0 and not view.busy['read'], 'first native output page')
    if view.body.value != first_page['text'] or view.hash_text.value != ref['sha256']:
        raise RuntimeError('Native initial output page differs from the verified production reader')

    interaction = {'fixture': True, 'synthetic_content': True, 'session_id': sid, 'output_id': output_id,
        'tool': 'fixture.large', 'tool_call_id': call_id, 'total_bytes': ref['total_bytes'],
        'ref_sha256': ref['sha256'], 'full_reader_sha256': digest.hexdigest(),
        'full_reader_pages': page_count, 'full_reader_verified': True,
        'read_limit_bytes': 4000, 'visible_byte_offset': 0,
        'real_bridge_api': True, 'real_control_callbacks': True, 'real_tool_execution': True,
        'real_model_calls': 0, 'no_model_request': True, 'scientific_validation': False}
    if screen == 'tool-output-search':
        expected = api('session_tool_output_search', output_id=output_id, query=needle, offset=0, limit=10)
        if len(expected['matches']) != 1 or expected['matches'][0]['offset'] <= 4000:
            raise RuntimeError('Production search did not locate the unique tail evidence beyond the first page')
        hit_offset = expected['matches'][0]['offset']
        view.query.value = needle
        event = SimpleNamespace(control=view.query, page=page, data=needle)
        view.query.on_change(event)
        view.query.on_submit(event)
        await wait_for(lambda: view.search_current is not None and view.search_current['query'] == needle
            and bool(view.search_current['matches']) and not view.busy['search'], 'native tail search match')
        if view.search_current['matches'][0]['offset'] != hit_offset:
            raise RuntimeError('Native search hit differs from the production byte offset')
        view.matches.controls[0].on_click(SimpleNamespace(control=view.matches.controls[0], page=page, data=None))
        await wait_for(lambda: view.current.get('offset') == hit_offset and not view.busy['read'], 'native hit jump')
        expected_page = api('session_tool_output_read', output_id=output_id, offset=hit_offset, limit=4000)
        if view.body.value != expected_page['text'] or needle not in view.body.value:
            raise RuntimeError('Native hit jump did not show the real archived tail evidence')
        interaction.update(search_query=needle, search_limit=10, hit_byte_offset=hit_offset,
                           visible_byte_offset=hit_offset, search_hit_jump_verified=True)
    interaction['visible_page_bytes'] = len(view.body.value.encode('utf-8'))
    if runtime.sessions.read(sid) != journal_before:
        raise RuntimeError('Read-only tool output browsing unexpectedly changed the session journal')
    interaction['session_journal_unchanged_by_browsing'] = True
    interaction['original_tool_replayed'] = False
    page.update()
    return interaction


async def prepare_workspace_mode_state(page, screen: str, data_dir: Path, ui: dict | None) -> dict:
    """Switch through native mode buttons, then inspect the isolated saved receipt."""
    required = ('workspace_mode_control', 'agent_request', 'agent_pick', 'tools_panel')
    if not ui or any(name not in ui for name in required):
        raise RuntimeError('Workspace mode QA requires the helper-captured desktop controls')
    from src import settings
    if not settings._PATH.resolve().is_relative_to(data_dir.resolve()):
        raise RuntimeError('Workspace mode QA settings must be inside the isolated data directory')
    control, field = ui['workspace_mode_control'], ui['agent_request']
    draft = '阅读当前项目，说明结构与启动方式。\n这是离线界面验收草稿，尚未发送。'
    field.value = draft
    changed = field.on_change(SimpleNamespace(control=field, page=page, data=draft))
    if inspect.isawaitable(changed):
        await changed
    page.update()

    async def select(mode):
        button = control.buttons[mode]
        result = button.on_click(SimpleNamespace(control=button, page=page, data=None))
        if inspect.isawaitable(result):
            await result
        await wait_for(lambda: not control.saving and control.mode == mode,
                       f'actual {mode} mode save callback')
        if settings.load().get('workspace_mode') != mode:
            raise RuntimeError('Native selected mode disagrees with isolated settings readback')
        if field.value != draft:
            raise RuntimeError('Native mode callback discarded the unsent draft')
        if ui['agent_pick'].value != mode:
            raise RuntimeError('Native mode callback did not update the new-session agent')

    target_mode = 'research' if screen == 'research-mode' else 'general'
    # Force a real save transition even when the previous capture left the
    # requested mode active; never paint buttons or edit settings directly.
    await select('general' if target_mode == 'research' else 'research')
    await select(target_mode)
    details = {'fixture': True, 'workspace_mode': target_mode,
        'saved_mode_readback': True, 'real_control_callbacks': True,
        'draft_preserved': True, 'default_agent': ui['agent_pick'].value,
        'no_model_request': True, 'request_dispatched': False,
        'os_keyboard_or_ime_tested': False,
        'navigation_target': '新任务' if target_mode == 'research' else '智能体'}
    if screen == 'mode-commands':
        button = next(c for c in walk_controls(page)
                      if getattr(c, 'tooltip', None) == '命令 · Ctrl+K')
        result = button.on_click(SimpleNamespace(control=button, page=page, data=None))
        if inspect.isawaitable(result):
            await result
        command = ui['tools_panel'].command
        command.value = '模式'
        result = command.on_change(SimpleNamespace(control=command, page=page, data='模式'))
        if inspect.isawaitable(result):
            await result
        from src.command_center import catalog
        if not any(item['name'] == 'mode' for item in catalog('模式')):
            raise RuntimeError('Native command catalog does not discover the workspace mode command')
        details.update(description_query='模式', no_command_execution=True)
        page.update()
    return details


async def prepare_state(page, screen: str, data_dir: Path, *, ui: dict | None = None) -> dict:
    if SCREENS.get(screen) == '智能体':
        await expand_section(page, '会话状态与上下文', screen in {
            'session-status', 'session-compacted', 'context-inspector',
            'context-inspector-collapsed', 'activity-tree'})
    if screen in {'general-workspace', 'research-mode', 'mode-commands'}:
        return await prepare_workspace_mode_state(page, screen, data_dir, ui)
    if ui and 'agent_options' in ui and screen != 'activity-tree':
        ui['agent_options'].visible = ui['agent_toolbox'].visible = ui['onboarding'].content.visible = True
    if screen == 'model-control':
        from src import model_registry
        model_registry.save_connection({'id': 'fixture-local', 'label': '本地研究模型', 'protocol': 'ollama', 'base_url': 'http://localhost:11434'})
        model_registry.save_profile({'id': 'fixture-reader', 'label': '证据阅读', 'connection_id': 'fixture-local', 'model': 'research-reader', 'thinking_style': 'ollama_think'})
        if not ui or 'quick_model_controls' not in ui:
            raise RuntimeError('Native quick model integration missing')
        ui['quick_model_controls'].button.on_click(None)
        await wait_for(lambda: ui['quick_model_controls'].snapshot is not None, 'quick model catalog')
        return {'fixture': True, 'no_model_request': True, 'catalog_read_only': True}
    if screen == 'command-search':
        control = next(c for c in walk_controls(page) if getattr(c, 'tooltip', None) == '命令 · Ctrl+K')
        control.on_click(SimpleNamespace(control=control, page=page, data=None))
        if not ui or 'tools_panel' not in ui:
            raise RuntimeError('Native command panel integration missing')
        command = ui['tools_panel'].command
        command.value = '模型'
        command.on_change(SimpleNamespace(control=command, page=page, data='模型'))
        page.update()
        return {'description_query': '模型', 'no_command_execution': True}
    if screen == 'activity-tree':
        from src.agent_runtime import SessionStore
        from src.state_store import write_json
        await click_text(page, '新建会话')
        store = SessionStore(data_dir / 'sessions')
        sid = store.list()[0]['session_id']
        store.append(sid, 'tool_call', {'id': 'fixture-search', 'name': 'research.search'})
        store.append(sid, 'tool_result', {'id': 'fixture-search', 'result': {'ok': True, 'message': '离线样例检索记录'}})
        store.append(sid, 'completed', {'status': 'done'})
        for child, agent, status in [('fixture-reader', 'literature', 'done'), ('fixture-critic', 'critic', 'cancelled')]:
            store.register_sidechain(child, sid)
            store.append(child, 'user_message', {'text': '离线界面样例 · 对照支持与反驳证据', 'agent': agent})
            store.append(child, 'completed', {'status': status})
            write_json(store.root / sid / 'children' / f'{child}.meta.json', {'schema_version': 1, 'child': child, 'parent': sid, 'agent': agent, 'status': status})
        if not ui or 'activity_panel' not in ui:
            raise RuntimeError('Native activity integration missing')
        ui['activity_panel'].expand()
        await wait_for(lambda: 'fixture-critic' in ui['activity_panel'].child_buttons, 'actual saved activity tree')
        ui['activity_panel'].content.expanded = True
        ui['agent_options'].visible = ui['agent_toolbox'].visible = ui['onboarding'].content.visible = False
        set_field(page, '交给智能体的任务', '未发送的研究草稿：比较两种检索策略。')
        page.update()
        await ui['agent_activity'].scroll_to(offset=150, duration=0)
        await asyncio.sleep(4)
        return {'fixture': True, 'session_id': sid, 'no_model_request': True, 'draft_preserved': True}
    if screen in {'draft-restored', 'draft-conflict'}:
        return await prepare_draft_state(page, screen, ui)
    if screen in {'tool-output-read', 'tool-output-search'}:
        return await prepare_tool_output_state(page, screen, data_dir, ui)
    if screen in {'review-inbox', 'review-result', 'review-stale', 'review-accepted'}:
        from src.bridge import handle
        handle({'cmd': 'settings_save', 'settings': {'experimental_evidence_reviews': True}})
        context = await prepare_state(page, 'evidence-brief', data_dir)
        record_id = context['record_id']
        original = handle({'cmd': 'notebook_get', 'id': record_id})['record']
        await click_text(open_dialogs(page)[-1], '后台复核')
        await wait_for(lambda: any(getattr(getattr(dialog, 'title', None), 'value', None) == '后台证据复核收件箱'
            for dialog in open_dialogs(page)), 'review inbox opened')
        inbox = next(dialog for dialog in open_dialogs(page) if getattr(getattr(dialog, 'title', None), 'value', None) == '后台证据复核收件箱')
        await wait_for(lambda: any(getattr(c, 'content', None) == '开始后台复核' and not c.disabled
            for c in walk_controls(inbox)), 'version-bound review action ready')
        prior = {row['id'] for row in handle({'cmd': 'review_list', 'record_id': record_id})['jobs']}
        await click_text(inbox, '开始后台复核')
        await wait_for(lambda: any(row['id'] not in prior and row['status'] == 'ready'
            for row in handle({'cmd': 'review_list', 'record_id': record_id})['jobs']), 'new background review ready')
        job = next(row for row in handle({'cmd': 'review_list', 'record_id': record_id})['jobs'] if row['id'] not in prior)
        await wait_for(lambda: any(getattr(c, 'key', None) == 'review-job-' + job['id']
            and has_text(c, '待研究者核查') for c in walk_controls(inbox)), 'new review ready state rendered')
        card = next(c for c in walk_controls(inbox) if getattr(c, 'key', None) == 'review-job-' + job['id'])
        assert job['status'] == 'ready'
        assert handle({'cmd': 'notebook_get', 'id': record_id})['record'] == original
        if screen == 'review-result':
            await click_text(card, '查看结果')
            await wait_for(lambda: len(open_dialogs(page)) >= 2, 'review report dialog')
        elif screen == 'review-stale':
            changed = handle({'cmd': 'notebook_save', 'expected_version': original['version'],
                'record': {'id': record_id, 'limitations': '验收中明确修改：增加了新的适用范围，旧复核需要重新检查。'}})
            assert changed['ok']
            await click_text(inbox, '刷新记录与任务')
            await wait_for(lambda: any('原记录已变化' in str(getattr(c, 'value', '')) for c in walk_controls(inbox)), 'stale review visibly blocked')
            assert not handle({'cmd': 'review_get', 'job_id': job['id']})['job']['can_accept']
        elif screen == 'review-accepted':
            await click_text(card, '保存为复核备注')
            await wait_for(lambda: any(getattr(c, 'value', None) == '已保存复核备注' for c in walk_controls(inbox)), 'explicit review note accepted')
            saved = handle({'cmd': 'notebook_get', 'id': record_id})['record']
            assert saved['version'] == original['version'] + 1
            assert saved['evidence'] == original['evidence']
            assert len(saved['observations']) == len(original['observations']) + 1
        return {'record_id': record_id, 'record_version': original['version'], 'job_id': job['id'],
                'real_background_local_review': True, 'record_unchanged_before_explicit_action': True,
                'stale_case': screen == 'review-stale', 'explicit_note_acceptance': screen == 'review-accepted',
                'real_model_calls': 0, 'all_materials_synthetic': True}
    if screen == 'steer-pending':
        from unittest.mock import patch
        from src import llm
        from src.bridge import handle
        from src.agent_runtime import AgentRuntime, SessionStore
        from tests.test_adaptive_driver import response
        handle({'cmd': 'settings_save', 'settings': {'experimental_inflight_steering': True, 'agent_stream_enabled': False}})
        store = SessionStore(data_dir / 'sessions')
        previous = {row['session_id'] for row in store.list()}
        await click_text(page, '新建会话')
        sid = ({row['session_id'] for row in store.list()} - previous).pop()
        runtime = AgentRuntime(session_root=store.root, offline=False, llm_enabled=True)
        runtime._register('qa.write', 'write', '未执行的验收写入', lambda _: (_ for _ in ()).throw(AssertionError('must not execute')), runtime._obj_schema({}))
        with patch.object(llm, 'available', return_value=True), patch.object(llm, 'chat', return_value=response(tool='qa.write')):
            result = runtime.send(sid, '合成验收：生成一个需要审批的操作')
        if result['status'] != 'waiting_approval':
            raise RuntimeError('Steering fixture must pause before any tool effect')
        await asyncio.sleep(1.5)
        await close_own_dialogs(page)
        await expand_section(page, '执行选项', False)
        await expand_section(page, '研究与会话工具', False)
        await expand_section(page, '开始使用', False)
        set_field(page, '交给智能体的任务', '不要执行原写入，请先核对反例与适用条件。')
        field = next(c for c in walk_controls(page) if getattr(c, 'label', None) == '交给智能体的任务')
        field.on_change(SimpleNamespace(control=field, page=page))
        await wait_for(lambda: any(getattr(c, 'content', None) == '补充本轮' and not c.disabled
            for c in walk_controls(page)), 'steering action ready')
        await click_text(page, '补充本轮')
        await wait_for(lambda: any('本轮补充 1 条待处理' in str(getattr(c, 'value', '')) for c in walk_controls(page)), 'durable steer receipt')
        correction = handle({'cmd': 'steer_status', 'session_id': sid})['steering']
        assert correction['items'][0]['state'] == 'pending' and field.value == ''
        assert not any(e['type'] in {'tool_call', 'permission_granted', 'steer_applied'} for e in store.read(sid))
        return {'session_id': sid, 'turn_id': correction['turn_id'], 'pending_corrections': 1,
                'real_bridge_receipt': True, 'mocked_initial_model_calls': 1, 'real_model_calls': 0,
                'executed_tools': 0, 'draft_cleared_after_matching_receipt': True, 'requires_explicit_continue': True}
    if screen == 'agent':
        await click_text(page, '新建会话')
        await expand_section(page, '执行选项', False)
        await expand_section(page, '研究与会话工具', False)
        await expand_section(page, '开始使用', False)
        stop = next(c for c in walk_controls(page) if getattr(c, 'content', None) == '停止会话')
        if not stop.disabled:
            raise RuntimeError('Idle draft must not offer cancellation')
        return {'fresh_draft': True, 'stop_disabled': True, 'no_model_request': True}
    if screen in {'queue-paused', 'queue-many'}:
        from src.bridge import handle
        from src.agent_runtime import SessionStore
        saved = handle({'cmd': 'settings_save', 'settings': {'experimental_message_queue': True}})
        if not saved.get('ok'):
            raise RuntimeError(saved.get('error'))
        store = SessionStore(data_dir / 'sessions')
        previous = {row['session_id'] for row in store.list()}
        await click_text(page, '新建会话')
        created = {row['session_id'] for row in store.list()} - previous
        if len(created) != 1:
            raise RuntimeError('New-session action did not create exactly one QA session')
        sid = created.pop()
        await wait_for(lambda: any(sid in str(getattr(c, 'value', ''))
            for c in walk_controls(page)), 'new QA session selected')
        paused = handle({'cmd': 'queue_pause', 'session_id': sid})
        if not paused.get('ok'):
            raise RuntimeError(paused.get('error'))
        count = 3 if screen == 'queue-paused' else 14
        for index in range(count):
            reply = handle({'cmd': 'queue_add', 'session_id': sid,
                'queue_request_id': f'qa-{sid}-{index}',
                'request': f'第 {index + 1} 项后续研究：核对支持与反驳材料的条件、来源范围和失败记录。\n这是未执行的界面验收请求。'})
            if not reply.get('ok'):
                raise RuntimeError(reply.get('error'))
        await wait_for(lambda: has_text(page, f'后续任务 {count}'), 'durable paused queue shown')
        await expand_section(page, '执行选项', False)
        await expand_section(page, '研究与会话工具', False)
        await expand_section(page, '查看后续任务')
        await expand_section(page, '开始使用', False)
        set_field(page, '交给智能体的任务', '未发送草稿：继续核对实验条件，暂不加入队列。')
        field = next(c for c in walk_controls(page) if getattr(c, 'label', None) == '交给智能体的任务')
        field.on_change(SimpleNamespace(control=field, data=field.value, page=page))
        queued = handle({'cmd': 'queue_list', 'session_id': sid})['queue']
        if not queued['paused'] or any(item['state'] != 'queued' for item in queued['items']):
            raise RuntimeError('QA queue must remain paused and undispatched')
        if any(event.get('payload', {}).get('queue_ticket') for event in store.read(sid)):
            raise RuntimeError('Paused QA queue unexpectedly dispatched a turn')
        stop = next(c for c in walk_controls(page) if getattr(c, 'content', None) == '停止会话')
        if not stop.disabled:
            raise RuntimeError('Paused queue beside an idle draft must not enable stopping a turn')
        column = agent_scroller(page)
        await column.scroll_to(offset=0, duration=0)
        return {'session_id': sid, 'queued': count, 'paused': True, 'current_turn_dispatched': False,
                'fixture': True, 'no_model_request': True, 'draft_preserved': True,
                'stop_disabled': True, 'queue_expanded': True, 'secondary_tools_collapsed': True}
    if screen in {'evidence-brief', 'evidence-draft'}:
        from src import research_notebook, service
        marker = data_dir / 'evidence-capture-record.json'
        task = next(row['task_id'] for row in service.list_tasks() if row['topic'] == DEMO_TOPIC)
        if not marker.exists():
            saved = research_notebook.handle({'cmd': 'notebook_save', 'record': {
                'kind': 'hypothesis', 'title': '反例检索能否改善综述边界说明？', 'task_id': task,
                'question': '相同文献池与预算下，主动核对反驳材料是否减少过度归因？',
                'prediction': '需要独立盲评检验；尚未执行实验。', 'synthetic': True,
                'evidence': [
                    {'relation': 'supports', 'source': {'id': 'qa-support', 'title': '合成支持材料', 'synthetic': True},
                     'quote': '界面验收例句：对照条件下记录了更完整的适用边界。', 'scope': 'abstract', 'locator': '摘要'},
                    {'relation': 'challenges', 'source': {'id': 'qa-challenge', 'title': '合成反驳材料', 'synthetic': True},
                     'quote': '界面验收例句：新增检索也可能引入不相关信息，不能直接推断效果改善。', 'scope': 'fulltext', 'locator': '第 2 页'},
                    {'relation': 'context', 'source': {'id': 'qa-background', 'synthetic': True},
                     'quote': '合成背景材料：记录模型、文献池、预算和人工复核规则。', 'scope': 'metadata'}]}})
            if not saved.get('ok'):
                raise RuntimeError(saved.get('error'))
            marker.write_text(json.dumps({'record_id': saved['record']['id']}), encoding='utf-8')
        await click_text(page, DEMO_TOPIC)
        await asyncio.sleep(.35)  # Complete the task-page transition before navigating back.
        await click_text(page, '智能体')
        await asyncio.sleep(.35)  # Allow the native page to mount before invoking its controls.
        draft = '这份已有草稿必须保留，证据对照不得自动替换或入队。'
        if screen == 'evidence-draft':
            set_field(page, '交给智能体的任务', draft)
        field = next(c for c in walk_controls(page) if getattr(c, 'label', None) == '交给智能体的任务')
        await expand_section(page, '研究与会话工具')
        await click_text(page, '证据对照')
        await wait_for(lambda: bool(open_dialogs(page)), 'evidence comparison dialog')
        dialog = open_dialogs(page)[-1]
        await wait_for(lambda: has_text(dialog, '反例检索能否改善综述边界说明？'), 'saved evidence record shown')
        if screen == 'evidence-draft':
            await click_text(dialog, '作为下一问')
            await wait_for(lambda: any('草稿，已保留' in str(getattr(c, 'value', ''))
                for c in walk_controls(dialog)), 'existing draft protection')
            if field.value != draft:
                raise RuntimeError('Evidence action replaced an existing draft')
        return {'task_id': task, 'record_id': json.loads(marker.read_text(encoding='utf-8'))['record_id'],
                'fixture': True, 'all_evidence_synthetic': True, 'scientific_verification': 'not_performed',
                'draft_protection_checked': screen == 'evidence-draft', 'no_model_request': True}
    if screen in {'onboarding', 'onboarding-config', 'onboarding-demo'}:
        await click_text(page, '新建会话')
        await expand_section(page, '开始使用')
        await wait_for(lambda: has_text(page, '新会话：规则模式'), 'local first-run diagnosis')
        if screen == 'onboarding-config':
            await click_text(page, '配置模型')
            await wait_for(lambda: any(getattr(c, 'label', None) == '服务地址'
                for c in walk_controls(page)), 'model settings opened directly')
            return {'navigation_target': '设置', 'model_settings_direct': True, 'no_model_request': True}
        if screen == 'onboarding-demo':
            from src import service
            set_field(page, '交给智能体的任务', '这是一份未提交的研究草稿。')
            await click_text(page, '体验离线研究')
            await wait_for(lambda: any(row.get('workflow_id') == 'literature_scan'
                and row.get('status') == 'done' for row in service.list_tasks()), 'explicit offline demo completed')
            return {'navigation_target': '研究进度', 'explicit_offline_demo': True, 'no_model_request': True}
        await click_text(page, '填入示例请求')
        column = agent_scroller(page)
        await column.scroll_to(offset=210, duration=0)
        return {'example_filled_not_sent': True, 'no_model_request': True}
    if screen in {'session-status', 'session-compacted', 'context-inspector', 'context-inspector-collapsed'}:
        from src.agent_runtime import SessionStore
        from src.state_store import write_json
        context_fixture = screen.startswith('context-inspector')
        await click_text(page, '新建会话')
        store = SessionStore(data_dir / 'sessions')
        sid = store.list()[0]['session_id']
        messages = [{'role': 'system', 'content': '证据可追溯，权限由运行时决定。'},
                    {'role': 'user', 'content': '核验科研智能体的支持与反驳材料'}]
        for index in range(2 if context_fixture else 10):
            question = f'第 {index + 1} 项核验：' + '保持对照条件，记录失败与局限。' * 70
            answer = '离线界面验收样例；不代表真实研究结果。' * 70
            messages += [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]
            store.append(sid, 'user_message', {'text': question, 'mode': 'agent'})
            store.append(sid, 'assistant_message', {'step': 1, 'content': answer})
        store.append(sid, 'driver_started', {'driver': 'adaptive'})
        store.append(sid, 'completed', {'status': 'done'})
        fixture_state = {
            'schema_version': 1, 'session_id': sid, 'status': 'done', 'messages': messages,
            'model_identity': {'model': 'fixture-observation', 'protocol': 'responses'},
            'tokens': 1250, 'model_calls': 3, 'max_steps': 16, 'token_budget': 100000,
            'usage_complete': True, 'context_limit_tokens': 32000, 'compressions': 0,
            'turn_usage': [{'tokens': 2200, 'model_calls': 4, 'usage_complete': False}]}
        if context_fixture:
            fixture_state['workspace_mode'] = 'research'
            fixture_state['context_manifest'] = {
                'schema_version': 1, 'status': 'captured', 'basis': 'frozen_prefix',
                'captured_at': '2026-10-06T10:20:30+08:00', 'prompt_version': '2.1',
                'workspace_mode': 'research', 'agent': 'research', 'stage': 'planning',
                'project_id': 'fixture-project',
                'budget': {'unit': 'characters', 'used_chars': 2180, 'limit_chars': 6000},
                'sources': [
                    {'kind': 'instructions', 'name': 'GEZHI.md', 'scope': 'project',
                     'path': 'D:/fixture-project/GEZHI.md', 'status': 'included',
                     'used_chars': 1200, 'total_chars': 1200},
                    {'kind': 'skill_index', 'name': 'evidence-audit', 'scope': 'stage',
                     'status': 'included', 'used_chars': 200, 'total_chars': 200},
                    {'kind': 'memory', 'name': '已批准的项目记忆', 'scope': 'project',
                     'status': 'included', 'used_chars': 780, 'total_chars': 780,
                     'body': 'PRIVATE FIXTURE MEMORY MUST NOT RENDER'},
                    {'kind': 'skill_index', 'name': 'experiment-review', 'scope': 'stage',
                     'status': 'budget_deferred', 'used_chars': 0, 'total_chars': 4100,
                     'reason': '字符预算不足'},
                    {'kind': 'skill_body', 'name': 'evidence-audit', 'scope': 'application',
                     'status': 'partial', 'used_chars': 6000, 'total_chars': 8000},
                ], 'notices': ['合成界面验收样例；来源正文不在此展示。'],
            }
        write_json(store.root / f'{sid}.driver.json', fixture_state)
        await wait_for(lambda: any('fixture-observation' in str(getattr(c, 'value', ''))
            for c in walk_controls(page)), 'saved session metrics')
        set_field(page, '交给智能体的任务', '待发送草稿：继续检查反驳证据。')
        if screen == 'session-compacted':
            await click_text(page, '整理上下文')
            await wait_for(lambda: any('已整理：估算' in str(getattr(c, 'value', ''))
                for c in walk_controls(page)), 'offline context compaction')
        await wait_for(lambda: any(str(getattr(c, 'value', '')).startswith('第 1 项核验：')
            for c in walk_controls(page)), 'fixture conversation layout')
        await asyncio.sleep(.4)
        column = agent_scroller(page)
        if context_fixture:
            await wait_for(lambda: any('已冻结' in str(getattr(c, 'value', ''))
                for c in walk_controls(page)), 'persisted context manifest')
            await expand_section(page, '执行上下文来源', screen == 'context-inspector')
            if any('PRIVATE FIXTURE MEMORY' in str(getattr(c, 'value', '')) for c in walk_controls(page)):
                raise RuntimeError('Context inspector leaked fixture memory body')
            await close_own_dialogs(page)
        await column.scroll_to(offset=440 if screen == 'context-inspector' else 320, duration=0)
        return {'session_id': sid, 'fixture': True, 'reported_usage_is_synthetic': True,
                'no_model_request': True, 'compacted': screen == 'session-compacted', 'draft_preserved': True,
                'context_manifest_is_synthetic': context_fixture,
                'context_inspector_expanded': screen == 'context-inspector'}
    if screen in {"tower", "swarm-plan", "swarm-approval"} or screen.startswith('reasoning'):
        set_field(page, "交给智能体的任务", AGENT_REQUEST)
        if screen.startswith('reasoning'):
            selected = {'reasoning-auto': ('auto','自动'), 'reasoning-low': ('low','轻量'),
                        'reasoning-medium': ('medium','均衡'), 'reasoning-off': ('off','关闭'),
                        'reasoning-minimal': ('minimal','极低'), 'reasoning-xhigh': ('xhigh','超高'),
                        'reasoning-max': ('max','最高')}.get(screen, ('high','深入'))
            if selected[0] in {'off', 'minimal', 'xhigh', 'max'}:
                menu = next(c for c in walk_controls(page) if type(c).__name__ == 'PopupMenuButton'
                    and len(getattr(c, 'items', [])) == 8)
                item = next(item for item in menu.items
                    if item.content.controls[0].controls[1].value == selected[0])
                item.on_click(None)
            else:
                await click_text(page, selected[1])
            from src import settings
            assert settings.load()['llm_reasoning_effort'] == selected[0]
            return {"reasoning_effort": selected[0], "isolated_settings": True, "no_model_request": True,
                    'saved_setting_verified': True, 'shape_transition_ms': 180}
        await expand_section(page, '研究与会话工具')
        await click_text(page, "控制塔" if screen == "tower" else "Swarm 协作")
        await wait_for(lambda: bool(open_dialogs(page)), "control tower dialog")
        dialog = open_dialogs(page)[-1]
        if screen in {"swarm-plan", "swarm-approval"}:
            await wait_for(lambda: any(getattr(c, "content", None) == "生成 Swarm 计划"
                and not getattr(c, "disabled", True) for c in walk_controls(dialog)), "Swarm planner ready")
            await click_text(dialog, "生成 Swarm 计划")
            await wait_for(lambda: any(getattr(c, "content", None) == "启动 Swarm"
                and not getattr(c, "disabled", True) for c in walk_controls(dialog)), "Swarm preview")
            if screen == "swarm-approval":
                await click_text(dialog, "启动 Swarm")
                await wait_for(lambda: any(getattr(c, "content", None) == "查看审批"
                    for c in walk_controls(dialog)), "Swarm permission gate")
                return {"swarm_plan": True, "execution_started": True, "approval_clicked": False,
                        "no_model_request": True}
            return {"swarm_plan": True, "execution_started": False, "no_model_request": True}
        await wait_for(lambda: has_text(dialog, "Swarm 协作 · 0") or
            any(str(getattr(c, "value", "")).startswith("科研任务 ·") for c in walk_controls(dialog)), "tower snapshot")
        return {"control_tower": True, "source": "isolated offline fixtures", "no_model_request": True}
    if screen.startswith('project-'):
        from src.ui.project_studio import SECTIONS
        view = 'files' if screen == 'project-code' else screen.removeprefix('project-')
        await click_text(page, SECTIONS[view])
        if screen in {'project-files', 'project-code'}:
            await click_text(page, 'analysis.py' if screen == 'project-code' else '研究方案.md')
        await asyncio.sleep(.7)
        return {'project_fixture': True, 'view': view, 'no_model_request':True}
    if screen in {'chat-history', 'chat-search', 'chat-input', 'chat-rewind'}:
        from src.agent_runtime import SessionStore
        await click_text(page, '新建会话')
        store = SessionStore(data_dir / 'sessions')
        sid = store.list()[0]['session_id']
        for index in range(23):
            store.append(sid, 'user_message', {'text': f'第 {index + 1} 轮：复核实验对照与证据出处。', 'mode': 'agent'})
            store.append(sid, 'assistant_message', {'step': 1,
                'content': f'第 {index + 1} 轮验收样例：保留对照条件、反驳证据和失败记录。\n\n'
                           '**下一步**：逐项核对材料。此处为界面测试内容，不是真实实验结论。'})
            store.append(sid, 'completed', {'status': 'done'})
        if screen == 'chat-input':
            await wait_for(lambda: has_text(page, '第 1 轮：复核实验对照与证据出处。'),
                           'long conversation in bounded event view')
            set_field(page, '交给智能体的任务', '继续复核反驳证据，并记录下一轮对照条件。')
            return {'session_id': sid, 'messages': 46, 'fixture': True,
                    'no_model_request': True, 'unsent_draft': True}
        await click_text(page, '回滚上一轮' if screen == 'chat-rewind' else '对话记录')
        await wait_for(lambda: bool(open_dialogs(page)), 'session history / rewind dialog')
        if screen == 'chat-search':
            dialog = open_dialogs(page)[-1]
            field = next(c for c in walk_controls(dialog) if getattr(c, 'label', None) == '搜索对话')
            field.value = '第 23 轮'
            result = field.on_submit(SimpleNamespace(control=field, page=page, data=field.value))
            if inspect.isawaitable(result):
                await result
            page.update()
        return {'session_id': sid, 'messages': 46, 'fixture': True,
                'no_model_request': True, 'rewind_applied': False,
                'search_query': '第 23 轮' if screen == 'chat-search' else ''}
    if screen in {'chat-compose', 'chat-side'}:
        close = next((c for c in walk_controls(page) if getattr(c,'tooltip',None)=='收起侧边讨论'), None)
        if close:
            close.on_click(SimpleNamespace(control=close))
        await click_text(page, '新建会话')
        set_field(page, '交给智能体的任务', '# 复现实验\n\n比较检索前后，方法局限的覆盖率是否改变。\n\n- 固定文献池与随机种子\n- 记录失败样本和反驳证据')
        await click_text(page, '预览 Markdown')
        if screen == 'chat-side':
            await click_text(page, '侧边讨论')
        return {'markdown_preview':True,'side_chat':screen=='chat-side','no_model_request':True}
    if screen == 'navigation-drawer':
        await page.show_drawer()
        return {'navigation_drawer': True, 'no_model_request': True}
    category = None
    if screen.startswith("settings-") and screen != "settings-features":
        category = 'appearance' if screen == 'settings-code' else screen.removeprefix("settings-")
    elif screen.startswith("model-"):
        category = "usage" if screen == "model-usage" else "models"
    elif screen == "skills-manager":
        category = "skills"
    if category:
        from src.ui.settings_center import CATEGORIES
        picker = next((c for c in walk_controls(page) if getattr(c, "label", None) == "设置分类"), None)
        if picker:
            picker.value = category
            picker.on_select(SimpleNamespace(control=picker, page=page, data=None))
        else:
            await click_text(page, next(label for key, label, _ in CATEGORIES if key == category))
        await asyncio.sleep(.3)
        if screen == 'settings-appearance':
            column = next(c for c in walk_controls(page) if type(c).__name__ == 'Column'
                and getattr(c, 'scroll', None) and any(getattr(child, '_scizhi_code_preview', False)
                    for child in walk_controls(c)))
            await column.scroll_to(offset=0, duration=0)
        if screen == 'settings-code':
            column = next(c for c in walk_controls(page) if type(c).__name__ == 'Column'
                and getattr(c, 'scroll', None) and any(getattr(child, '_scizhi_code_preview', False)
                    for child in walk_controls(c)))
            await column.scroll_to(offset=760, duration=0)
        if screen.startswith("settings-") or screen in {"skills-manager", "model-usage"}:
            return {"settings_category": category, "no_model_request": True}
    if screen == "research-audit":
        tabs = next(c for c in walk_controls(page) if type(c).__name__ == "Tabs")
        tabs.selected_index = 5
        page.update()
        return {"research_audit": "traceability_only", "fixture": True}
    if screen.startswith("model-"):
        column = next(c for c in walk_controls(page)
                      if type(c).__name__ == "Column" and getattr(c, "scroll", None)
                      and any(getattr(x, "value", None) == "默认模型连接" for x in getattr(c, "controls", [])))
        target = "model_options" if screen == "model-options" else "model_usage"
        if screen in {"model-connections", "model-profiles", "model-routes"}:
            if not any(getattr(c, "label", None) == "配置内容" for c in walk_controls(page)):
                await click_text(page, "多连接与模型路由")
            selector = next(c for c in walk_controls(page) if getattr(c, "label", None) == "配置内容")
            selector.value = screen.removeprefix("model-")
            selector.on_select(SimpleNamespace(control=selector, page=page, data=None))
            target = "advanced_models"
        await asyncio.sleep(.5)
        await column.scroll_to(scroll_key=target, duration=0)
        await asyncio.sleep(.3)
        return {"model_configuration_view": screen, "no_model_request": True}
    if screen == "skills-manager":
        await click_text(page, "科研 Skill 与阶段加载")
        return {"skills_selection": True, "settings_modified": False}
    if screen == "settings-features":
        column = next(c for c in walk_controls(page)
                      if type(c).__name__ == "Column" and getattr(c, "scroll", None)
                      and any(getattr(x, "value", None) == "工作台功能" for x in getattr(c, "controls", [])))
        await asyncio.sleep(.5)
        await column.scroll_to(offset=900, duration=0)
        await asyncio.sleep(.4)
        return {"feature_switches": True, "settings_modified": False}
    panel_tips = {"workflows-panel": "科研工作流", "tasks-panel": "任务进程", "loop-panel": "Loop",
                  "remote-panel": "手机远控", "commands-panel": "命令 · Ctrl+K"}
    if screen in panel_tips:
        control = next(c for c in walk_controls(page) if getattr(c, "tooltip", None) == panel_tips[screen])
        control.on_click(SimpleNamespace(control=control, page=page, data=None))
        await asyncio.sleep(1)
        return {"tool_panel": panel_tips[screen], "no_action_executed": True}
    if screen == "home":
        if any(getattr(c, "label", None) == "起始年" for c in walk_controls(page)):
            await click_text(page, "检索条件")
        return {"search_options_visible": False}
    if screen == "data":
        await click_text(page, "试试内置示例数据")
        return {"data_source": "examples/experiment_demo.csv"}
    if screen == "home-options":
        # The same Page is reused at both sizes, so open the section idempotently.
        if not any(getattr(c, "label", None) == "起始年" for c in walk_controls(page)):
            await click_text(page, "检索条件")
        labels = {getattr(c, "label", None) for c in walk_controls(page)
                  if isinstance(getattr(c, "label", None), str)}
        expected = {"关键词（空格分隔，可空）", "起始年", "截止年", "参考文献格式"}
        if not expected <= labels:
            raise RuntimeError("Search options did not become visible")
        return {"search_options_visible": True, "visible_fields": sorted(expected)}
    if screen in {"agent-plan", "agent-approval"}:
        set_field(page, "交给智能体的任务", AGENT_REQUEST)
        set_field(page, "专项智能体", "literature" if screen == "agent-approval" else "research")
        set_field(page, "执行模式", "agent")
        if screen == "agent-plan":
            await click_text(page, "预览计划")
            await wait_for(lambda: has_text(page, "计划预览 · 尚未执行"), "offline plan preview")
            return {"plan_preview": True, "execution_started": False}
        await click_text(page, "执行任务")
        await wait_for(lambda: has_text(page, "需要你确认"), "network tool approval")
        from src.agent_runtime import SessionStore
        store = SessionStore(data_dir / "sessions")
        rows = store.list()
        if not rows:
            raise RuntimeError("Approval is visible without a persisted session")
        snapshot = store.snapshot(rows[0]["session_id"])
        pending = snapshot.get("pending") or {}
        events = store.read(snapshot["session_id"])
        if snapshot.get("status") != "waiting_approval" or pending.get("capability") != "network":
            raise RuntimeError("The agent did not stop at a network approval")
        if any(e.get("type") in {"permission_granted", "tool_call"} for e in events):
            raise RuntimeError("The approval fixture unexpectedly executed a tool")
        return {"session_id": snapshot["session_id"], "status": snapshot["status"],
                "pending_tool": pending["tool"], "capability": pending["capability"],
                "approval_clicked": False, "executed_tools": 0}
    if screen == "agent-policy":
        await click_text(page, "工具权限")
        await wait_for(lambda: bool(open_dialogs(page)), "tool permission dialog")
        titles = [getattr(d.title, "value", None) for d in open_dialogs(page)]
        if titles != ["工具权限"]:
            raise RuntimeError(f"Unexpected dialog stack: {titles}")
        return {"modal_title": "工具权限", "policy_saved": False}
    return {}


def own_window(title: str) -> int:
    """Find only the random title assigned to this Page, never a generic app name."""
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    user32.FindWindowW.restype = wt.HWND
    return int(user32.FindWindowW(None, title) or 0)


def capture_window(title: str, target: Path) -> dict:
    from PIL import ImageGrab, ImageStat

    hwnd = own_window(title)
    if not hwnd:
        raise RuntimeError("The helper's uniquely titled native window was not found")
    # Pillow's HWND path captures the target window, including off-screen areas.
    # Never fall back to a desktop bounding-box capture: it can include other apps.
    image = ImageGrab.grab(window=hwnd)
    if image.width < 300 or image.height < 300:
        raise RuntimeError(f"Unexpected capture size: {image.size}")
    if max(ImageStat.Stat(image).stddev) < 3:
        raise RuntimeError("Native capture returned a near-uniform/blank image")
    image.save(target)
    return {"file": target.name, "pixel_size": list(image.size), "hwnd": hwnd}


def parse_sizes(value: str) -> list[tuple[int, int]]:
    sizes = []
    for item in value.split(","):
        try:
            width, height = (int(v) for v in item.lower().split("x"))
        except ValueError as exc:
            raise argparse.ArgumentTypeError("Use sizes such as 1440x900,1100x760") from exc
        if width < 1080 or height < 700:
            raise argparse.ArgumentTypeError("SciZhi's native minimum is 1080x700")
        sizes.append((width, height))
    return sizes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "design-previews/2026-09-16-workbench")
    parser.add_argument("--sizes", type=parse_sizes, default=parse_sizes("1440x900,1100x760"))
    parser.add_argument("--screens", default="home,agent,monitor,results,settings")
    parser.add_argument('--scratch', type=Path, default=ROOT / '_testtmp',
                        help='Isolated fixture parent directory inside this workspace')
    parser.add_argument("--settle", type=float, default=2.0,
                        help="Seconds to allow native rendering after each navigation")
    parser.add_argument('--capture-method', choices=['flutter', 'hwnd'], default='flutter',
                        help='Flutter captures this Page only, including overlays; HWND also includes window chrome.')
    parser.add_argument('--theme', choices=['dark', 'light'], default='dark',
                        help='Theme for this isolated screenshot fixture')
    parser.add_argument('--font-scale', type=float, choices=[.9, 1.0, 1.1, 1.2], default=1.0,
                        help='Reading scale for this isolated screenshot fixture')
    parser.add_argument('--ui-font-size', type=int, choices=range(12, 21), default=None,
                        help='Canonical interface size; code and terminal remain independent')
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This native HWND capture helper supports Windows only")
    screens = [s.strip() for s in args.screens.split(",") if s.strip()]
    if not screens or any(s not in SCREENS for s in screens):
        parser.error(f"--screens must use: {', '.join(SCREENS)}")
    if args.settle < 0.5:
        parser.error("--settle must be at least 0.5 seconds")
    scratch = args.scratch.resolve()
    if not scratch.is_relative_to(ROOT.resolve()):
        parser.error('--scratch must stay inside this workspace')
    from PIL import ImageGrab
    if "window" not in inspect.signature(ImageGrab.grab).parameters:
        parser.error("Pillow >= 11.2.1 with HWND capture is required")

    run_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S") + "-" + uuid.uuid4().hex[:6]
    output = args.out.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    # Keep new QA fixtures inside the explicit workspace scratch boundary so
    # clean_workspace.py can remove them after review without touching user data.
    scratch.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="scizhi-native-qa-", dir=scratch))
    os.environ["GEZHI_DATA_DIR"] = str(data_dir)
    os.environ["GEZHI_LIT_CROSSREF"] = "0"
    # This process runs fixture data only; never inherit a paid/live model route.
    for key in list(os.environ):
        if key.startswith("GEZHI_LLM_"):
            os.environ.pop(key)
    sys.path.insert(0, str(ROOT))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from scripts.demo_run import run_demo
    from src import settings
    settings.save({"enable_crossref": False, 'appearance_dark_mode': args.theme == 'dark',
                   'appearance_font_scale': args.font_scale})
    if args.ui_font_size is not None:
        settings.save({'ui_font_size': args.ui_font_size})
    demo = run_demo(DEMO_TOPIC, out_dir=data_dir / "exports" / "fixture", offline=True)
    from src.research_notebook import handle as notebook
    notebook({"cmd": "notebook_save", "record": {"kind": "hypothesis", "title": "反例检索能否减少综述中的过度归因？",
        "question": "显式检索反驳证据，是否能改善生成综述对方法局限的覆盖？", "status": "planned", "synthetic": True,
        "prediction": "相同文献池下，反例检索组遗漏的限制条件更少。", "method": "同池、同模型、固定预算；独立人工盲评。",
        "evidence": [{"relation": "challenges", "source": {"id": "fixture-observation", "synthetic": True},
                      "quote": "样例观察：增加检索不一定改善信息综合。", "scope": "synthetic_observation"}]}})
    if any(s.startswith(('project-', 'chat-')) for s in screens):
        from src.project_workspace import handle as project
        from src.research_planning import handle as planning
        folder = data_dir/'研究项目'
        folder.mkdir()
        subprocess.run(['git','-c','init.templateDir=','init','--initial-branch=main',str(folder)],
                       check=True, capture_output=True)
        (folder/'研究方案.md').write_text('# 反例检索与综述可靠性\n\n## 待验证问题\n显式寻找反驳材料，是否减少过度归因？\n\n## 对照设置\n- 固定文献池、模型与预算\n- 由研究者独立盲评\n- 保留未成功的实验\n\n> 这是界面验收材料，不是真实实验结果。',encoding='utf-8')
        (folder/'analysis.py').write_text('def coverage(found, expected):\n    return len(found & expected) / max(1, len(expected))\n',encoding='utf-8')
        p = project({'cmd':'project_open','path':str(folder),'name':'综述可靠性 · 验收项目'})['project']
        project({'cmd':'project_update','project_id':p['id'],'view':'split'})
        project({'cmd':'project_select','project_id':p['id']})
        for title, status in [('整理支持与反驳证据','done'),('冻结对照实验的评估口径','doing'),('检查随机种子与样本划分','todo')]:
            planning({'cmd':'todo_save','todo':{'project_id':p['id'],'title':title,'status':status,'priority':'normal','task_id':demo['task_id']}})

    import flet as ft
    from src.ui import desktop

    title = f"SciZhi Native QA [{run_id}]"
    report = {"run_id": run_id, "fixture": True, "data_dir": str(data_dir),
              "task_id": demo["task_id"], "source_mutated": False,
              "capture_method": args.capture_method, "captures": [],
              "theme": args.theme, "font_scale": args.font_scale,
              "status": "running"}

    def write_report() -> None:
        (output / "manifest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def wrapped(page: ft.Page) -> None:
        ui = {}
        if any(screen in {'draft-restored', 'draft-conflict', 'tool-output-read', 'tool-output-search', 'model-control', 'activity-tree', 'command-search', 'general-workspace', 'research-mode', 'mode-commands'} for screen in screens):
            previous_profile = sys.getprofile()
            wanted = ('drafts', 'agent_request', 'agent_state', 'select_agent_session', 'get_runtime', 'finish_native_close',
                      'quick_model_controls', 'activity_panel', 'agent_options', 'agent_toolbox', 'onboarding', 'tools_panel', 'agent_activity',
                      'workspace_mode_control', 'agent_pick')

            def capture_locals(frame, event, arg):
                if event == 'return' and frame.f_code is desktop.main.__code__:
                    ui.update({name: frame.f_locals[name] for name in wanted if name in frame.f_locals})

            try:
                sys.setprofile(capture_locals)
                desktop.main(page)
            finally:
                sys.setprofile(previous_profile)
            if any(name not in ui for name in wanted):
                raise RuntimeError('Desktop interaction QA dependencies were not captured')
        else:
            desktop.main(page)
        page.enable_screenshots = True
        page.title = title
        page.window.maximized = False
        page.window.left = 24
        page.window.top = 24
        page.update()

        async def capture_suite() -> None:
            try:
                await asyncio.sleep(3)
                for width, height in args.sizes:
                    page.window.maximized = False
                    page.window.width, page.window.height = width, height
                    page.update()
                    await asyncio.sleep(args.settle)
                    for screen in screens:
                        await close_own_dialogs(page)
                        if screen in {"monitor", "results", "research-audit"}:
                            await click_text(page, DEMO_TOPIC)
                        await navigate(page, SCREENS[screen])
                        await asyncio.sleep(.35)
                        interaction = await prepare_state(page, screen, data_dir, ui=ui)
                        await asyncio.sleep(args.settle)
                        navigation_target = interaction.get('navigation_target', SCREENS[screen])
                        expected_breadcrumb = f"工作台  /  {navigation_target}"
                        if not has_text(page, expected_breadcrumb):
                            raise RuntimeError(f"Navigation changed before capture: {screen}")
                        target = output / f"{width}x{height}-{screen}.png"
                        if args.capture_method == 'hwnd':
                            captured = await asyncio.to_thread(capture_window, title, target)
                        else:
                            from PIL import Image, ImageStat
                            png = await asyncio.wait_for(page.take_screenshot(pixel_ratio=1), timeout=20)
                            rendered = Image.open(io.BytesIO(png))
                            if rendered.width < 300 or rendered.height < 300 or max(ImageStat.Stat(rendered).stddev) < 3:
                                raise RuntimeError('Flutter returned a blank or undersized image')
                            target.write_bytes(png)
                            captured = {'file':target.name, 'pixel_size':list(rendered.size)}
                        captured.update({"screen": screen, "requested_size": [width, height],
                                         "page_size": [page.width, page.height],
                                         "navigation_target": navigation_target,
                                         "breadcrumb": expected_breadcrumb,
                                         "interaction": interaction})
                        report["captures"].append(captured)
                        write_report()
                        print(f"Captured {target}", flush=True)
                report["status"] = "captured"
            except Exception as exc:
                report["status"] = "failed"
                report["error"] = f"{type(exc).__name__}: {exc}"
                print(report["error"], file=sys.stderr, flush=True)
            finally:
                write_report()
                # Close only this Page's native window. No taskkill/process scan.
                try:
                    await close_capture_page(page, ui)
                except RuntimeError as exc:
                    if "Session closed" not in str(exc):
                        raise

        page.run_task(capture_suite)

    write_report()
    ft.run(wrapped)
    print(f"Capture manifest: {output / 'manifest.json'}", flush=True)
    # Retain this isolated fixture for inspection until workspace cleanup.
    return 0 if report["status"] == "captured" else 1


if __name__ == "__main__":
    raise SystemExit(main())
