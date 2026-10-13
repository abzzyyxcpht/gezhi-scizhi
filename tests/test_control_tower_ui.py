"""Control Tower interactions keep plan and approval identities intact."""
from types import SimpleNamespace
from unittest import mock

from src.ui.control_tower import ControlTower, ReasoningControl


def controls(root):
    yield root
    for name in ("controls", "actions"):
        for child in getattr(root, name, []) or []:
            yield from controls(child)
    child = getattr(root, "content", None)
    if child is not None and not isinstance(child, str):
        yield from controls(child)


def panel():
    page = mock.MagicMock()
    page.width, page.height = 1100, 760
    page.run_thread.side_effect = lambda fn: fn()
    api = SimpleNamespace(
        get_control_tower=mock.Mock(return_value={"summary": {"running": 2}, "pending": [
            {"session_id": "child-2", "id": "call-7", "tool": "file.write"}]}),
        plan_swarm=mock.Mock(return_value={"plan_id": "plan-1", "status": "planned", "request": "研究目标",
            "branches": [{"id": "b-1", "agent": "literature", "request": "文献检索", "status": "planned"}]}),
        start_swarm=mock.Mock(return_value={"plan_id": "plan-1", "status": "running", "branches": []}),
        get_swarm=mock.Mock(return_value={"plan_id": "plan-1", "status": "done", "branches": []}),
        cancel_swarm=mock.Mock(return_value={"plan_id": "plan-1", "status": "cancelled", "branches": []}),
    )
    opened = mock.Mock()
    return ControlTower(page, open_session=opened, api=api), page, api, opened


def test_preview_does_not_execute_and_start_uses_preview_identity():
    ui, _, api, _ = panel()
    ui.open("swarm")
    ui.request.value = "研究目标"
    ui.preview()
    api.plan_swarm.assert_called_once_with("研究目标", max_parallel=4)
    api.start_swarm.assert_not_called()
    assert ui.plan_id == "plan-1"
    ui.start()
    api.start_swarm.assert_called_once_with("plan-1")


def test_editing_request_invalidates_old_preview():
    ui, _, api, _ = panel()
    ui.open("swarm")
    ui.request.value = "研究目标"
    ui.preview()
    ui.request.value = "新的目标"
    ui.invalidate_plan()
    ui.start()
    api.start_swarm.assert_not_called()
    assert ui.plan_id == ""


def test_pending_approval_opens_exact_child_session():
    ui, _, _, opened = panel()
    ui.open()
    button = next(c for c in controls(ui.body) if getattr(c, "content", None) == "查看审批")
    button.on_click(None)
    opened.assert_called_once_with("child-2")


def test_closed_dialog_ignores_late_snapshot():
    ui, page, _, _ = panel()
    queued = []
    page.run_thread.side_effect = queued.append
    ui.open()
    before = list(ui.body.controls)
    ui.close()
    queued.pop()()
    assert ui.body.controls == before


def test_switching_swarm_keeps_new_identity_when_old_read_finishes():
    ui, page, api, _ = panel()
    ui.open("swarm")
    queued = []
    page.run_thread.side_effect = queued.append
    ui.show_swarm("plan-1")
    ui.show_swarm("plan-2")
    queued.pop()()
    api.get_swarm.assert_called_once_with("plan-1")
    assert ui.plan_id == "plan-2"
    assert ui.snapshot == {}


def test_waiting_children_cannot_be_replaced_with_another_plan():
    ui, _, _, _ = panel()
    ui.open("swarm")
    ui.render_swarm({"plan_id": "active-plan", "status": "waiting_children", "branches": []})
    ui.invalidate_plan()
    assert ui.plan_id == "active-plan"
    assert ui.preview_button.disabled
    assert not ui.cancel_button.disabled


def test_reasoning_setting_failure_keeps_previous_choice():
    page = mock.MagicMock()
    save = mock.Mock(side_effect=ValueError("无法保存"))
    ui = ReasoningControl(page, value="medium", save=save, motion=lambda: False)
    ui.choose("high")
    assert ui.value == "medium"
    assert "无法保存" in ui.note.value
    assert all(bar.animate is None and bar.animate_opacity is None for bar in ui.bars)


def test_reasoning_choice_saves_real_setting_and_changes_visual_intensity():
    page = mock.MagicMock()
    save = mock.Mock()
    ui = ReasoningControl(page, value="low", save=save, motion=lambda: True)
    low = [bar.height for bar in ui.bars]
    ui.choose("high")
    save.assert_called_once_with({"llm_reasoning_effort": "high"})
    assert max(bar.height for bar in ui.bars) > max(low)
    ui.motion = lambda: False
    ui.pulse(1, active=True)
    assert all(bar.animate is None and bar.animate_opacity is None for bar in ui.bars)
