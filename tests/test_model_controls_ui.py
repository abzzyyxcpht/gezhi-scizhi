"""Native quick controls save one explicit target, with stale-view isolation."""
from unittest import mock

from src.ui.model_controls import ModelControlPanel


def catalog(revision="r1", active="reader"):
    return {"revision": revision, "stage": "planning", "active_id": active, "stages": ["planning", "review"],
            "items": [{"id": name, "label": name, "model": name + "-model", "protocol": "ollama", "reasoning_effort": "auto",
                       "efforts": [{"value": "auto", "enabled": True, "reason": ""}, {"value": "high", "enabled": True, "reason": ""},
                                   {"value": "max", "enabled": False, "reason": "不接受该参数"}], "configured": True} for name in ["reader", "critic"]]}


def panel():
    page = mock.Mock()
    page.width, page.height = 1080, 700
    page.run_thread.side_effect = lambda fn: fn()
    api = mock.Mock(return_value={"ok": True, "catalog": catalog()})
    changed = mock.Mock()
    return ModelControlPanel(page, api=api, on_saved=changed), page, api, changed


def test_open_reads_and_selection_is_a_preview():
    ui, _, api, _ = panel()
    ui.open()
    assert api.call_count == 1
    assert api.call_args.args[0]["cmd"] == "model_control_catalog"
    ui.profile.value = "critic"
    ui.select_profile()
    assert api.call_count == 1
    assert ui.apply_button.disabled is False
    assert all(option.key != "max" for option in ui.effort.options)


def test_apply_passes_exact_revision_stage_target_and_effort():
    ui, _, api, changed = panel()
    ui.open()
    ui.profile.value, ui.effort.value = "critic", "high"
    api.return_value = {"ok": True, "catalog": catalog("r2", "critic")}
    ui.apply()
    api.assert_called_with({"cmd": "model_control_apply", "stage": "planning", "profile_id": "critic", "effort": "high", "expected_revision": "r1"})
    changed.assert_called_once()
    assert "已保存" in ui.message.value


def test_failed_apply_keeps_the_selection_and_shows_error():
    ui, _, api, changed = panel()
    ui.open()
    ui.effort.value = "high"
    api.return_value = {"ok": False, "error": "模型配置已变化"}
    ui.apply()
    assert ui.effort.value == "high"
    assert "已变化" in ui.message.value
    changed.assert_not_called()


def test_closed_panel_ignores_delayed_response():
    ui, page, _, _ = panel()
    queued = []
    page.run_thread.side_effect = queued.append
    ui.open()
    ui.close()
    queued.pop()()
    assert ui.snapshot is None


def test_new_stage_invalidates_old_catalog_and_response():
    ui, page, api, _ = panel()
    queued = []
    page.run_thread.side_effect = queued.append
    ui.open()
    ui.stage.value = "review"
    ui.load()
    api.return_value = {"ok": True, "catalog": catalog()}
    queued[0]()
    assert ui.snapshot is None
    assert ui.apply_button.disabled


def test_effort_preview_changes_without_saving_or_calling_model():
    ui, _, api, changed = panel()
    ui.open()
    ui.effort.value = "high"
    ui.effort.on_select(None)
    assert ui.feedback.value == "high"
    assert max(bar.height for bar in ui.feedback.bars) == 24
    assert api.call_count == 1
    changed.assert_not_called()
    assert "尚未保存" in ui.preview_note.value


def test_unknown_saved_effort_requires_an_explicit_valid_choice():
    ui, _, api, _ = panel()
    saved = catalog()
    saved["items"][0]["reasoning_effort"] = "future-effort"
    api.return_value = {"ok": True, "catalog": saved}
    ui.open()
    assert ui.effort.value is None
    assert ui.feedback.value == "future-effort"
    assert ui.apply_button.disabled
    ui.apply()
    assert api.call_count == 1
    ui.effort.value = "high"
    ui.effort.on_select(None)
    assert not ui.apply_button.disabled
    assert ui.feedback.value == "high"


def test_failed_effort_save_preserves_draft_without_confirmed_flash():
    ui, _, api, changed = panel()
    ui.open()
    ui.effort.value = "high"
    ui.effort.on_select(None)
    ui.feedback.refresh = mock.Mock(wraps=ui.feedback.refresh)
    api.return_value = {"ok": False, "error": "模型配置已变化"}
    ui.apply()
    assert ui.feedback.value == "high"
    assert not any(call.kwargs.get("confirmed") for call in ui.feedback.refresh.call_args_list)
    changed.assert_not_called()


def test_closing_model_panel_clears_transient_feedback():
    ui, _, _, _ = panel()
    ui.open()
    ui.feedback.clear_feedback = mock.Mock()
    ui.close()
    ui.feedback.clear_feedback.assert_called_once()


def test_saved_effort_confirms_preview_only_after_success():
    ui, _, api, _ = panel()
    ui.open()
    ui.effort.value = "high"
    ui.effort.on_select(None)
    ui.feedback.confirm = mock.Mock()
    saved = catalog("r2")
    saved["items"][0]["reasoning_effort"] = "high"
    api.return_value = {"ok": True, "catalog": saved}
    ui.apply()
    ui.feedback.confirm.assert_called_once()
    assert "已保存" in ui.preview_note.value
