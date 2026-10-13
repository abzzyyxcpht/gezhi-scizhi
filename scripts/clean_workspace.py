"""清理明确可再生的工作区缓存；默认只预览，--apply 才执行。

    python scripts/clean_workspace.py
    python scripts/clean_workspace.py --json
    python scripts/clean_workspace.py --apply --json

保留全部 data、dist、deliverables、assets、design-previews、tools、依赖和
其他 CLI 的状态。根目录仅删除逐项确认的一次性文件；不按前缀、数量或
时间裁剪用户记录。build 只允许带 PyInstaller 标志的 build/Gezhi 或
build/desktop-X.Y.Z/Gezhi 子目录，版本安装包与构建日志保留。
符号链接、junction 和其他 reparse point 不进入删除树。失败返回非零；
removed_bytes 仅累计已复核删除的普通文件字节数，不代表物理磁盘释放量。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# These files were inspected and explicitly marked as disposable probes.
# 2026-10-12: _audit_pages.py and _build_docx.py were retired as superseded one-off
# helpers (planning-book build/audit moved into deliverables and docs tooling).
ROOT_DISPOSABLE_FILES = frozenset({
    "debug.log", "_cleanup.ps1", "_color_audit.py", "_del_probe.txt", "_final_check.py",
    "_import_smoke.py", "_inventory.ps1", "_layout_audit.py", "_lint_ctx.py", "_palette.py",
    "_probe_eof.py", "_probe_pdf.py", "_run_pdf_tests.py", "_run_tests.py", "_shotmap.py",
    "_verify_clean.py", "_verify_gui.py", "_verify_pdf_cmd.py", "_verify_pdf.py",
    "_verify_tui_layout.js", "格致.spec", "_audit_pages.py", "_build_docx.py",
    "nav-dump.json", "格致-桌面版.bat", "格致-终端版.bat",
    "03_项目计划书.pdf", "03_项目计划书.docx",
})
ROOT_CACHE_DIRS = (".pytest_cache", ".ruff_cache", "_testtmp")
PROTECTED_DIRS = frozenset({
    "data", "dist", "deliverables", "assets", "design-previews", "tools", "node_modules",
    "build", ".git", ".venv", "venv", ".codely-cli", ".mimosa",
})


def human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1048576:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1048576:.2f} MB"


def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def _within_workspace(root: Path, target: Path) -> None:
    """Validate both the lexical path and every parent before resolving the target."""
    relative = target.relative_to(root)
    if not relative.parts or ".." in relative.parts:
        raise ValueError("拒绝清理工作区根目录或上级路径")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if _linked(cursor):
            raise ValueError("拒绝符号链接、junction 或 reparse point")
    resolved = target.resolve(strict=True)
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("清理目标超出工作区")


def _tree_size(root: Path, target: Path, kind: str) -> int:
    """Check the complete tree without following links, before deleting any of it."""
    _within_workspace(root, target)
    info = target.lstat()
    if kind == "file":
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("目标应为普通文件，实际类型不符")
        return info.st_size
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("目标应为目录，实际类型不符")
    total = 0
    pending = [target]
    while pending:
        directory = pending.pop()
        _within_workspace(root, directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                _within_workspace(root, path)
                details = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(details.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(details.st_mode):
                    total += details.st_size
                else:
                    raise ValueError("清理目录包含非普通文件")
    return total


def _remove_tree(root: Path, target: Path) -> None:
    def retry_readonly_file(operation, failed_path, error):
        path = Path(failed_path)
        _within_workspace(root, path)
        if not path.is_relative_to(target):
            raise ValueError('删除回调超出已核验目标')
        info = path.lstat()
        # Git packs are read-only on Windows. Only clear that ordinary file
        # attribute; other permission errors and linked targets stay failures.
        if (os.name != 'nt' or not isinstance(error, PermissionError)
                or not stat.S_ISREG(info.st_mode) or info.st_mode & stat.S_IWRITE):
            raise error
        path.chmod(info.st_mode | stat.S_IWRITE)
        operation(path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(target, onexc=retry_readonly_file)
    else:
        shutil.rmtree(target, onerror=lambda operation, path, info: retry_readonly_file(operation, path, info[1]))


def collect_root_junk(root: Path | None = None) -> list[Path]:
    """Explicit allowlist only; unknown underscore files and backups are preserved."""
    root = ROOT if root is None else root
    candidates = [root / name for name in sorted(ROOT_DISPOSABLE_FILES)]
    candidates.append(root / "scripts" / "clean_intermediates.ps1")
    return [path for path in candidates if os.path.lexists(path)]


def _collect(root: Path) -> list[tuple[Path, str, str]]:
    candidates = [(path, "file", "已确认一次性文件") for path in collect_root_junk(root)]
    for name in ROOT_CACHE_DIRS:
        path = root / name
        if os.path.lexists(path):
            candidates.append((path, "directory", "可再生缓存"))
    for path in root.glob(".chart-data-*"):
        if _linked(path) or path.is_dir():
            candidates.append((path, "directory", "绘图临时数据"))

    # The packaging entry point uses Gezhi.spec. Require both known PyInstaller
    # markers; a user directory named build/Gezhi alone is not disposable.
    build_paths = [root / 'build' / 'Gezhi']
    build_paths.extend(path / 'Gezhi' for path in (root / 'build').glob('desktop-*')
                       if re.fullmatch(r'desktop-[0-9]+\.[0-9]+\.[0-9]+', path.name))
    for build in build_paths:
        if not os.path.lexists(build):
            continue
        try:
            _within_workspace(root, build)
        except (OSError, ValueError, RuntimeError):
            candidates.append((build, "directory", "拒绝未经验证的构建路径"))
        else:
            if all((build / marker).is_file() for marker in ("Analysis-00.toc", "PYZ-00.toc")):
                candidates.append((build, "directory", "已确认 PyInstaller 缓存"))

    selected = {path for path, _, _ in candidates}
    def scan_error(error):
        raise error
    for directory, children, _ in os.walk(root, topdown=True, followlinks=False, onerror=scan_error):
        retained = []
        for name in children:
            path = Path(directory) / name
            if path in selected or name.casefold() in PROTECTED_DIRS:
                continue
            if name == "__pycache__":
                candidates.append((path, "directory", "Python 字节码缓存"))
            elif not name.startswith(".") and not _linked(path):
                retained.append(name)
        children[:] = retained

    # Parent candidates own their descendants, so byte accounting and deletion
    # cannot process _testtmp/a/__pycache__ a second time.
    unique = []
    for candidate in sorted(candidates, key=lambda row: (len(row[0].parts), str(row[0]).casefold())):
        if not any(candidate[0] == row[0] or candidate[0].is_relative_to(row[0]) for row in unique):
            unique.append(candidate)
    return unique


def clean_workspace(root: Path | None = None, *, apply: bool = False) -> dict[str, Any]:
    root = Path(ROOT if root is None else root).resolve(strict=True)
    report: dict[str, Any] = {
        "root": str(root), "mode": "apply" if apply else "dry-run", "ok": True,
        "targets": [], "planned_bytes": 0, "removed_bytes": 0, "errors": [],
    }
    try:
        candidates = _collect(root)
    except (OSError, ValueError, RuntimeError) as error:
        report["ok"] = False
        report["errors"].append(f"无法完整检查工作区：{error}")
        return report
    for path, kind, reason in candidates:
        row = {"path": path.relative_to(root).as_posix(), "kind": kind,
               "reason": reason, "bytes": 0, "status": "planned"}
        report["targets"].append(row)
        try:
            row["bytes"] = _tree_size(root, path, kind)
            report["planned_bytes"] += row["bytes"]
        except (OSError, ValueError, RuntimeError) as error:
            row.update(status="refused", error=str(error))
            report["errors"].append(f"{row['path']}：{error}")
            continue
        if apply:
            try:
                # Revalidate immediately before mutation, including nested links.
                removed_size = _tree_size(root, path, kind)
                if kind == "directory":
                    _remove_tree(root, path)
                else:
                    path.unlink()
                if os.path.lexists(path):
                    raise OSError("删除后目标仍存在，未计入已删除字节")
                row["status"] = "removed"
                row["removed_bytes"] = removed_size
                report["removed_bytes"] += removed_size
            except (OSError, ValueError, RuntimeError) as error:
                row.update(status="failed", error=str(error))
                report["errors"].append(f"{row['path']}：{error}")
    report["ok"] = not report["errors"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="执行预览清单内的安全清理")
    parser.add_argument("--json", action="store_true", help="向标准输出写入 JSON 报告")
    args = parser.parse_args(argv)
    report = clean_workspace(apply=args.apply)
    if args.json:
        # ASCII escapes keep JSON lossless through Windows GBK/UTF-8 pipelines.
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print(f"{'执行' if args.apply else '干跑预览'}：{report['root']}")
        for row in report["targets"]:
            print(f"  {row['status']:8} {row['path']}  {human(row['bytes'])}  {row['reason']}")
        for error in report["errors"]:
            print(f"  失败：{error}")
        field = "removed_bytes" if args.apply else "planned_bytes"
        print(f"{'已删除文件字节' if args.apply else '预计删除文件字节'}：{human(report[field])}")
        if not args.apply:
            print("未删除文件；加 --apply 执行。可加 --json 保存完整清单。")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
