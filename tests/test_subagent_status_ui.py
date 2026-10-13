"""Parent transcript never leaves a finished or waiting child marked running."""
from types import SimpleNamespace

import flet as ft
import pytest

from src.ui.session_composer import SessionComposer


@pytest.mark.parametrize("status,label", [("waiting_approval", "等待审批"), ("cancelled", "已取消"),
    ("blocked", "已阻止"), ("partial", "部分完成"), ("waiting_children", "等待分支"), ("interrupted", "已中断")])
def test_child_state_transition_replaces_running(status, label):
    view = SimpleNamespace(subagent_rows={}, transcript=ft.Column())
    SessionComposer._update_subagent_row(view, "subagent_started", {"child": "child-1", "agent": "critic", "status": "running"})
    SessionComposer._update_subagent_row(view, "subagent_status", {"child": "child-1", "agent": "critic", "status": status})
    assert label in view.subagent_rows["child-1"].value
    assert "运行中" not in view.subagent_rows["child-1"].value


def test_missing_reported_child_usage_is_not_displayed_as_zero():
    view = SimpleNamespace(subagent_rows={}, transcript=ft.Column())
    SessionComposer._update_subagent_row(view, "subagent_status", {"child": "child-1", "agent": "critic", "status": "done"})
    assert "未报告" in view.subagent_rows["child-1"].value
