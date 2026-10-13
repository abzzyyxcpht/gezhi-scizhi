# 格致 SciZhi · 桌面版便携包构建（PyInstaller onedir 即便携结构：解压即用，无需安装）
# 运行: python scripts/build_desktop_portable.py
"""Build the portable (no-install) desktop distribution.

The PyInstaller onedir output (``dist/<ver>/Gezhi``) is inherently portable: it
bundles the Python runtime and all dependencies. This script stages it with a
data-isolated launcher and zips it to ``dist/``.
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.version import APP_VERSION  # noqa: E402 -- direct script entry adds the repository root

SOURCE = ROOT / "dist" / APP_VERSION / "Gezhi"

BAT = """@echo off
rem 格致 SciZhi 桌面便携版启动器（数据隔离在包内 data\\，不写系统目录）
chcp 65001 >nul
setlocal EnableExtensions
set "HERE=%~dp0"
set "GEZHI_DATA_DIR=%HERE%data"
if not exist "%GEZHI_DATA_DIR%" mkdir "%GEZHI_DATA_DIR%" >nul 2>&1
if not exist "%HERE%Gezhi.exe" (
  echo [启动失败] 未找到 Gezhi.exe；请确认压缩包已完整解压。
  pause
  exit /b 1
)
start "" "%HERE%Gezhi.exe"
endlocal
"""

README = """# 格致 SciZhi 桌面便携版 {ver}（Windows x64）

## 直接运行

双击 `Gezhi-桌面版.bat` 启动桌面端（Flet GUI，内置 Python 运行时，无需安装）。

## 说明

- 数据目录：`./data`（包内隔离，不写系统目录）
- 首次运行可能需要几秒钟初始化；若杀毒软件拦截请放行后重试
- 终端 TUI 不在本包内：TUI 需要 Node 运行时，请使用 `格致SciZhi-便携版-<ver>-win64.zip`
- 退出：关闭窗口即可；数据与会话保存在 `./data`

## 目录

    Gezhi.exe      桌面端入口
    _internal/     Python 运行时与依赖（请勿单独移动文件）
    data/          运行时数据（自动创建）
"""


def main() -> int:
    if not (SOURCE / "Gezhi.exe").is_file():
        raise SystemExit(f"缺少 {SOURCE}/Gezhi.exe；先运行 scripts/build_desktop.bat 构建桌面端")
    work = ROOT / "_testtmp" / "desktop-portable-build"
    app = work / f"格致SciZhi-桌面便携版-{APP_VERSION}-win64"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    print("[desktop-portable] 复制 onedir（约 700MB）…")
    shutil.copytree(SOURCE, app / "Gezhi", dirs_exist_ok=True)
    (app / "Gezhi" / "data").mkdir(exist_ok=True)
    # 启动 bat 放外层，指向内层目录
    bat_here = app
    (bat_here / "Gezhi-桌面版.bat").write_text(
        BAT.replace("%HERE%Gezhi.exe", "%HERE%Gezhi\\Gezhi.exe").replace("%HERE%data", "%HERE%Gezhi\\data"),
        encoding="utf-8")
    (app / "README.md").write_text(README.format(ver=APP_VERSION), encoding="utf-8")

    # 冒烟：入口与运行时目录在位
    required = [app / "Gezhi" / "Gezhi.exe", app / "Gezhi" / "_internal"]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit(f"桌面便携包不完整：{missing}")
    print("[desktop-portable] 结构 OK（Gezhi.exe + _internal）")

    out = ROOT / "dist" / f"格致SciZhi-桌面便携版-{APP_VERSION}-win64.zip"
    if out.exists():
        out.unlink()
    print(f"[desktop-portable] 打包 {out} …")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for path in sorted(app.rglob("*")):
            if path.is_file() and path.suffix != ".pyc":
                zf.write(path, path.relative_to(work))
    size_mb = out.stat().st_size / 1048576
    print(f"[desktop-portable] 完成：{out}（{size_mb:.0f} MB）")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
