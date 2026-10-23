"""Desktop entry point and a windowless verification mode for shipped builds."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

from src.version import APP_VERSION

EXPECTED_VERSION = APP_VERSION
REQUIRED_RESOURCES = (
    "workflows/research_pipeline.yaml", "workflows/literature_scan.yaml", "workflows/evidence_review.yaml",
    "skills/research-planning/SKILL.md", "skills/evidence-audit/SKILL.md", "skills/lit-critique/SKILL.md",
    "skills/academic-writing/SKILL.md", "skills/experiment-review/SKILL.md",
    "plugins/bibtex_export.py", "plugins/term_freq.py",
    "assets/gezhi.ico", "assets/gezhi.png", "assets/gezhi.svg", "assets/gezhi-wordmark.png",
    "assets/research-study.svg", "assets/remote-control.html", "assets/ui-design.json", "LICENSE", "NOTICE",
)


def _imports():
    # Static imports remain visible to PyInstaller's dependency analysis.
    # They must happen AFTER the self-check selects its temporary data root.
    from src import agent_launch, config, swarm
    from src.agent_runtime import AgentRuntime
    from src import session_controls, session_events, session_projection
    from src.ui import desktop
    if not all(callable(operation) for operation in (session_controls.session_status,
            session_controls.compact_session, session_events.read_event_page, session_projection.read_projection)):
        raise ImportError('Required session display/control operations are unavailable')
    return desktop, swarm, agent_launch, config, AgentRuntime


def _markdown_code_preview():
    """Exercise bundled Markdown and code controls without a page or clipboard."""
    import flet as ft
    from src.ui.code_preview import CodePreview
    from src.ui.markdown_view import MarkdownView

    code = 'value = "证据"\nprint(value)\n'
    source = f'Before code.\n\n```python\n{code}```\n\nAfter code.\n'
    options = {'code_font_size': 14, 'code_show_line_numbers': True, 'code_wrap_long_lines': True}
    view = MarkdownView(source, selectable=True, options=options, dark=False)
    if (view.value != source or len(view.controls) != 3
            or not isinstance(view.controls[0], ft.Markdown)
            or not isinstance(view.controls[1], CodePreview)
            or not isinstance(view.controls[2], ft.Markdown)):
        raise ValueError('Markdown prose and fenced code did not produce native controls')
    preview = view.controls[1]

    def check_source():
        displayed = [''.join(span.text for span in line.spans) for line in preview.code_lines]
        if preview.source != code or preview.language != 'python' or displayed != code.splitlines():
            raise ValueError('Code preview changed the source text')

    check_source()
    if ([row.controls[0].value for row in preview.rows] != ['1', '2']
            or not all(row.controls[0].visible for row in preview.rows)
            or any(line.no_wrap for line in preview.code_lines)):
        raise ValueError('Code preview did not enable line numbers and wrapping')
    button = preview.copy_button
    if (not isinstance(button, ft.TextButton) or not callable(button.on_click)
            or button.disabled or button.content != '复制代码'
            or not any(item is button for item in preview.header.controls)
            or not any(item is preview.header for item in preview.content.controls)):
        raise ValueError('Code preview copy button is missing or unavailable')
    view.refresh_appearance({**options, 'code_show_line_numbers': False, 'code_wrap_long_lines': False}, dark=True)
    check_source()
    if (any(row.controls[0].visible for row in preview.rows)
            or not all(line.no_wrap for line in preview.code_lines)
            or not isinstance(preview.code_body, ft.Row) or preview.code_body.scroll != ft.ScrollMode.AUTO):
        raise ValueError('Code preview did not disable line numbers and enable horizontal scrolling')
    return {'code_lines': len(preview.code_lines), 'source_preserved': True,
            'line_number_toggle': True, 'wrap_toggle': True, 'copy_button': True}


@contextmanager
def _offline_network():
    """Block sockets in this standalone self-check process and count attempts."""
    import socket
    previous = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo, socket.socket.sendto
    state = {'attempts': 0}

    def denied(*args, **kwargs):
        state['attempts'] += 1
        raise RuntimeError('Network is disabled during release self-check')

    socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = socket.socket.sendto = denied
    try:
        yield state
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo, socket.socket.sendto = previous


def self_check(output_path: Path | str, *, resource_root: Path | None = None,
               frozen: bool | None = None) -> int:
    """Verify imports, bundled files and planning with no GUI or provider calls.

    This is a process entry mode. If application modules were already imported
    with a different data root, fail the isolation check instead of touching
    that existing workspace.
    """
    output_path = Path(output_path).resolve()
    resource_root = Path(resource_root or getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    report = {"ok": False, "version": EXPECTED_VERSION, "frozen": frozen, "checks": [], "error": ""}

    def check(name, operation):
        try:
            detail = operation()
            report["checks"].append({"name": name, "ok": True, "detail": detail})
            return True
        except Exception as exc:
            report["checks"].append({"name": name, "ok": False,
                                     "error": f"{type(exc).__name__}: {exc}"})
            return False

    def read_resource(relative):
        with (resource_root / relative).open("rb") as stream:
            if not stream.read(1):
                raise ValueError("Bundled resource is empty")
        return "readable"

    def check_client():
        path = resource_root / "flet_desktop" / "app" / "flet-windows.zip"
        with zipfile.ZipFile(path) as archive:
            if not any(name.replace("\\", "/").endswith("flet.exe") for name in archive.namelist()):
                raise ValueError("Native desktop archive has no flet.exe")
        return "native desktop archive readable"

    fixture_environment = {
        'GEZHI_LLM_BASE_URL': 'http://127.0.0.1:9/v1', 'GEZHI_LLM_API_KEY': 'self-check-secret',
        'GEZHI_LLM_MODEL': 'self-check-model', 'GEZHI_LLM_MODEL_FAST': 'self-check-model',
        'GEZHI_LLM_MODEL_STRONG': 'self-check-model', 'GEZHI_LLM_PROTOCOL': 'chat_completions',
        'GEZHI_LLM_AUTH_STYLE': 'bearer', 'GEZHI_EMB_BASE_URL': 'http://127.0.0.1:9/v1',
        'GEZHI_EMB_API_KEY': 'self-check-embedding-secret', 'GEZHI_EMB_MODEL': 'self-check-embedding',
    }
    previous_environment = {key: os.environ.get(key) for key in ('GEZHI_DATA_DIR', *fixture_environment)}
    try:
        with tempfile.TemporaryDirectory(prefix="scizhi-self-check-") as isolated, _offline_network() as network:
            data_root = Path(isolated)
            os.environ["GEZHI_DATA_DIR"] = str(data_root)
            os.environ.update(fixture_environment)
            loaded = []
            imported = check("imports", lambda: loaded.extend(_imports()) or
                             "desktop, swarm, agent_launch, config and AgentRuntime imported")
            for relative in REQUIRED_RESOURCES:
                check(f"resource:{relative}", lambda relative=relative: read_resource(relative))
            if frozen:
                check("frozen_desktop_client", check_client)
            if imported:
                desktop, swarm, agent_launch, config, runtime_type = loaded

                def version():
                    actual = getattr(desktop, "APP_VERSION", None)
                    report["version"] = actual
                    if actual != EXPECTED_VERSION:
                        raise ValueError(f"Expected {EXPECTED_VERSION}; found {actual}")
                    if not callable(getattr(agent_launch, "AgentLauncher", None)):
                        raise ValueError("AgentLauncher is unavailable")
                    return actual

                def isolation():
                    if config.DATA_DIR.resolve() != data_root.resolve():
                        raise ValueError("Application was already imported outside the isolated data root")
                    return "temporary data root active"

                check("version", version)
                if check("data_isolation", isolation):
                    check('markdown_code_preview', _markdown_code_preview)
                    runtime = runtime_type(session_root=data_root / "sessions", offline=True, llm_enabled=False)

                    def plan():
                        value = runtime.plan("Inspect the self-check workspace", mode="plan", agent="explore")
                        if value.get("mode") != "plan" or not value.get("steps") or runtime.sessions.list():
                            raise ValueError("Read-only planning did not preserve its execution boundary")
                        return "offline read-only plan created; no tools executed"

                    def swarm_plan_cancel():
                        coordinator = swarm.SwarmCoordinator(runtime, root=data_root / "swarms")
                        value = coordinator.plan("Self-check plan", branches=[{
                            "agent": "explore", "request": "Inspect the self-check workspace"}], max_parallel=1)
                        if value.get("status") != "planned":
                            raise ValueError("Swarm plan was not persisted")
                        cancelled = coordinator.cancel(value["plan_id"])
                        if cancelled.get("status") != "cancelled" or runtime.sessions.list():
                            raise ValueError("Cancelling an unstarted Swarm unexpectedly executed work")
                        return "Swarm plan persisted and cancelled; no tools executed"

                    check("runtime_plan", plan)
                    check("swarm_plan_cancel", swarm_plan_cancel)

                    def execution_plan_check():
                        from src.agent_runtime import ToolCall, SessionStore
                        from src.execution_plan import read_plan, completion_issue
                        sid = 'selfcheck-execution-plan'
                        runtime.sessions.append(sid, 'user_message', {'text': 'Inspect saved workspace'})
                        runtime.sessions.append(sid, 'turn_started', {'turn_id': 'selfcheck-plan-turn'})
                        step = {'id': 'inspect', 'title': '读取工作区', 'criterion': '关联实际读取回执',
                                'kind': 'execution', 'status': 'in_progress', 'evidence': []}
                        def update(call_id, revision, value):
                            return runtime._execute_call(sid, ToolCall(call_id, 'plan.update', {
                                'expected_turn_id': 'selfcheck-plan-turn', 'expected_revision': revision,
                                'steps': [value]}, 'write'))
                        if update('plan-create', 0, step).get('ok') is not True:
                            raise ValueError('Execution plan creation failed')
                        if not completion_issue(runtime.sessions, sid):
                            raise ValueError('Open execution plan was reported complete')
                        fake = update('plan-fake', 1, {**step, 'status': 'completed', 'evidence': ['missing-receipt']})
                        if fake.get('ok') is not False or read_plan(runtime.sessions, sid)['revision'] != 1:
                            raise ValueError('Fabricated completion evidence was accepted')
                        observed = runtime._execute_call(sid, ToolCall('plan-inspect', 'workspace.read', {}, 'read'))
                        if observed.get('ok') is False:
                            raise ValueError('Workspace evidence read failed')
                        finished = update('plan-finish', 1, {**step, 'status': 'completed', 'evidence': ['plan-inspect']})
                        if finished.get('ok') is not True:
                            raise ValueError('Genuine execution evidence was rejected')
                        reopened = read_plan(SessionStore(runtime.sessions.root), sid)
                        if not reopened['ready'] or reopened['completed'] != 1:
                            raise ValueError('Execution plan did not survive reopening the journal')
                        runtime.sessions.append(sid, 'completed', {'status': 'done'})
                        return {'persisted': True, 'real_tool_receipt': True, 'fake_evidence_rejected': True,
                                'model_calls': 0}

                    check('execution_plan', execution_plan_check)

                    def outcome_check():
                        from src.outcome_checks import run as run_outcome_checks
                        empty = run_outcome_checks({}, {}, {})
                        if empty["verdict"] == "all_satisfied":
                            raise ValueError("Empty evidence was reported as a satisfied outcome")
                        states = {item["state"] for item in empty["items"]}
                        if not states <= {"satisfied", "failed", "stale", "pending", "unknown"}:
                            raise ValueError("Outcome checks produced an undocumented state")
                        # A material that changed after it was recorded must become stale.
                        import hashlib
                        from src import config
                        sample = Path(config.DATA_DIR) / "selfcheck-material.txt"
                        sample.parent.mkdir(parents=True, exist_ok=True)
                        sample.write_text("first", encoding="utf-8")
                        digest = hashlib.sha256(sample.read_bytes()).hexdigest()
                        artifacts = {"lit_filter": {"pool": [
                            {"id": "M1", "title": "material", "sha256": digest,
                             "path": str(sample)}]}}
                        settled = run_outcome_checks(artifacts, {}, {})
                        material = next(item for item in settled["items"]
                                        if item["id"] == "material:integrity")
                        if material["state"] != "satisfied":
                            raise ValueError("Unchanged material was not verified")
                        sample.write_text("second", encoding="utf-8")
                        changed = run_outcome_checks(artifacts, {}, {})
                        material = next(item for item in changed["items"]
                                        if item["id"] == "material:integrity")
                        if material["state"] != "stale":
                            raise ValueError("Changed material was not reported as stale")
                        from src import outcome_review, service
                        from src.orchestrator.engine import TaskContext
                        review_ctx = TaskContext('selfcheck-outcome-review', 'research_pipeline',
                                                 {'topic': 'isolated review check'})
                        review_ctx.artifacts = artifacts
                        service._tasks[review_ctx.task_id] = review_ctx
                        try:
                            revision = service.outcome_checks(review_ctx.task_id)['revision']
                            outcome_review.record(review_ctx.task_id, 'self-check', 'needs changes',
                                                  decision='changes_requested', expected_revision=revision)
                            rejected = service.outcome_checks(review_ctx.task_id)
                            if next(item['state'] for item in rejected['items']
                                    if item['id'] == 'review:manual') != 'failed':
                                raise ValueError('Requested changes were reported as approval')
                            outcome_review.record(review_ctx.task_id, 'self-check', 'checked this version',
                                                  decision='approved', expected_revision=revision)
                            review_ctx.artifacts['extract'] = {'digest_md': 'a new deliverable'}
                            revised = service.outcome_checks(review_ctx.task_id)
                            if next(item['state'] for item in revised['items']
                                    if item['id'] == 'review:manual') != 'stale':
                                raise ValueError('Old approval survived a changed deliverable')
                            try:
                                outcome_review.record(review_ctx.task_id, 'self-check', 'stale form',
                                                      decision='approved', expected_revision=revision)
                            except outcome_review.ReviewError as error:
                                if error.code != 'stale_revision':
                                    raise
                            else:
                                raise ValueError('A stale review was accepted')
                        finally:
                            service._tasks.pop(review_ctx.task_id, None)
                            sample.unlink(missing_ok=True)
                        return {"items": settled["total"], "verdict": settled["verdict"],
                                "stale_detected": True, "review_version_bound": True,
                                "model_calls": 0}

                    check('outcome_checks', outcome_check)


                    def event_projection():
                        store = runtime.sessions
                        sid = 'selfcheck-events'
                        store.append(sid, 'user_message', {'text': 'Isolated event check', 'mode': 'agent'})
                        if store.snapshot(sid)['status'] != 'running':
                            raise ValueError('Initial session projection failed')
                        first = store.event_page(sid, limit=1, tail=True)
                        other = type(store)(store.root)
                        other.append(sid, 'assistant_message', {'content': '中文增量验证'})
                        other.append(sid, 'completed', {'status': 'done'})
                        page = store.event_page(sid, cursor=first['cursor'])
                        if (page['reset'] or page['has_more'] or len(page['events']) != 2
                                or page['events'][0]['payload']['content'] != '中文增量验证'
                                or store.snapshot(sid)['status'] != 'done'):
                            raise ValueError('Incremental events or cross-store projection failed')
                        if store.event_page(sid, cursor=page['cursor'])['events']:
                            raise ValueError('Idle cursor repeated previously delivered events')
                        return 'UTF-8 event pages, cross-store projection and idle cursor verified'

                    def session_controls():
                        from src.bridge import handle
                        from src.state_store import write_json
                        sid = 'selfcheck-context'
                        store = runtime.sessions
                        store.append(sid, 'user_message', {'text': 'Self-check context', 'mode': 'agent'})
                        store.append(sid, 'driver_started', {'driver': 'adaptive'})
                        store.append(sid, 'completed', {'status': 'done'})
                        messages = [{'role': 'system', 'content': 'Self-check only.'},
                                    {'role': 'user', 'content': 'Self-check context'}]
                        for index in range(10):
                            messages.extend([{'role': 'user', 'content': f'Question {index} ' + 'evidence ' * 200},
                                             {'role': 'assistant', 'content': f'Observation {index} ' + 'checked ' * 200}])
                        write_json(store.root / f'{sid}.driver.json', {'schema_version': 1, 'session_id': sid,
                            'status': 'done', 'messages': messages, 'original_request': 'Self-check context',
                            'tokens': 0, 'model_calls': 0, 'steps': 0, 'usage_complete': True,
                            'max_steps': 16, 'token_budget': 1000, 'context_limit_tokens': 32000,
                            'compressions': 0, 'queue': [], 'pending': None, 'active_calls': [],
                            'model_identity': {'model': 'offline-self-check', 'protocol': 'chat_completions'}})
                        status = handle({'cmd': 'session_status', 'session_id': sid})
                        if not status.get('ok') or not status['status']['can_compact']:
                            raise ValueError('Session status did not expose the isolated saved driver')
                        compact = handle({'cmd': 'session_compact', 'session_id': sid})
                        result = compact.get('result') or {}
                        if (not compact.get('ok') or not result.get('changed') or result.get('model_calls') != 0
                                or result['after_tokens'] >= result['before_tokens']
                                or compact['status']['context']['compressions'] != 1):
                            raise ValueError('Offline context compaction contract failed')
                        events = handle({'cmd': 'session_events', 'session_id': sid, 'limit': 1, 'tail': True})
                        if not events.get('ok') or events['events'][-1]['type'] != 'context_compressed':
                            raise ValueError('Compaction audit event was not available through the bridge')
                        return 'session_status/session_compact/session_events verified; zero model calls'

                    check('session_event_projection', event_projection)
                    check('session_status_compact', session_controls)

                    def product_readiness():
                        from src.bridge import handle
                        response = handle({'cmd': 'product_readiness'})
                        value = response.get('readiness') or {}
                        serialized = json.dumps(value, ensure_ascii=False)
                        if (not response.get('ok') or value.get('version') != EXPECTED_VERSION
                                or not value.get('research', {}).get('offline_available')
                                or value.get('model', {}).get('connection_verified') is not False
                                or value.get('model', {}).get('status') != 'configured'):
                            raise ValueError('First-run readiness did not match the configured offline fixture')
                        if any(secret in serialized for secret in ('self-check-secret', 'self-check-embedding-secret',
                                                                  '127.0.0.1', 'api_key', 'base_url', 'credential_fingerprint')):
                            raise ValueError('First-run readiness exposed private configuration')
                        return 'version and local readiness verified; credentials/endpoints omitted'

                    def product_demo():
                        from src import service
                        from src.bridge import handle
                        response = handle({'cmd': 'product_demo'})
                        sid = response.get('task_id')
                        if not response.get('ok') or response.get('offline') is not True or not sid:
                            raise ValueError('Offline demo did not create a task')
                        checkpoint = config.CHECKPOINT_DIR / f'{sid}.json'
                        deadline = time.monotonic() + 30
                        completed = False
                        while time.monotonic() < deadline:
                            task = service.get(sid)
                            if task and task.status in {'failed', 'cancelled', 'waiting_human'}:
                                raise ValueError(f'Offline demo stopped with status {task.status}')
                            if task and task.status == 'done':
                                try:
                                    completed = json.loads(checkpoint.read_text(encoding='utf-8')).get('status') == 'done'
                                except (OSError, ValueError):
                                    pass
                                if completed:
                                    break
                            time.sleep(.02)
                        if not completed:
                            raise ValueError('Offline demo did not finish within the self-check deadline')
                        artifacts = service.artifacts_of(sid)
                        if not artifacts.get('pool') or not artifacts.get('citations'):
                            raise ValueError('Offline demo finished without literature and citation artifacts')
                        return {'status': 'done', 'pool_count': len(artifacts['pool']),
                                'offline': True, 'synthetic_example': True, 'configured_endpoints_unused': True}

                    check('product_readiness', product_readiness)
                    check('product_demo_offline', product_demo)

                    def pdf_backend():
                        import io
                        import pypdf
                        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
                        from src import pdf_text
                        if pdf_text.backend() != 'pypdf':
                            raise ValueError('The preferred pypdf backend is missing from this build')
                        writer = pypdf.PdfWriter()
                        page = writer.add_blank_page(width=200, height=200)
                        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
                        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                            DictionaryObject({NameObject('/F1'): font})})
                        content = DecodedStreamObject()
                        content.set_data(b'BT /F1 12 Tf 10 100 Td (SciZhi PDF self-check) Tj ET')
                        page.replace_contents(content.flate_encode())
                        payload = io.BytesIO()
                        writer.write(payload)
                        result = pdf_text.extract(payload.getvalue())
                        if (not result.get('ok') or result.get('method') != 'pypdf'
                                or 'SciZhi PDF self-check' not in result.get('text', '')):
                            raise ValueError('Bundled pypdf did not extract the in-memory test document')
                        return {'backend': result['method'], 'version': pypdf.__version__, 'pages': result['pages']}

                    check('pdf_backend', pdf_backend)

                    from src import release_experiments
                    for name, operation in (
                        ('experimental_defaults', release_experiments.defaults),
                        ('experimental_queue', release_experiments.queue),
                        ('experimental_steering', release_experiments.steering),
                        ('evidence_review_inbox', release_experiments.review),
                        ('history_index', release_experiments.history),
                    ):
                        check(name, operation)

                    from src import release_experience
                    for name, operation in release_experience.checks(runtime, data_root):
                        check(name, operation)

            def no_network():
                if network['attempts']:
                    raise ValueError(f"Blocked unexpected network attempts: {network['attempts']}")
                return 'socket connections disabled; no network attempts'

            check('network_isolation', no_network)
    except Exception as exc:
        report["checks"].append({"name": "self_check", "ok": False,
                                 "error": f"{type(exc).__name__}: {exc}"})
    finally:
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    failures = [item["name"] for item in report["checks"] if not item["ok"]]
    report["ok"] = bool(report["checks"]) and not failures
    report["error"] = "; ".join(failures)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return 1
    return 0 if report["ok"] else 1


def launch(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--self-check":
        return self_check(args[1]) if len(args) == 2 else 2
    import flet as ft
    from src.ui.desktop import main
    ft.run(main)
    return 0

if __name__ == "__main__":
    raise SystemExit(launch())
