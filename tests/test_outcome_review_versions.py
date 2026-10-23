"""Human reviews apply to the exact observed outcome, including local files."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src import bridge, config, outcome_checks, service, settings
from src.orchestrator import engine
from src.orchestrator.engine import TaskContext


@pytest.fixture
def task_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings, "_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(engine, "CHECKPOINT_DIR", tmp_path / "checkpoints")

    def create(task_id="reviewed-task"):
        material = tmp_path / f"{task_id}.txt"
        material.write_text("original material", encoding="utf-8")
        ctx = TaskContext(task_id, "research_pipeline", {"topic": "Original topic"})
        ctx.status = "done"
        ctx.artifacts = {
            "lit_filter": {"pool": [{"id": "W1", "title": "Paper", "path": str(material),
                                      "sha256": hashlib.sha256(material.read_bytes()).hexdigest()}]},
            "extract": {"digest_md": "Original summary", "records": []},
            "review_frame": {"outline_md": "Original outline", "sections": []},
            "citation": {"entries": [{"title": "Paper"}]},
        }
        monkeypatch.setitem(service._tasks, task_id, ctx)
        return ctx

    return create


def _checks(ctx):
    return service.outcome_checks(ctx.task_id)


def _revision(ctx):
    return _checks(ctx).get("revision", "0" * 64)


def _review(ctx, *, decision="approved", revision=None, note="Checked the material"):
    return bridge.handle({"cmd": "outcome_review", "task_id": ctx.task_id,
                          "record": {"by": "Reviewer", "note": note, "decision": decision,
                                     "expected_revision": revision or _revision(ctx)}})


def _item(ctx, item_id="review:manual"):
    return next(item for item in _checks(ctx)["items"] if item["id"] == item_id)


def test_real_pipeline_summary_is_recognized_without_changing_artifacts(task_factory):
    ctx = task_factory()
    original = copy.deepcopy(ctx.artifacts)
    item = _item(ctx, "artifact:digest")
    assert item["state"] == "satisfied"
    assert item["evidence"] == [{"artifact": "extract", "field": "digest_md"}]
    assert ctx.artifacts == original


def test_legacy_summary_remains_supported(task_factory):
    ctx = task_factory()
    ctx.artifacts["digest"] = ctx.artifacts.pop("extract")["digest_md"]
    assert _item(ctx, "artifact:digest")["state"] == "satisfied"


def test_review_revision_is_stable_and_success_reply_identifies_saved_version(task_factory):
    ctx = task_factory()
    first = _checks(ctx)
    assert len(first.get("revision", "")) == 64
    assert first["revision"] == _checks(ctx)["revision"]
    reply = _review(ctx)
    assert reply["ok"] is True
    assert reply["task_id"] == ctx.task_id
    assert reply["review"]["revision"] == first["revision"]
    assert reply["review"]["decision"] == "approved"
    assert _checks(ctx)["revision"] == first["revision"]
    assert _checks(ctx)["verdict"] == "all_satisfied"


@pytest.mark.parametrize("change", ["summary", "citation", "file", "reset"])
def test_prior_review_becomes_stale_when_outcomes_change(task_factory, change):
    ctx = task_factory()
    assert _review(ctx)["ok"] is True
    if change == "summary":
        ctx.artifacts["extract"]["digest_md"] = "Opposite conclusion"
    elif change == "citation":
        ctx.artifacts["citation"]["entries"] = [{"title": "Different paper"}]
    elif change == "file":
        Path(ctx.artifacts["lit_filter"]["pool"][0]["path"]).write_text("changed material")
    else:
        ctx.reset()
    assert _item(ctx)["state"] == "stale"
    assert _checks(ctx)["verdict"] != "all_satisfied"


def test_unrelated_cache_refresh_does_not_expire_review(task_factory):
    ctx = task_factory()
    assert _review(ctx)["ok"] is True
    before = _revision(ctx)
    ctx.artifacts["_evidence_graph"] = {"file": "new-preview.png"}
    ctx.events.append({"kind": "poll"})
    assert _revision(ctx) == before
    assert _item(ctx)["state"] == "satisfied"


def test_changed_check_rules_expire_prior_approval(task_factory, monkeypatch):
    ctx = task_factory()
    assert _review(ctx)["ok"] is True
    original = _revision(ctx)
    monkeypatch.setattr(outcome_checks, "SCHEMA_VERSION", "outcome-checks.next")
    assert _revision(ctx) != original
    assert _item(ctx)["state"] == "stale"


def test_stale_form_is_rejected_without_overwriting_review(task_factory, tmp_path):
    ctx = task_factory()
    revision = _revision(ctx)
    assert _review(ctx)["ok"] is True
    path = tmp_path / "outcome_reviews.json"
    before = path.read_bytes()
    ctx.artifacts["extract"]["digest_md"] = "Changed while review form was open"
    reply = _review(ctx, revision=revision)
    assert reply["ok"] is False
    assert reply["code"] == "stale_revision"
    assert reply["revision"] == _revision(ctx)
    assert path.read_bytes() == before


def test_changes_requested_blocks_acceptance_and_history_preserves_both_decisions(task_factory):
    ctx = task_factory()
    assert _review(ctx, decision="changes_requested", note="Citation needs correction")["ok"] is True
    assert _item(ctx)["state"] == "failed"
    reply = _review(ctx, decision="approved", note="Correction checked")
    assert reply["ok"] is True
    assert _item(ctx)["state"] == "satisfied"
    assert [entry["decision"] for entry in reply["review"]["history"]] == [
        "changes_requested", "approved"]
    assert reply["review"]["history"][0]["note"] == "Citation needs correction"


def test_legacy_unversioned_review_requires_new_confirmation(task_factory, tmp_path):
    ctx = task_factory()
    legacy = {"by": "Previous reviewer", "note": "Old conclusion", "at": "2026-01-01", "source": "user"}
    (tmp_path / "outcome_reviews.json").write_text(json.dumps({ctx.task_id: legacy}), encoding="utf-8")
    assert _item(ctx)["state"] == "pending"
    reply = _review(ctx)
    assert reply["ok"] is True
    assert reply["review"]["history"][0]["note"] == legacy["note"]


@pytest.mark.parametrize("payload", [
    {}, [], "approved", {"by": "Reviewer", "note": "n"},
    {"by": "Reviewer", "note": "n", "decision": "perhaps", "expected_revision": "a" * 64},
    {"by": {}, "note": "n", "decision": "approved", "expected_revision": "a" * 64},
    {"by": "Reviewer", "note": [], "decision": "approved", "expected_revision": "a" * 64},
])
def test_invalid_review_payload_is_rejected_without_write(task_factory, tmp_path, payload):
    ctx = task_factory()
    reply = bridge.handle({"cmd": "outcome_review", "task_id": ctx.task_id, "record": payload})
    assert reply["ok"] is False
    assert reply.get("code") == "invalid_review"
    assert not (tmp_path / "outcome_reviews.json").exists()


def test_unknown_task_cannot_receive_a_review(task_factory, tmp_path):
    ctx = task_factory()
    revision = _revision(ctx)
    ctx.task_id = "never-created"
    reply = _review(ctx, revision=revision)
    assert reply["ok"] is False
    assert reply["code"] == "task_not_found"
    assert not (tmp_path / "outcome_reviews.json").exists()


def test_corrupt_review_store_is_preserved_on_write(task_factory, tmp_path):
    ctx = task_factory()
    revision = _revision(ctx)
    path = tmp_path / "outcome_reviews.json"
    path.write_text("{broken", encoding="utf-8")
    reply = _review(ctx, revision=revision)
    assert reply["ok"] is False
    assert reply["code"] == "review_store_error"
    assert path.read_text(encoding="utf-8") == "{broken"


def test_history_retains_only_fifty_recent_records(task_factory):
    ctx = task_factory()
    for number in range(55):
        reply = _review(ctx, note=f"Review {number}")
        assert reply["ok"] is True
    assert len(reply["review"]["history"]) == 50
    assert reply["review"]["history"][0]["note"] == "Review 5"
    assert reply["review"]["history"][-1]["note"] == "Review 54"


def test_concurrent_reviews_for_different_tasks_are_not_lost(task_factory, tmp_path):
    tasks = [task_factory(f"task-{number}") for number in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(_review, tasks))
    assert all(reply["ok"] is True for reply in replies)
    stored = json.loads((tmp_path / "outcome_reviews.json").read_text(encoding="utf-8"))
    assert set(stored) == {ctx.task_id for ctx in tasks}
    assert all(entry["note"] == "Checked the material" for entry in stored.values())


def test_independent_processes_do_not_overwrite_each_others_reviews(tmp_path):
    script = """
import sys, time
from pathlib import Path
from src import service, outcome_review
from src.orchestrator.engine import TaskContext
root, worker = Path(sys.argv[1]), sys.argv[2]
deadline = time.monotonic() + 20
while not (root / 'start').exists():
    if time.monotonic() > deadline:
        raise TimeoutError('test start barrier')
    time.sleep(.01)
for number in range(8):
    tid = f'process-{worker}-{number}'
    ctx = TaskContext(tid, 'research_pipeline', {'topic': 'process test'})
    service._tasks[tid] = ctx
    revision = service.outcome_checks(tid)['revision']
    outcome_review.record(tid, worker, str(number), decision='approved', expected_revision=revision)
"""
    environment = dict(os.environ, GEZHI_DATA_DIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    children = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), worker],
                                cwd=Path(__file__).resolve().parents[1], env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for worker in ("A", "B", "C")]
    try:
        (tmp_path / "start").write_text("go", encoding="utf-8")
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, (stdout, stderr)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    stored = json.loads((tmp_path / "outcome_reviews.json").read_text(encoding="utf-8"))
    assert set(stored) == {f"process-{worker}-{number}" for worker in ("A", "B", "C") for number in range(8)}


def test_failed_atomic_replace_preserves_previous_records(task_factory, tmp_path, monkeypatch):
    from src import state_store
    ctx = task_factory()
    assert _review(ctx)["ok"] is True
    path = tmp_path / "outcome_reviews.json"
    before = path.read_bytes()

    def unavailable(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(state_store.os, "replace", unavailable)
    reply = _review(ctx, decision="changes_requested")
    assert reply["ok"] is False
    assert reply["code"] == "review_store_error"
    assert path.read_bytes() == before


def test_hash_without_file_location_cannot_pass_integrity():
    report = outcome_checks.run({"lit_filter": {"pool": [{"id": "W1", "sha256": "a" * 64}]}})
    item = next(item for item in report["items"] if item["id"] == "material:integrity")
    assert item["state"] == "unknown"


def test_partial_local_hash_coverage_cannot_pass_integrity(task_factory):
    ctx = task_factory()
    ctx.artifacts["lit_filter"]["pool"].append({"id": "W2", "path": "missing-local.pdf"})
    assert _item(ctx, "material:integrity")["state"] == "unknown"
