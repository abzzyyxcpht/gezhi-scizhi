"""Tests for scripts/refresh_source_manifest.py."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_source_manifest as refresh  # noqa: E402 -- script directory added above


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class RefreshSourceManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "test@example.invalid")
        _git(self.root, "config", "user.name", "test")
        (self.root / "a.txt").write_text("alpha", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.py").write_text("b = 1\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        self._saved_root = refresh.ROOT
        refresh.ROOT = self.root

    def tearDown(self) -> None:
        refresh.ROOT = self._saved_root
        self._tmp.cleanup()

    def test_build_manifest_hashes_every_tracked_file(self) -> None:
        manifest = refresh.build_manifest(self.root)
        self.assertEqual(set(manifest), {"a.txt", "sub/b.py"})
        self.assertEqual(manifest["a.txt"],
                         hashlib.sha256(b"alpha").hexdigest())

    def test_manifest_excludes_itself(self) -> None:
        (self.root / "SOURCE_MANIFEST.json").write_text("{}", encoding="utf-8")
        _git(self.root, "add", "SOURCE_MANIFEST.json")
        self.assertNotIn("SOURCE_MANIFEST.json",
                         refresh.build_manifest(self.root))

    def test_missing_tracked_file_is_rejected(self) -> None:
        (self.root / "a.txt").unlink()
        with self.assertRaises(SystemExit):
            refresh.build_manifest(self.root)

    def test_write_then_check_round_trip(self) -> None:
        self.assertEqual(refresh.main([]), 0)
        self.assertEqual(refresh.main(["--check"]), 0)
        # Any content change must be reported as drift.
        (self.root / "a.txt").write_text("changed", encoding="utf-8")
        with self._capture_stderr():
            self.assertEqual(refresh.main(["--check"]), 1)

    def test_write_uses_stable_json(self) -> None:
        refresh.main([])
        rendered = (self.root / "SOURCE_MANIFEST.json").read_text(encoding="utf-8")
        self.assertEqual(rendered, json.dumps(refresh.build_manifest(self.root),
                                              indent=2, ensure_ascii=False))

    @staticmethod
    def _capture_stderr():
        import contextlib
        import io
        return contextlib.redirect_stderr(io.StringIO())


if __name__ == "__main__":
    unittest.main()
