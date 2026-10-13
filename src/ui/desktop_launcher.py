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
    "assets/research-study.svg", "assets/remote-control.html", "LICENSE", "NOTICE",
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
