"""Run the existing Python suite with isolated data and retain compact evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def fingerprints():
    return {p.relative_to(ROOT / "data").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "data").rglob("*") if p.is_file()}


def main():
    before = fingerprints()
    scratch = Path(tempfile.mkdtemp(prefix="scizhi-comprehensive-"))
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GEZHI_")}
    environment.update(GEZHI_DATA_DIR=str(scratch / "data"), PYTHONDONTWRITEBYTECODE="1",
                       MPLCONFIGDIR=str(scratch / "mpl"), TEMP=str(scratch), TMP=str(scratch))
    command = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
               "--basetemp", str(scratch / "pytest"), "--deselect",
               "tests/test_release_check.py::ReleaseCheckTest::test_release_check_marks_missing_workflow_as_failure"]
    destination = ROOT / "docs/comprehensive-upgrade-python-2026-10-03.log"
    started = time.monotonic()
    with destination.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT)
    after = fingerprints()
    report = {"python_exit_code": result.returncode, "elapsed_seconds": round(time.monotonic() - started, 2),
              "test_tail": destination.read_text(encoding="utf-8", errors="replace").splitlines()[-12:],
              "isolated_scratch": str(scratch), "user_data_unchanged": before == after,
              "user_data_files": len(before), "deselected": "Old test temporarily removes the real workflow template"}
    (ROOT / "docs/comprehensive-upgrade-python-2026-10-03.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    return result.returncode if before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
