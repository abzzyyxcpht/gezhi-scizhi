"""The manifest gate must explain the ordering mistake instead of just failing."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_public_source as verify  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "SOURCE_MANIFEST.json").write_text(json.dumps({}), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def test_untracked_new_path_is_reported_with_the_ordering_hint(tmp_path):
    root = _repo(tmp_path)
    errors = verify.verify(root)
    assert any(error.startswith("Unreviewed tracked file") for error in errors)
    assert "git add" in verify.hint(errors)


def test_changed_bytes_are_reported_with_the_refresh_hint(tmp_path):
    root = _repo(tmp_path)
    digest = "0" * 64
    (root / "SOURCE_MANIFEST.json").write_text(
        json.dumps({"a.txt": digest}), encoding="utf-8")
    errors = verify.verify(root)
    assert any(error.startswith("Changed public file") for error in errors)
    assert "refresh_source_manifest" in verify.hint(errors)


def test_clean_tree_has_no_errors_and_no_hint(tmp_path):
    root = _repo(tmp_path)
    import hashlib
    digest = hashlib.sha256(b"alpha").hexdigest()
    (root / "SOURCE_MANIFEST.json").write_text(
        json.dumps({"a.txt": digest}), encoding="utf-8")
    errors = verify.verify(root)
    assert errors == []
    assert verify.hint(errors) == ""
