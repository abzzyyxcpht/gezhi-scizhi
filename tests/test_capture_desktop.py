"""Native screenshot helpers close through the application's shutdown contract."""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest import mock

import flet as ft
import pytest

from scripts import capture_desktop


def test_regular_capture_invokes_the_registered_native_close_callback():
    page = mock.Mock()
    page.window.on_event = mock.AsyncMock()
    page.window.destroy = mock.AsyncMock()
    asyncio.run(capture_desktop.close_capture_page(page, {}))
    page.window.on_event.assert_awaited_once()
    assert page.window.on_event.call_args.args[0].type == ft.WindowEventType.CLOSE
    page.window.destroy.assert_not_awaited()


def test_draft_capture_preserves_its_explicit_force_close_cleanup():
    page = mock.Mock()
    close = mock.AsyncMock()
    asyncio.run(capture_desktop.close_capture_page(page, {"finish_native_close": close}))
    close.assert_awaited_once_with(force=True)
    page.window.on_event.assert_not_called()


def test_capture_scrolls_activity_without_moving_the_fixed_request():
    request = ft.TextField(label="交给智能体的任务")
    activity = ft.Column([ft.ExpansionTile(title=ft.Text("执行选项"))], scroll=ft.ScrollMode.AUTO)
    fixed = ft.Column([request, activity])
    page = SimpleNamespace(controls=[fixed])
    assert capture_desktop.agent_scroller(page) is activity


def test_mode_capture_refuses_missing_native_controls():
    isolated = capture_desktop.ROOT / '_testtmp' / 'capture-safety-check'
    with pytest.raises(RuntimeError, match='helper-captured desktop controls'):
        asyncio.run(capture_desktop.prepare_workspace_mode_state(
            mock.Mock(), 'general-workspace', isolated, {}))


def test_mode_capture_refuses_settings_outside_its_isolated_directory():
    from src import settings
    sandbox = capture_desktop.ROOT / '_testtmp' / 'capture-safety-check'
    controls = {name: mock.Mock() for name in
        ('workspace_mode_control', 'agent_request', 'agent_pick', 'tools_panel')}
    with mock.patch.object(settings, '_PATH', sandbox / 'user-settings.json'):
        with pytest.raises(RuntimeError, match='inside the isolated data directory'):
            asyncio.run(capture_desktop.prepare_workspace_mode_state(
                mock.Mock(), 'general-workspace', sandbox / 'isolated', controls))
    assert not controls['workspace_mode_control'].mock_calls


def test_outcome_screens_use_the_results_destination():
    assert capture_desktop.SCREENS.get('outcome-checks') == '文献与成果'
    assert capture_desktop.SCREENS.get('outcome-review') == '文献与成果'


@pytest.fixture
def outcome_task(tmp_path, monkeypatch):
    from src import config, service
    from src.orchestrator import engine

    data = tmp_path / 'isolated-capture'
    data.mkdir()
    uploads, checkpoints = data / 'uploads', data / 'checkpoints'
    uploads.mkdir()
    checkpoints.mkdir()
    monkeypatch.setattr(config, 'DATA_DIR', data)
    monkeypatch.setattr(config, 'UPLOAD_DIR', uploads)
    monkeypatch.setattr(config, 'CHECKPOINT_DIR', checkpoints)
    monkeypatch.setattr(engine, 'CHECKPOINT_DIR', checkpoints)
    monkeypatch.setattr(service, 'CHECKPOINT_DIR', checkpoints)
    task = engine.TaskContext('capture-outcome', 'research_pipeline',
                              {'topic': capture_desktop.DEMO_TOPIC, 'offline': True})
    task.status = 'done'
    task.artifacts = {'lit_filter': {'pool': []},
                      'extract': {'digest_md': '离线界面夹具，不是真实研究结论。'},
                      'review_frame': {'outline_md': '离线综述框架'},
                      'citation': {'entries': [{'title': '离线验收材料'}]}}
    monkeypatch.setitem(service._tasks, task.task_id, task)
    task.save()
    return data, task


def test_outcome_fixture_uses_real_material_hash_and_keeps_review_pending(outcome_task):
    from src import bridge, outcome_review

    data, task = outcome_task
    seeded = capture_desktop.seed_outcome_material(data, task.task_id)
    material = data / 'uploads' / seeded['material_file']
    assert material.is_file()
    assert '离线界面验收夹具' in material.read_text(encoding='utf-8')
    assert seeded['sha256'] == hashlib.sha256(material.read_bytes()).hexdigest()
    reply = bridge.handle({'cmd': 'outcome_checks', 'task_id': task.task_id})
    assert reply['ok']
    checks = {row['id']: row for row in reply['checks']['items']}
    assert checks['material:integrity']['state'] == 'satisfied'
    assert checks['review:manual']['state'] == 'pending'
    assert not outcome_review.read(task.task_id)
    capture_desktop.seed_outcome_material(data, task.task_id)
    assert len(task.artifacts['lit_filter']['pool']) == 1


def test_outcome_fixture_refuses_another_data_directory(outcome_task):
    data, task = outcome_task
    with pytest.raises(RuntimeError, match='isolated data directory'):
        capture_desktop.seed_outcome_material(data / 'another', task.task_id)
    assert task.artifacts['lit_filter']['pool'] == []


def test_outcome_fixture_refuses_a_live_task(outcome_task):
    data, task = outcome_task
    task.params['offline'] = False
    with pytest.raises(RuntimeError, match='offline demonstration task'):
        capture_desktop.seed_outcome_material(data, task.task_id)
    assert not list((data / 'uploads').iterdir())


def test_outcome_capture_opens_task_without_a_visible_history_sidebar(outcome_task):
    data, task = outcome_task
    state = {'task_id': None}
    opened = []

    def open_task(task_id):
        opened.append(task_id)
        state['task_id'] = task_id

    page = SimpleNamespace(controls=[], update=lambda: None)
    asyncio.run(capture_desktop.select_outcome_task(
        page, data, task.task_id, {'open_task': open_task, 'state': state}))
    assert opened == [task.task_id]
    assert state['task_id'] == task.task_id


@pytest.mark.parametrize('screen', ['outcome-checks', 'outcome-review'])
def test_outcome_capture_reads_real_checks_and_never_submits_review(outcome_task, screen):
    from src import outcome_review
    from src.ui.outcome_panel import OutcomeCheckPanel

    data, task = outcome_task
    capture_desktop.seed_outcome_material(data, task.task_id)
    dialogs = []
    page = SimpleNamespace(update=lambda: None, run_thread=lambda job: job(),
                           show_dialog=lambda dialog: dialogs.append(dialog),
                           _dialogs=SimpleNamespace(controls=dialogs))
    panel = OutcomeCheckPanel(page)

    def tab_changed(event):
        panel.refresh(task.task_id, force=True)
        # Native tab notifications may arrive after the scripted selection.
        asyncio.get_running_loop().call_later(
            .05, lambda: panel.refresh(task.task_id, force=True))

    tabs = ft.Tabs(content=ft.Column([]), length=7, selected_index=0,
                   on_change=tab_changed)
    page.controls = [tabs, panel.content]
    ui = {'outcome_panel': panel, 'result_tabs': tabs, 'state': {'task_id': task.task_id}}
    result = asyncio.run(capture_desktop.prepare_outcome_state(page, screen, data, ui))
    assert tabs.selected_index == 6
    assert result['fixture'] is True
    assert result['review_submitted'] is False
    assert result['revision'] == panel.checks['revision']
    assert result['evidence_expanded'] == 1
    assert sum(bool(control.expanded) for control in capture_desktop.walk_controls(panel.items)
               if isinstance(control, ft.ExpansionTile)) == 1
    assert not outcome_review.read(task.task_id)
    if screen == 'outcome-review':
        assert panel.dialog in dialogs
        assert '离线' in panel.reviewer.value
        assert '不提交' in panel.review_note.value
        assert panel.review_decision.value is None
    else:
        assert not dialogs
