"""Verify the exact reviewed public-source manifest before publishing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

# A file that is tracked but absent from the manifest is almost always an
# ordering mistake: the manifest was refreshed before the path was staged.
# Say so instead of leaving the reader to guess.
_ORDERING_HINT = (
    "Unreviewed tracked file usually means the manifest was refreshed before "
    "staging: run `git add -A` first, then `python scripts/refresh_source_manifest.py`."
)


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


def hint(errors: list[str]) -> str:
    """Actionable next step for the failures that have one."""
    if any(error.startswith("Unreviewed tracked file") for error in errors):
        return _ORDERING_HINT
    if any(error.startswith("Changed public file") for error in errors):
        return ("Changed public file means the reviewed bytes drifted: refresh with "
                "`python scripts/refresh_source_manifest.py` after staging your changes.")
    return ""


if __name__ == "__main__":
    problems = verify()
    print(json.dumps({"ok": not problems, "errors": problems, "hint": hint(problems)},
                     ensure_ascii=False))
    raise SystemExit(1 if problems else 0)
