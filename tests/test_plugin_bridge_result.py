from unittest import mock

from src.bridge import handle


def test_failed_plugin_result_stays_failed_at_bridge():
    with mock.patch("src.plugins.run_command_result", return_value={"ok": False, "error": "插件运行失败", "output": "失败输出"}):
        result = handle({"cmd": "plugin_call", "name": "fixture", "args": "", "request_id": "failed-call"})
    assert result["ok"] is False
    assert "失败" in result["error"]
    assert result["request_id"] == "failed-call"
