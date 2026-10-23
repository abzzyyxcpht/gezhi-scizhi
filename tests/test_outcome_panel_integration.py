"""Exercise a human review through the native panel, bridge and on-disk store."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

from src import bridge, config, outcome_review, service
from src.orchestrator import engine
from src.ui.outcome_panel import OutcomeCheckPanel


def test_native_review_then_material_change_requires_another_review(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'UPLOAD_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(engine, 'CHECKPOINT_DIR', tmp_path / 'checkpoints')
    config.UPLOAD_DIR.mkdir()
    source = config.UPLOAD_DIR / 'evidence.txt'
    source.write_text('offline test material v1', encoding='utf-8')
    ctx = engine.TaskContext('panel-review-e2e', 'research_pipeline', {'topic': 'offline'})
    ctx.artifacts = {
        'lit_filter': {'pool': [{'id': 'W1', 'title': 'Offline fixture', 'url': str(source),
                                'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]},
        'extract': {'digest_md': 'Offline digest'},
        'citation': {'entries': [{'title': 'Offline fixture'}]},
        'review_frame': {'sections': ['Scope']},
    }
    monkeypatch.setitem(service._tasks, ctx.task_id, ctx)
    pending, dialogs = [], []
    page = SimpleNamespace(update=Mock(), run_thread=pending.append,
                           show_dialog=dialogs.append, pop_dialog=Mock())
    panel = OutcomeCheckPanel(page, api=bridge.handle)

    def drain():
        while pending:
            pending.pop(0)()

    def manual_state():
        return next(row['state'] for row in panel.checks['items']
                    if row['id'] == 'review:manual')

    panel.refresh(ctx.task_id)
    drain()
    assert manual_state() == 'pending'
    assert next(row['state'] for row in panel.checks['items']
                if row['id'] == 'artifact:digest') == 'satisfied'
    for decision, state in [('changes_requested', 'failed'), ('approved', 'satisfied')]:
        panel.open_review()
        panel.reviewer.value = 'Test reviewer'
        panel.review_note.value = 'Explicit offline review'
        panel.review_decision.value = decision
        panel.save_review()
        drain()
        assert manual_state() == state
    assert len(outcome_review.read(ctx.task_id)['history']) == 2

    panel.open_review()
    panel.reviewer.value = 'Test reviewer'
    panel.review_note.value = 'Keep my draft on a version conflict'
    panel.review_decision.value = 'approved'
    source.write_text('changed after the form was opened', encoding='utf-8')
    panel.save_review()
    drain()
    assert panel.dialog is not None
    assert panel.dialog_error.visible
    assert '成果已变化' in panel.dialog_error.value
    assert panel.review_note.value == 'Keep my draft on a version conflict'
    assert len(outcome_review.read(ctx.task_id)['history']) == 2
    panel.close_review()
    panel.refresh_button.on_click(None)
    drain()
    assert manual_state() == 'stale'
    assert next(row['state'] for row in panel.checks['items']
                if row['id'] == 'material:integrity') == 'stale'
