"""Assemble only explicitly public source; never copy runtime user data."""
from __future__ import annotations

import hashlib
import json
import argparse
from datetime import datetime
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.version import APP_VERSION as VERSION  # noqa: E402 -- direct script entry adds the repository root
TREES = ("src", "tests", "tui/src", "tui/test", "tui/scripts", "workflows", "plugins", "skills", "examples", ".github", "packaging")
ROOT_FILES = ("DESIGN.md", "LICENSE", "NOTICE", "CONTRIBUTING.md", "SECURITY.md", ".gitignore", ".gitattributes", ".env.example", "requirements.txt", "requirements-dev.txt", "requirements-portable.txt", "pytest.ini", "Gezhi.spec", "tui/package.json", "tui/package-lock.json")
ASSETS = ("gezhi.ico", "gezhi.png", "gezhi.svg", "gezhi-wordmark.png", "research-study.svg", "remote-control.html", "ui-design.json", "ui-workbench.png", "ui-code-conversation.png")
DOC_FILES = (
    "docs/ui-alignment-2026-10-15.md",
    "docs/OPEN_SOURCE_README.md", "docs/agent-release-2026-09-22.md",
    "docs/release-verification-2026-09-22.json", "docs/release-notes-0.5.0.md",
    "docs/research/performance-2026-09-22.json", "docs/research/2026-09-22-agent-benchmark.md",
    "docs/research/2026-09-24-harness-iteration.md", "docs/research/session-performance-2026-09-24.json",
    "docs/harness-controls-2026-09-27.md", "docs/research/2026-09-27-harness-matrix.md",
    "docs/research/session-projection-performance-2026-09-27.json",
    "docs/release-notes-0.6.0.md", "docs/release-verification-2026-09-28.json",
    "docs/harness-product-2026-09-28.md", "docs/research/2026-09-28-harness-matrix.md",
    "docs/research/tui-startup-2026-09-28.json",
    'docs/release-notes-0.7.0.md', 'docs/harness-product-0.7.0.md',
    'docs/release-0.7.0-source-self-check.json', 'docs/release-0.7.0-offline-eval.json',
    'docs/harness-experiments-2026-09-28.md', 'docs/harness-steering-2026-09-28.md',
    'docs/harness-review-inbox-2026-09-28.md', 'docs/harness-history-index-2026-09-28.md',
    'docs/experimental-verification-2026-09-28.json', 'docs/steering-verification-2026-09-28.json',
    'docs/review-inbox-verification-2026-09-28.json', 'docs/history-index-verification-2026-09-28.json',
    'docs/history-index-tui-wire-2026-09-28.json', 'docs/history-index-http-2026-09-28.json',
    'docs/history-index-lock-stress-2026-09-28.json', 'docs/review-lock-stress-2026-09-28.json',
    'docs/review-inbox-tui-wire-2026-09-28.json', 'docs/review-inbox-wire-2026-09-28.json',
    'docs/steering-wire-verification-2026-09-28.json', 'docs/experimental-workbench-2026-09-28.json',
    'docs/research/2026-09-28-upstream-runtime-source.md', 'docs/research/2026-09-28-upstream-runtime-source.json',
    'docs/research/2026-09-28-gui-source.md', 'docs/research/2026-09-28-gui-source.json',
    'docs/research/2026-09-28-tui-startup-source.md', 'docs/research/2026-09-28-tui-startup-source.json',
    'docs/research/2026-09-28-grok-sdk-source.md', 'docs/research/2026-09-28-grok-sdk-source.json',
    'docs/research/2026-09-28-history-source.json', 'docs/research/2026-09-28-queue-tui-integration.json',
    'docs/research/2026-09-28-experimental-harness-performance.json',
    'docs/research/2026-09-28-experimental-gui-performance.json',
    'docs/research/history-index-performance-2026-09-28.json',
    'docs/research/history-index-gui-performance-2026-09-28.json',
    'docs/harness-tui-input-2026-09-28.md',
    'docs/harness-desktop-drafts-2026-09-28.md', 'docs/desktop-drafts-verification-2026-09-28.json',
    'docs/harness-tool-output-2026-09-28.md', 'docs/tool-output-verification-2026-09-28.json',
    'docs/research/2026-09-28-tool-output-source.md', 'docs/research/2026-09-28-tool-output-source.json',
    'docs/research/tool-output-baseline-2026-09-28.json', 'docs/research/tool-output-performance-2026-09-28.json',
    'docs/research/file-read-performance-2026-09-28.json',
    'docs/desktop-drafts-shutdown-2026-09-28.json', 'docs/desktop-drafts-shutdown-conflict-2026-09-28.json',
    'docs/qa/verify_draft_shutdown.py',
    'docs/research/2026-09-28-draft-source.md', 'docs/research/2026-09-28-draft-source.json',
    'docs/research/desktop-drafts-performance-2026-09-28.json',
    'docs/research/2026-09-28-tui-input-source.md', 'docs/research/2026-09-28-tui-input-source.json',
    'docs/research/tui-input-performance-2026-09-28.json', 'docs/tui-input-verification-2026-09-28.json',
    'docs/harness-interaction-shell-2026-09-28.md', 'docs/shell-interaction-verification-2026-09-28.json',
    'docs/research/2026-09-28-shell-source.md', 'docs/research/2026-09-28-shell-source.json',
    'docs/research/2026-09-28-interaction-polish.md', 'docs/research/shell-capture-performance-2026-09-28.json',
    'docs/shell-native-2026-09-28.json', 'docs/shell-frozen-process-2026-09-28.json',
    'docs/qa/verify_shell_gui.py', 'docs/qa/verify_frozen_process_owner.py',
    'docs/release-notes-0.8.0.md', 'docs/harness-product-0.8.0.md',
    'docs/release-0.8.0-source-self-check.json', 'docs/release-0.8.0-offline-eval.json',
    'docs/release-0.8.0-final-review.md', 'docs/release-0.8.0-provider-readiness.json',
    'docs/shell-interaction-python-tests-final-2026-09-28.log', 'docs/shell-interaction-tui-tests-final-2026-09-28.log',
    'docs/tool-output-python-tests-final-2026-09-28.log', 'docs/tool-output-tui-tests-2026-09-28.log',
    'docs/desktop-drafts-python-tests-final-2026-09-28.log', 'docs/tui-input-tests-2026-09-28.log',
)
PUBLIC_PREVIEWS = (
    'design-previews/tui-startup-2026-09-28',
    'design-previews/tui-input-2026-09-28',
    'design-previews/tui-interaction-2026-09-28',
    'design-previews/2026-09-28-interaction/2026-09-28T22-17-15-ffd164',
    'design-previews/2026-09-28-shell-native/221409-036784d6',
    'design-previews/2026-09-28-drafts/2026-09-28T20-33-11-56e754',
    'design-previews/2026-09-28-drafts/native-close-conflict',
    'design-previews/2026-09-28-tool-output/2026-09-28T21-09-38-17ad81',
    'design-previews/2026-09-28-tool-output/2026-09-28T21-13-22-5dffd4',
    'design-previews/2026-09-28-experimental/2026-09-28T17-13-30-c37c2a',
    'design-previews/2026-09-28-steering/2026-09-28T17-34-42-6274ae',
    'design-previews/2026-09-28-review-inbox/2026-09-28T18-05-37-1daf06',
    'design-previews/2026-09-28-history-index/2026-09-28T18-39-49-70fcc5',
)
_PRIVATE_DIRS = {"__pycache__", "node_modules", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
_PRIVATE_NAMES = {"credentials.json", "secrets.json", "token.json", "tokens.json", ".netrc", ".npmrc", ".pypirc"}


def _excluded(path: Path, root: Path) -> bool:
    name = path.name.lower()
    return (bool(_PRIVATE_DIRS.intersection(path.relative_to(root).parts))
            or path.suffix.lower() in {".pyc", ".pyo", ".log", ".pem", ".key", ".p12", ".pfx"}
            or name in _PRIVATE_NAMES
            or name.startswith('.env') and name not in {'.env.example', '.env.sample'})


def public_files(root: Path = ROOT) -> list[Path]:
    paths = set()
    for tree in TREES:
        for path in (root / tree).rglob("*"):
            if path.is_file() and not path.is_symlink() and not _excluded(path, root):
                paths.add(path)
    for name in (*ROOT_FILES, *DOC_FILES):
        if (root / name).is_file():
            paths.add(root / name)
    paths.update(root / "assets" / name for name in ASSETS if (root / "assets" / name).is_file())
    for folder in PUBLIC_PREVIEWS:
        paths.update(path for path in (root / folder).glob('*') if path.is_file()
                     and path.suffix in {'.png', '.json', '.html', '.txt'} and not path.is_symlink())
    for path in (root / "scripts").glob('*'):
        if path.is_file() and path.suffix in {".py", ".ps1", ".bat"} and not path.name.startswith("presentation") and not _excluded(path, root):
            paths.add(path)
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Public source path escaped the workspace")
    return sorted(paths)


def _release_tag(value: str) -> str:
    try:
        if not re.fullmatch(r'\d{8}', value):
            raise ValueError
        datetime.strptime(value, '%Y%m%d')
    except ValueError as exc:
        raise argparse.ArgumentTypeError('--tag must be a real date in YYYYMMDD format') from exc
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', type=_release_tag, help='Create a separate dated release without overwriting previous deliverables')
    parser.add_argument('--versioned-dir', action='store_true', help='Also include the release version in the extracted source directory')
    # Existing Python callers use main() without inheriting unittest/pytest argv.
    args = parser.parse_args([] if argv is None else argv)
    tag = args.tag
    suffix = f'-{tag}' if tag else ''
    destination = ROOT / 'deliverables' / f'scizhi-agent{"-" + VERSION if args.versioned_dir else ""}{suffix}'
    archive = ROOT / 'deliverables' / f'SciZhi-Agent-{VERSION}{suffix}-source.zip'
    if (tag or args.versioned_dir) and (destination.exists() or archive.exists()):
        raise FileExistsError(f'Dated release already exists; refusing to overwrite {destination.name} or {archive.name}')
    if not destination.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Release destination escaped the workspace')
    sources = public_files(ROOT)
    readme = ROOT / 'docs' / 'OPEN_SOURCE_README.md'
    if not readme.is_file() or readme.is_symlink() or not readme.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('A workspace-owned docs/OPEN_SOURCE_README.md is required')
    destination.mkdir(parents=True, exist_ok=tag is None)
    copied: set[Path] = set()
    for source in sources:
        target = destination / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.add(target)
    shutil.copy2(readme, destination / "README.md")
    copied.add(destination / "README.md")
    files = sorted(copied)
    manifest = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    (destination / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    with zipfile.ZipFile(archive, 'x' if tag else 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in files + [destination / "SOURCE_MANIFEST.json"]:
            bundle.write(path, Path(f"scizhi-agent-{VERSION}{suffix}") / path.relative_to(destination))
    print(json.dumps({"files": len(manifest), 'tag': tag, 'destination': str(destination), "archive": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
