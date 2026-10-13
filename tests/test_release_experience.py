"""Release gates exercise real modules in an isolated process, without a model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
NAMES = {"composer_drafts", "tool_outputs", "shell_owned_execution", "shell_stream_capture", "interaction_controls"}


def run_isolated(script, directory):
    env = dict(os.environ, GEZHI_DATA_DIR=str(directory), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=35,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def test_import_does_not_choose_application_data_before_selfcheck_isolation():
    with tempfile.TemporaryDirectory() as directory:
        result = run_isolated("import sys; import src.release_experience; assert 'src.config' not in sys.modules", directory)
    assert result.returncode == 0, result.stderr


def test_all_experience_checks_run_real_modules_without_network_or_provider():
    script = """
import json
from pathlib import Path
from src.ui.desktop_launcher import _offline_network
with _offline_network() as network:
    from src import config
    from src.agent_runtime import AgentRuntime
    from src.release_experience import checks
    runtime = AgentRuntime(session_root=config.DATA_DIR / 'sessions', offline=True, llm_enabled=False)
    results = {name: operation() for name, operation in checks(runtime, config.DATA_DIR)}
    assert network['attempts'] == 0
print(json.dumps(results, ensure_ascii=False))
"""
    with tempfile.TemporaryDirectory() as directory:
        result = run_isolated(script, directory)
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert set(report) == NAMES
        assert report["composer_drafts"]["cas_conflict"] and report["composer_drafts"]["clear_reopened"]
        assert report["tool_outputs"]["cross_session_denied"] and report["tool_outputs"]["utf8_pages"] > 1
        assert report["shell_owned_execution"]["tree_empty_after_cancel"]
        assert report["shell_owned_execution"]["exit_code"] == 7
        assert report["shell_stream_capture"]["live_utf8"] and report["shell_stream_capture"]["pre_cancel_no_effect"]
        assert report["interaction_controls"]["distinct_heights"]


def test_experience_gate_rejects_every_missing_or_failed_check(tmp_path):
    from scripts import pack_release
    assert NAMES <= pack_release.REQUIRED_SELF_CHECKS
    base = {"ok": True, "frozen": True, "version": pack_release.APP_VER,
            "checks": [{"name": name, "ok": True} for name in pack_release.REQUIRED_SELF_CHECKS]}
    report = tmp_path / "self-check.json"
    report.write_text(json.dumps(base), encoding="utf-8")
    assert pack_release._validated_check(report)["ok"]
    for missing in NAMES:
        candidate = {**base, "checks": [row for row in base["checks"] if row["name"] != missing]}
        report.write_text(json.dumps(candidate), encoding="utf-8")
        with pytest.raises(SystemExit, match="invalid"):
            pack_release._validated_check(report)
        candidate = {**base, "checks": [dict(row, ok=False) if row["name"] == missing else row for row in base["checks"]]}
        report.write_text(json.dumps(candidate), encoding="utf-8")
        with pytest.raises(SystemExit, match="invalid"):
            pack_release._validated_check(report)
