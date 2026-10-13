"""Isolated Flet Python session benchmarks; excludes Flutter rendering and IPC.

python scripts/benchmark_gui_sessions.py --sizes 10000 20000 --repeats 3 --out report.json

Measures real SessionComposer callbacks against temporary JSONL and chat-index
files. Legacy and cursor providers run in the same process with identical data.
The desktop callback is extracted from its AST, not reimplemented for this test.
Only page.update and window integration are replaced by no-op page methods.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import ExitStack
from datetime import datetime, timezone
import gc
from importlib.metadata import version
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import flet as ft  # noqa: E402
from src import bridge, config, settings, session_queue  # noqa: E402
from src.agent_runtime import SessionStore  # noqa: E402
from src.ui import brand, session_composer  # noqa: E402
from src.ui.control_tower import STATUS  # noqa: E402
from src.ui.session_status import SessionStatusPanel  # noqa: E402
from src.ui.session_queue import SessionQueuePanel  # noqa: E402
from src.ui.onboarding import OnboardingPanel  # noqa: E402


def fixture(root: Path, count: int) -> tuple[SessionStore, list[dict[str, Any]]]:
    events = []
    for index in range(count):
        user = index % 2 == 0
        events.append({
            'type': 'user_message' if user else 'assistant_message', 'session_id': 'bench',
            'timestamp': '2026-09-24T12:00:00+0000',
            'payload': {'text': f'Research question {index}'} if user else
                       {'content': f'Evidence answer {index}', 'step': 1},
        })
    store = SessionStore(root / 'sessions')
    store._path('bench').write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
    (root / 'chat_index.json').write_text(json.dumps({'sessions': {'bench': {
        'session_id': 'bench', 'title': 'Long research benchmark', 'archived': False,
        'attachments': [], 'project_id': '', 'created_at': events[0]['timestamp'],
    }}}), encoding='utf-8')
    # A realistic retained active context is separate from the 10k/20k audit
    # history. The current session panel must derive actual model/budget data.
    messages = [{'role': 'system', 'content': 'Preserve research evidence.'},
                {'role': 'user', 'content': 'Research question 0'}]
    messages.extend({'role': 'user' if event['type'] == 'user_message' else 'assistant',
        'content': str(event['payload'].get('text') or event['payload'].get('content') or '')}
        for event in events[-400:])
    (store.root / 'bench.driver.json').write_text(json.dumps({
        'schema_version': 1, 'session_id': 'bench', 'status': 'running', 'messages': messages,
        'model_identity': {'model': 'benchmark-recorded-model', 'protocol': 'responses'},
        'tokens': 12000, 'model_calls': 4, 'steps': 4, 'max_steps': 16,
        'token_budget': 100000, 'context_limit_tokens': 128000, 'compressions': 2,
        'usage_complete': True, 'queue': [], 'unresolved': {},
        'turn_usage': [{'tokens': 23000, 'model_calls': 6, 'usage_complete': True}],
    }), encoding='utf-8')
    return store, events


def fake_page():
    dialogs = []
    return SimpleNamespace(update=lambda: None, show_dialog=dialogs.append, dialogs=dialogs,
                           services=[], run_thread=lambda fn: fn(), pop_dialog=lambda: None)


def composer(store: SessionStore, mode: str, *, callback=None):
    kwargs = ({'events_provider': store.read} if mode == 'legacy' else
              {'event_page_provider': store.event_page, 'events_changed': callback})
    return session_composer.SessionComposer(fake_page(), ft.TextField(), lambda: 'bench',
        lambda: '', lambda sid: None, lambda: None, **kwargs)


def elapsed(fn) -> float:
    started = time.perf_counter()
    fn()
    return round((time.perf_counter() - started) * 1000, 3)


def desktop_scope(store: SessionStore):
    """Compile the two production callbacks with their real Flet dependencies."""
    tree = ast.parse((ROOT / 'src/ui/desktop.py').read_text(encoding='utf-8'))
    wanted = {'render_events_log', 'select_agent_session', 'observe_session_status', 'refresh_agent_actions'}
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in wanted]
    scope = {'ft': ft, 'brand': brand, 'ROSTER': {}, 'Any': Any, 'SESSION_STATUS': STATUS,
             'get_runtime': lambda sid: SimpleNamespace(sessions=store),
             'agent_state': {'session_id': 'bench', 'view_epoch': 0},
             'agent_log_col': ft.Column(), 'agent_log_wrap': ft.Container(),
             'agent_empty': ft.Container(), 'agent_approval_card': ft.Container(),
             'agent_plan_card': ft.Container(), 'agent_request': ft.TextField(),
             'agent_pick': ft.Dropdown(), 'agent_mode': ft.Dropdown(),
             'agent_run_btn': ft.FilledButton(), 'agent_mode_help': ft.Text(),
             'agent_continue_btn': ft.TextButton(), 'agent_cancel_btn': ft.TextButton(),
             'agent_busy': ft.ProgressRing(visible=False),
             'agent_answer': ft.Markdown(), 'agent_session_label': ft.Text(),
             'render_plan': lambda value: None, 'render_approval': lambda *args: None,
             'close_approval_dialog': lambda: None, 'page': fake_page()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 'desktop-production-callbacks', 'exec'), scope)
    scope['composer'] = composer(store, 'paged', callback=scope['render_events_log'])
    def status_api(request):
        if request.get('cmd') != 'session_status':
            raise ValueError('This benchmark only observes session state')
        with patch.object(bridge, '_runtime', return_value=SimpleNamespace(sessions=store)):
            return bridge.handle(request)
    scope['session_status'] = SessionStatusPanel(scope['page'], lambda: scope['agent_state']['session_id'],
        epoch=lambda: scope['agent_state']['view_epoch'], busy=lambda: scope['agent_busy'].visible,
        api=status_api, on_status=scope['observe_session_status'])
    scope['onboarding'] = OnboardingPanel(scope['page'], scope['agent_request'],
        open_models=lambda: None, open_demo=lambda _: None,
        selected=lambda: scope['agent_state']['session_id'])
    scope['session_queue'] = SessionQueuePanel(scope['page'], scope['agent_request'],
        lambda: scope['agent_state']['session_id'], epoch=lambda: scope['agent_state']['view_epoch'])
    return scope


def trial(count: int, mode: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='scizhi-gui-benchmark-') as directory:
        root = Path(directory)
        store, events = fixture(root, count)
        view = composer(store, mode)
        with patch.object(config, 'DATA_DIR', root), patch.object(settings, '_PATH', root / 'settings.json'), ExitStack() as cleanup:
            cleanup.callback(session_queue.close)
            settings.save({'experimental_message_queue': False, 'llm_enabled': False,
                           'mcp_enabled': False, 'plugins_enabled': False})
            sample = {'jsonl_bytes': store._path('bench').stat().st_size,
                      'initial_refresh_ms': elapsed(view.refresh)}
            sample['initial_controls'] = len(view.transcript.controls)
            sample['idle_poll_ms'] = elapsed(view.consume_events)
            store.append('bench', 'user_message', {'text': 'Follow-up question'})
            store.append('bench', 'assistant_message', {'content': 'Follow-up answer', 'step': 1})
            sample['update_poll_ms'] = elapsed(view.consume_events)
            sample['updated_controls'] = len(view.transcript.controls)
            if mode == 'paged':
                sample['history_open_ms'] = elapsed(view.show_history)
                dialog = view.page.dialogs[-1]
                search = dialog.content.content.controls[0].controls[0]
                search.value = f'Research question {count - 2}'
                sample['history_search_ms'] = elapsed(lambda: search.on_submit(None))
                sample['history_match_label'] = dialog.content.content.controls[2].value
                # Fresh store: snapshot() must read/project once, as on first
                # opening a session after process startup, before the tail view.
                scope = desktop_scope(SessionStore(root / 'sessions'))
                sample['select_session_cold_ms'] = elapsed(lambda: scope['select_agent_session']('bench'))
                sample['status_context_tokens'] = scope['session_status'].status['context']['tokens']
                sample['status_refresh_ms'] = elapsed(lambda: scope['session_status'].refresh(force=True))
                sample['selected_log_controls'] = len(scope['agent_log_col'].controls)
                scope['agent_state'].pop('log_view', None)
                sample['log_render_ms'] = elapsed(lambda: scope['render_events_log'](events))
                sample['log_controls'] = len(scope['agent_log_col'].controls)
        return sample


def run(sizes: list[int], repeats: int) -> dict[str, Any]:
    result = {
        'schema_version': 2, 'measured_at': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(), 'platform': platform.platform(), 'flet': version('flet'),
        'scope': 'Flet Python controls + real local JSONL/metadata I/O; no Flutter frame, IPC, model or network measurement',
        'method': 'Same process, identical per-trial temporary fixtures, perf_counter milliseconds; median of recorded samples. OS filesystem cache is not cleared. Current desktop selection includes real SessionStatusPanel, SessionQueuePanel (experiment disabled), onboarding context and bridge derivation, with UI worker dispatch executed immediately in this benchmark.',
        'status_fixture': 'Saved adaptive model identity, 402 retained active messages (or fewer for smoke fixtures), current-turn and prior-turn reported usage; JSONL audit history has the requested event count.',
        'repeats': repeats, 'results': [],
        'prechange_single_observation': {
            'note': 'One pre-edit observation with predecoded composer input and no-op page.update; not a statistical baseline. Desktop log values measure the original unbounded renderer.',
            '10000': {'composer_refresh_ms': 660.216, 'log_render_ms': 1195.381, 'log_controls': 10000, 'idle_full_read_ms': 22.668},
            '20000': {'composer_refresh_ms': 1447.512, 'log_render_ms': 2668.859, 'log_controls': 20000, 'idle_full_read_ms': 44.276},
        },
    }
    for count in sizes:
        row = {'events': count, 'providers': {}}
        for mode in ('legacy', 'paged'):
            samples = []
            for _ in range(repeats):
                gc.collect()
                samples.append(trial(count, mode))
            medians = {key: round(statistics.median(sample[key] for sample in samples), 3)
                       for key in samples[0] if key.endswith('_ms')}
            row['providers'][mode] = {'median_ms': medians, 'samples': samples}
        result['results'].append(row)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sizes', type=int, nargs='+', default=[10000, 20000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.repeats < 1 or any(size < 2 or size % 2 for size in args.sizes):
        parser.error('repeats must be positive and sizes must be positive even event counts')
    result = run(args.sizes, args.repeats)
    destination = args.out.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(destination), 'results': [
        {'events': row['events'], 'providers': {name: data['median_ms'] for name, data in row['providers'].items()}}
        for row in result['results']]}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
