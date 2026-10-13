import pytest

from src.remote_control import _READ_ONLY, _validated


def test_remote_activity_and_model_preview_are_bounded_read_only():
    assert _validated({"cmd": "session_activity", "session_id": "parent-1", "limit": 30})["limit"] == 30
    assert _validated({"cmd": "model_control_catalog", "stage": "planning"})["stage"] == "planning"
    assert {"session_activity", "model_control_catalog"}.issubset(_READ_ONLY)


def test_remote_cannot_mutate_model_controls():
    with pytest.raises(PermissionError):
        _validated({"cmd": "model_control_apply", "stage": "planning", "profile_id": "reader", "expected_revision": "r1"})


@pytest.mark.parametrize("payload", [{"cmd": "session_activity", "limit": 20},
    {"cmd": "session_activity", "session_id": "parent", "limit": 101},
    {"cmd": "session_activity", "session_id": "../other"},
    {"cmd": "model_control_catalog", "stage": "not-a-stage"},
    {"cmd": "model_control_catalog", "stage": "planning", "api_key": "unexpected"}])
def test_invalid_remote_preview_rejected(payload):
    with pytest.raises(ValueError):
        _validated(payload)
