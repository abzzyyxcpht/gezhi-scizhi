"""Regenerate SOURCE_MANIFEST.json from the files Git tracks.

``SOURCE_MANIFEST.json`` pins the exact reviewed public source. Until now only
``prepare_opensource.py`` wrote it, while the README told contributors to
"refresh the manifest" without a way to do so; this script is that way.

Rules:
- the manifest covers every tracked file except ``SOURCE_MANIFEST.json`` itself;
- a tracked path that is missing from the working tree is a hard error, so a
  forgotten ``git rm`` cannot silently produce an incomplete manifest;
- ``--check`` only compares and exits non-zero on drift (usable from CI).

Usage:
    python scripts/refresh_source_manifest.py           # rewrite the manifest
    python scripts/refresh_source_manifest.py --check   # verify, do not write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "SOURCE_MANIFEST.json"


def tracked_files(root: Path | None = None) -> list[str]:
    """Tracked paths from the index, excluding the manifest itself."""
    root = ROOT if root is None else root
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                            capture_output=True, check=True)
    names = result.stdout.decode("utf-8").split("\0")
    return [name for name in names if name and name != MANIFEST]


def build_manifest(root: Path | None = None) -> dict[str, str]:
    """Map every tracked file to its SHA-256 digest."""
    root = ROOT if root is None else root
    manifest: dict[str, str] = {}
    for name in sorted(tracked_files(root)):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"tracked file missing or not a regular file: {name} "
                             f"(stage its deletion with `git rm --cached` first)")
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="compare with the committed manifest instead of writing")
    args = parser.parse_args(argv)

    # Only tracked files belong to the reviewed manifest. Run this AFTER
    # `git add` so newly added paths are covered by the same gate, and
    # refresh once more if any tracked file changes afterwards.
    # staging so newly added paths are covered by the same gate.
    manifest = build_manifest()
    target = ROOT / MANIFEST
    rendered = json.dumps(manifest, indent=2, ensure_ascii=False)

    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != rendered:
            print(f"{MANIFEST} is out of date; run "
                  f"`python scripts/refresh_source_manifest.py`", file=sys.stderr)
            return 1
        print(f"{MANIFEST} matches {len(manifest)} tracked files")
        return 0

    target.write_text(rendered, encoding="utf-8")
    print(f"wrote {MANIFEST} for {len(manifest)} tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
