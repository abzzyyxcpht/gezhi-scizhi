"""Stage research exports and restore previous files if publication fails."""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from . import aigc


MANAGED_FILES = frozenset({
    "文献摘要整理稿.md", "文献综述框架.md", "参考文献.txt", "文献池.csv",
    "证据图谱.png", "科研证据审计.md", "科研证据台账.json", "manifest.md",
})
_EXPORT_LOCK = threading.RLock()


def _plain_path(path: Path, *, directory: bool) -> bool:
    """Do not follow links or replace directories that happen to share a file name."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(info.st_mode) or reparse:
        raise OSError(f"导出路径不能是链接：{path}")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise OSError(f"导出路径类型冲突：{path}")
    return True


def _plain_directory(path: Path) -> None:
    for component in (path, *path.parents):
        _plain_path(component, directory=True)


def _previous_files(out: Path) -> set[str]:
    manifest = out / "manifest.md"
    if not _plain_path(manifest, directory=False):
        return set()
    try:
        contents = manifest.read_text(encoding="utf-8")
    except UnicodeError:
        return set()
    if (not contents.startswith("# 导出清单（含 AI 参与度说明）\n")
            or "| 文件 | 内容类型 | AI 参与 | 说明 |" not in contents
            or f"{aigc.MARK}——清单所列 AI 参与文件均带正文标识区" not in contents):
        return set()
    rows = {line.split("|", 2)[1].strip()
            for line in contents.splitlines() if line.startswith("| ") and line.count("|") >= 5}
    return rows & MANAGED_FILES


def export_files(destination: Path, render: Callable[[Path], list[str]]) -> list[str]:
    """Publish a complete file set; leave unrelated files in the destination alone.

    Calls within this process are serialized. The manifest is published last. If a
    filesystem error also prevents rollback, retain the recovery directory and
    report its path instead of discarding the only copies of previous results.
    """
    requested = Path(destination)
    out = Path(os.path.abspath(requested))
    with _EXPORT_LOCK:
        _plain_directory(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".scizhi-export-", dir=out.parent))
        keep_recovery = False
        created_out = False
        try:
            staging = work / "files"
            staging.mkdir()
            written = render(staging)
            names = []
            for entry in written:
                path = Path(entry)
                if path.parent != staging or path.name not in MANAGED_FILES:
                    raise ValueError("导出文件不属于受管理的成果清单")
                _plain_path(path, directory=False)
                if path.name in names:
                    raise ValueError("导出文件清单存在重复项")
                names.append(path.name)
            if "manifest.md" not in names:
                raise ValueError("导出文件清单缺少 manifest.md")
            _plain_directory(out)
            previous = _previous_files(out) if out.exists() else set()
            stale = previous - set(names)
            affected = set(names) | stale
            existing = {name for name in affected if _plain_path(out / name, directory=False)}
            backup = work / "previous"
            backup.mkdir()
            for name in sorted(existing):
                shutil.copy2(out / name, backup / name)
            if not out.exists():
                out.mkdir()
                created_out = True
            changed: list[str] = []
            try:
                for name in names:
                    if name != "manifest.md":
                        os.replace(staging / name, out / name)
                        changed.append(name)
                for name in sorted(stale & existing):
                    (out / name).unlink()
                    changed.append(name)
                os.replace(staging / "manifest.md", out / "manifest.md")
                changed.append("manifest.md")
            except BaseException as error:
                recovery_errors = []
                for name in reversed(changed):
                    try:
                        if name in existing:
                            os.replace(backup / name, out / name)
                        else:
                            (out / name).unlink(missing_ok=True)
                    except OSError as exc:
                        recovery_errors.append(exc)
                if created_out:
                    try:
                        out.rmdir()
                    except OSError:
                        pass
                if recovery_errors:
                    keep_recovery = True
                    raise OSError(f"导出未完成，部分旧文件需要恢复；备份保存在：{work}") from error
                raise
            return [str(requested / name) for name in names]
        finally:
            if not keep_recovery:
                shutil.rmtree(work)
