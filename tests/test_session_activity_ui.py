"""Activity rows are read-only and open the exact reported child identity."""
from unittest import mock

from src.ui.session_activity import SessionActivityPanel


def panel():
    page = mock.Mock()
    page.run_thread.side_effect = lambda fn: fn()
    selected = ["parent"]
    api = mock.Mock(return_value={"ok": True, "activity": {"session_id": "parent", "status": "waiting_children",
        "items": [{"id": "call-1", "type": "tool", "label": "research.search", "status": "done", "summary": "已返回"}],
        "children": [{"session_id": "child", "parent_id": "parent", "agent": "critic", "status": "waiting_approval", "request": "检查证据", "depth": 1,
                      "pending": {"id": "approval-1", "tool": "file.write"}, "tokens": None, "steps": None}], "truncated": False}})
    opened = mock.Mock()
    ui = SessionActivityPanel(page, lambda: selected[0], open_session=opened, api=api)
    return ui, page, selected, api, opened


def test_collapsed_activity_does_not_poll_or_trigger_work():
    ui, _, _, api, _ = panel()
    ui.refresh()
    api.assert_not_called()
    ui.expand()
    assert api.call_args.args[0] == {"cmd": "session_activity", "session_id": "parent", "limit": 30}


def test_child_button_opens_exact_child_and_waiting_reason():
    ui, _, _, _, opened = panel()
    ui.expand()
    ui.child_buttons["child"].on_click(None)
    opened.assert_called_once_with("child")
    assert "等待审批" in ui.child_buttons["child"].content
    assert "1" in ui.summary.value


def test_other_session_ignores_old_async_response():
    ui, page, selected, _, _ = panel()
    queued = []
    page.run_thread.side_effect = queued.append
    ui.expand()
    selected[0] = "other"
    queued.pop()()
    assert ui.child_buttons == {}


def test_failed_refresh_retains_last_good_view_with_error():
    ui, _, _, api, _ = panel()
    ui.expand()
    before = list(ui.rows.controls)
    api.return_value = {"ok": False, "error": "活动暂不可读"}
    ui.refresh(force=True)
    assert ui.rows.controls == before
    assert "暂不可读" in ui.note.value


def test_old_child_button_cannot_navigate_after_parent_switch():
    ui, _, selected, _, opened = panel()
    ui.expand()
    previous = ui.child_buttons["child"]
    selected[0] = "other"
    previous.on_click(None)
    opened.assert_not_called()
