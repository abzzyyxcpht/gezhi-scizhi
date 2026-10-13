"""Verify the exact reviewed public-source manifest before publishing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def verify(root: Path = ROOT) -> list[str]:
    manifest = json.loads((root / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    errors = []
    for name, digest in manifest.items():
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or not path.is_file():
            errors.append(f"Invalid or missing public file: {name}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f"Changed public file: {name}")
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    extra = set(filter(None, tracked)) - set(manifest) - {"SOURCE_MANIFEST.json"}
    errors.extend(f"Unreviewed tracked file: {name}" for name in sorted(extra))
    return errors


if __name__ == "__main__":
    problems = verify()
    print(json.dumps({"ok": not problems, "errors": problems}, ensure_ascii=False))
    raise SystemExit(1 if problems else 0)
