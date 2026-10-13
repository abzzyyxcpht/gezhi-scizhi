"""Cleanup removes disposable files, never research records or linked targets."""
from __future__ import annotations

import json
import os
import stat
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from scripts import clean_workspace as cleaner


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory(prefix="scizhi-cleanup-") as directory:
        yield Path(directory)


def put(root, relative, text="keep"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_root_allowlist_preserves_unknown_files_and_reusable_scripts(tmp_path, monkeypatch):
    monkeypatch.setattr(cleaner, "ROOT", tmp_path)
    keep = ["_my_research.py", "_tmp_results.csv",
            "notes.bak", "paper.orig", "Custom.spec", "Gezhi.spec"]
    for name in keep + ["_verify_pdf.py", "格致.spec", "scripts/clean_intermediates.ps1"]:
        put(tmp_path, name)
    actual = {path.relative_to(tmp_path).as_posix() for path in cleaner.collect_root_junk()}
    assert actual == {"_verify_pdf.py", "格致.spec", "scripts/clean_intermediates.ps1"}


def test_default_apply_preserves_research_deliverables_and_external_cli_state(tmp_path):
    preserved = [
        "_unknown.txt", "data/checkpoints/old.json", "data/sessions/old.jsonl",
        "data/exports/old/result.md", "data/artifacts/stats/chart.png", "data/__pycache__/keep.pyc",
        "dist/old-Setup-0.4.0.exe", "dist/Gezhi/__pycache__/runtime.pyc",
        "deliverables/package/__pycache__/module.pyc", "assets/__pycache__/image.pyc",
        "design-previews/__pycache__/capture.pyc", "tools/__pycache__/helper.pyc",
        "tui/node_modules/example/__pycache__/module.pyc", ".codely-cli/history.jsonl",
        ".mimosa/state.json", "build/pitch-enhanced-final/deck.pptx",
        "build/pitch/__pycache__/slides.pyc", "build/verification.json",
    ]
    for name in preserved:
        put(tmp_path, name)
    disposable = ["src/__pycache__/module.pyc", ".pytest_cache/cache/nodeids",
                  ".ruff_cache/entry", ".chart-data-abc/chart.json", "_testtmp/probe.txt"]
    for name in disposable:
        put(tmp_path, name, "temporary")
    report = cleaner.clean_workspace(tmp_path, apply=True)
    assert report["ok"] is True
    assert all((tmp_path / name).is_file() for name in preserved)
    assert not any((tmp_path / name).exists() for name in disposable)
    assert report["removed_bytes"] == sum(len("temporary") for _ in disposable)


def test_build_cache_requires_pyinstaller_markers_and_preserves_presentations(tmp_path):
    custom = put(tmp_path, "build/Gezhi/notes.md")
    report = cleaner.clean_workspace(tmp_path, apply=True)
    assert report["ok"] and custom.exists()
    put(tmp_path, "build/Gezhi/Analysis-00.toc")
    put(tmp_path, "build/Gezhi/PYZ-00.toc")
    presentation = put(tmp_path, "build/pitch/deck.pptx")
    report = cleaner.clean_workspace(tmp_path, apply=True)
    assert report["ok"]
    assert not (tmp_path / "build/Gezhi").exists()
    assert presentation.exists()


def test_versioned_build_cache_requires_markers_and_preserves_release_artifacts(tmp_path):
    cache = 'build/desktop-0.6.0/Gezhi'
    put(tmp_path, cache + '/Analysis-00.toc')
    put(tmp_path, cache + '/PYZ-00.toc')
    preserved = [put(tmp_path, 'build/desktop-0.6.0-build.log'),
                 put(tmp_path, 'build/desktop-0.7.0/Gezhi/notes.md'),
                 put(tmp_path, 'dist/0.6.0/Gezhi/Gezhi.exe'),
                 put(tmp_path, 'dist/SciZhi-Agent-Setup-0.6.0.exe')]
    report = cleaner.clean_workspace(tmp_path, apply=True)
    assert report['ok']
    assert not (tmp_path / cache).exists()
    assert all(path.exists() for path in preserved)


def test_dry_run_reports_exact_paths_without_mutating(tmp_path):
    cache = put(tmp_path, "src/__pycache__/module.pyc", "12345")
    probe = put(tmp_path, "_verify_pdf.py", "123")
    report = cleaner.clean_workspace(tmp_path)
    assert report["mode"] == "dry-run" and report["ok"]
    assert {row["path"] for row in report["targets"]} == {"src/__pycache__", "_verify_pdf.py"}
    assert all(row["status"] == "planned" for row in report["targets"])
    assert report["planned_bytes"] == 8 and report["removed_bytes"] == 0
    assert cache.exists() and probe.exists()


def test_cli_json_preview_and_apply_have_machine_readable_reports(tmp_path):
    script = tmp_path / "scripts/clean_workspace.py"
    script.parent.mkdir()
    shutil.copyfile(Path(cleaner.__file__), script)
    temporary = put(tmp_path, ".pytest_cache/cache/nodeids", "abc")
    for flags, mode in [(["--json"], "dry-run"), (["--apply", "--json"], "apply")]:
        result = subprocess.run([sys.executable, str(script), *flags], capture_output=True,
                                text=True, encoding="utf-8", check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.lstrip().startswith("{"), result.stdout
        report = json.loads(result.stdout)
        assert report["mode"] == mode and report["ok"]
        assert any(row["path"] == ".pytest_cache" for row in report["targets"])
        assert temporary.exists() is (mode == "dry-run")


def make_directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Directory symlinks are unavailable")
        # Junction creation is shell-free; removes no files and targets a test sandbox.
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, check=False)
        if result.returncode:
            pytest.skip("Neither symlinks nor junctions are available")


def unlink_directory_link(link):
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        os.rmdir(link)


def test_linked_cleanup_target_cannot_escape_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = put(tmp_path, "outside/important.txt")
    link = workspace / ".pytest_cache"
    make_directory_link(link, outside.parent)
    try:
        report = cleaner.clean_workspace(workspace, apply=True)
        assert report["ok"] is False and report["removed_bytes"] == 0
        assert report["targets"][0]["status"] == "refused"
        assert outside.read_text() == "keep" and os.path.lexists(link)
    finally:
        unlink_directory_link(link)


def test_link_inside_cache_rejects_the_entire_target(tmp_path):
    workspace = tmp_path / "workspace"
    cache_file = put(workspace, ".pytest_cache/cache/nodeids")
    outside = put(tmp_path, "outside/important.txt")
    link = workspace / ".pytest_cache/linked"
    make_directory_link(link, outside.parent)
    try:
        report = cleaner.clean_workspace(workspace, apply=True)
        assert report["ok"] is False and report["removed_bytes"] == 0
        assert cache_file.exists() and outside.exists()
    finally:
        unlink_directory_link(link)


def test_source_link_is_not_followed_during_cache_discovery(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    outside = put(tmp_path, "outside/__pycache__/important.pyc")
    link = workspace / "src/linked"
    make_directory_link(link, outside.parent.parent)
    try:
        report = cleaner.clean_workspace(workspace, apply=True)
        assert report["ok"] and report["targets"] == []
        assert outside.exists()
    finally:
        unlink_directory_link(link)


@pytest.mark.parametrize("outcome", ["raises", "silently_keeps"])
def test_failed_deletion_is_not_reported_as_freed(tmp_path, monkeypatch, capsys, outcome):
    cache = put(tmp_path, ".pytest_cache/cache/nodeids", "12345")
    monkeypatch.setattr(cleaner, "ROOT", tmp_path)
    def fail(*args, **kwargs):
        if outcome == "raises":
            raise PermissionError("simulated permission failure")
    monkeypatch.setattr(cleaner.shutil, "rmtree", fail)
    assert cleaner.main(["--apply", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["removed_bytes"] == 0
    assert report["targets"][0]["status"] == "failed"
    assert report["errors"] and cache.exists()


def test_nested_caches_are_counted_once(tmp_path):
    put(tmp_path, "_testtmp/nested/__pycache__/probe.pyc", "12345")
    report = cleaner.clean_workspace(tmp_path, apply=True)
    assert [row["path"] for row in report["targets"]] == ["_testtmp"]
    assert report["planned_bytes"] == 5 and report["removed_bytes"] == 5


def test_readonly_git_object_inside_disposable_clone_can_be_cleaned(tmp_path):
    scratch = put(tmp_path, '_testtmp/clone/.git/objects/pack/pack.idx', '12345')
    protected = put(tmp_path, 'data/research.json', 'keep')
    scratch.chmod(stat.S_IREAD)
    try:
        report = cleaner.clean_workspace(tmp_path, apply=True)
        assert report['ok'] and report['removed_bytes'] == 5
        assert not scratch.exists() and protected.read_text() == 'keep'
    finally:
        if scratch.exists():
            scratch.chmod(stat.S_IWRITE | stat.S_IREAD)
