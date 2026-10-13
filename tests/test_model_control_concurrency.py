"""Competing clients cannot accept the same obsolete model preview twice."""
import multiprocessing
from pathlib import Path
from unittest import mock

from src import settings
from src.model_controls import model_control_catalog


def apply_from_process(path, revision, ready, release, results, effort):
    from src import settings
    from src.model_controls import model_control_apply
    settings._PATH = Path(path)
    settings.apply_at_startup()
    ready.put(True)
    release.wait(10)
    try:
        model_control_apply("planning", "reader", revision, effort=effort)
        results.put("saved")
    except ValueError:
        results.put("stale")


def test_two_clients_one_saved_revision(tmp_path):
    path = tmp_path / "settings.json"
    saved_config = {"llm_model": "", "llm_base_url": "", "llm_api_key": "", "model_connections": [
        {"id": "local", "base_url": "http://localhost:11434", "protocol": "ollama"}], "model_profiles": [
        {"id": "reader", "model": "reader", "connection_id": "local", "thinking_style": "ollama_think"}]}
    with mock.patch.object(settings, "_PATH", path):
        settings.save({**settings._DEFAULTS, **saved_config})
        revision = model_control_catalog()["revision"]
        context = multiprocessing.get_context("spawn")
        ready, results, release = context.Queue(), context.Queue(), context.Event()
        children = [context.Process(target=apply_from_process, args=(str(path), revision, ready, release, results, value)) for value in ("low", "high")]
        try:
            for child in children:
                child.start()
            for _ in children:
                ready.get(timeout=15)
            release.set()
            outcome = sorted(results.get(timeout=15) for _ in children)
            assert outcome == ["saved", "stale"]
        finally:
            for child in children:
                child.join(15)
                if child.is_alive():
                    child.terminate()
                    child.join(5)
            for queue in (ready, results):
                queue.close()
                queue.join_thread()
