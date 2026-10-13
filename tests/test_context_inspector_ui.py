"""双端执行上下文来源面板的脱敏与历史冻结显示合同。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock


def _manifest():
    return {
        "schema_version": 1,
        "status": "captured",
        "basis": "frozen_prefix",
        "captured_at": "2026-10-06T10:20:30+00:00",
        "workspace_mode": "research",
        "project_id": "project-a",
        "agent": "research",
        "stage": "planning",
        "prompt_version": "2.1",
        "budget": {"unit": "characters", "limit_chars": 6000, "used_chars": 2180},
        "sources": [
            {"kind": "instructions", "name": "GEZHI.md", "scope": "project",
             "path": "D:/project-a/GEZHI.md", "status": "included",
             "used_chars": 1200, "total_chars": 1200},
            {"kind": "skill_index", "name": "evidence-audit", "scope": "stage",
             "path": "skills/evidence-audit/SKILL.md", "status": "budget_deferred",
             "used_chars": 0, "total_chars": 4100, "reason": "字符预算不足"},
            {"kind": "memory", "name": "approved-memory", "scope": "project",
             "path": "", "status": "included", "used_chars": 980,
             "total_chars": 980, "prompt_text": "PRIVATE MEMORY BODY MUST NOT RENDER"},
            {"kind": "skill_body", "name": "search-plan", "scope": "session",
             "status": "partial", "used_chars": 6000, "total_chars": 8000},
        ],
        "notices": ["仅显示来源元数据，不显示正文"],
    }


def test_manifest_lines_show_frozen_sources_and_never_render_body():
    from src.ui.session_status import context_manifest_lines

    lines = context_manifest_lines(_manifest())
    text = "\n".join(lines)

    assert "已冻结" in text
    assert "2026-10-06" in text
    assert "prompt v2.1" in text
    assert "字符 2,180/6,000" in text
    assert "GEZHI.md" in text and "已纳入" in text
    assert "evidence-audit" in text and "因预算延期" in text
    assert "部分正文已读取" in text
    assert "PRIVATE MEMORY BODY MUST NOT RENDER" not in text
    assert "仅显示来源元数据" in text


def test_missing_manifest_is_explicitly_unknown_and_does_not_use_live_settings():
    from src.ui.session_status import context_manifest_lines, execution_context_identity

    assert context_manifest_lines(None) == ["执行上下文来源：未记录（旧会话或规则流程）"]
    assert execution_context_identity({
        "workspace_mode": None, "agent": None, "mode": None,
    }) == "用途 未记录 · 角色 未记录 · 执行 未记录"


def test_panel_keeps_purpose_role_execution_separate_and_renders_manifest():
    from src.ui.session_status import SessionStatusPanel

    selected = {"sid": "session-a", "epoch": 1}
    queued = []
    page = SimpleNamespace(update=Mock(), run_thread=queued.append)
    status = {
        "session_id": "session-a", "status": "done", "driver": "adaptive",
        "workspace_mode": "general", "agent": "general", "mode": "review",
        "model": {"model": "recorded-model", "protocol": "responses"},
        "context_manifest": _manifest(), "context": None, "budget": None,
        "session_usage": None, "tokens": None, "usage_complete": None,
        "can_compact": False, "compact_unavailable_reason": "规则流程无需整理",
    }
    panel = SessionStatusPanel(
        page, lambda: selected["sid"], epoch=lambda: selected["epoch"],
        api=Mock(return_value={"ok": True, "status": status}), clock=lambda: 0.0,
    )
    panel.refresh(force=True)
    queued.pop()()

    assert panel.execution_scope.value == "用途 通用 · 角色 general · 执行 证据审查"
    assert panel.context_source_detail.value
    assert "已冻结" in panel.context_source_detail.value
    assert "recorded-model" not in panel.context_source_detail.value
    assert panel.context_sources.expanded is False


def test_context_sources_are_bounded_and_unsupported_schema_is_unknown():
    from src.ui.session_status import context_manifest_lines

    data = _manifest()
    data["sources"] = [{**data["sources"][0], "name": f"rule-{i}"} for i in range(40)]
    text = "\n".join(context_manifest_lines(data))
    assert "另有 16 个来源未显示" in text
    assert "rule-24" not in text
    assert "未记录" in context_manifest_lines({**data, "schema_version": 999})[0]
    assert "另有 21 个来源未显示" in "\n".join(context_manifest_lines({**data, "omitted_sources": 5}))


def test_manifest_scopes_and_manual_only_do_not_claim_manual_executor_exists():
    from src.ui.session_status import context_manifest_lines

    data = _manifest()
    data["sources"] = [
        {**data["sources"][0], "scope": "application"},
        {**data["sources"][1], "scope": "working_directory", "status": "manual_only"},
    ]
    text = "\n".join(context_manifest_lines(data))
    assert "程序目录" in text and "工作目录" in text
    assert "禁止模型调用（手动入口待支持）" in text


def test_late_context_manifest_cannot_replace_current_selection():
    from src.ui.session_status import SessionStatusPanel

    selected = {"sid": "session-a", "epoch": 1}
    queued = []
    page = SimpleNamespace(update=Mock(), run_thread=queued.append)
    responses = [
        {"ok": True, "status": {"session_id": "session-b", "status": "done", "context_manifest": None}},
        {"ok": True, "status": {"session_id": "session-a", "status": "done", "context_manifest": _manifest()}},
    ]
    panel = SessionStatusPanel(page, lambda: selected["sid"], epoch=lambda: selected["epoch"],
        api=Mock(side_effect=responses), clock=lambda: 0.0)
    panel.refresh(force=True)
    stale = queued.pop()
    selected.update(sid="session-b", epoch=2)
    panel.refresh(force=True)
    queued.pop()()
    stale()
    assert "未记录" in panel.context_source_detail.value
    assert "GEZHI.md" not in panel.context_source_detail.value

