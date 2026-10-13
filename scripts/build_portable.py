# 格致 SciZhi · TUI 便携包构建（Windows x64，解压即用，无需安装）
# 构成：便携 Python 3.13 embeddable + 预装依赖 + 便携 Node + 源码 + node_modules + 启动 bat
# 运行: python scripts/build_portable.py [--skip-download] [--skip-deps]
"""Build the portable (no-install) TUI distribution.

Layout produced under ``dist/``:
    格致SciZhi-便携版-<ver>-win64/
        Gezhi.bat           双击启动（OpenTUI 引擎）
        python/             embeddable Python + requirements-portable.txt 预装依赖
        node/node.exe       便携 Node（构建机同架构单文件）
        src/ tui/ workflows/ skills/ plugins/ examples/ assets/
        data/               运行时数据（隔离，不写系统目录）

Dependencies are installed with the build machine's pip into the portable target,
so the embeddable interpreter and the build interpreter must share the same
Python version (3.13).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.version import APP_VERSION  # noqa: E402 -- direct script entry adds the repository root

EMBED_URL = "https://www.python.org/ftp/python/3.13.9/python-3.13.9-embed-amd64.zip"
EMBED_ZIP = "python-3.13.9-embed-amd64.zip"
PORTABLE_REQ = ROOT / "requirements-portable.txt"
SOURCE_DIRS = ("src", "tui/src", "tui/package.json", "tui/package-lock.json", "tui/node_modules",
               "workflows", "skills", "plugins", "examples", "assets")
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build", "deliverables",
                "design-previews", "docs", "data", "logs", "checkpoints", "uploads", "cache",
                "artifacts", "exports", "research", "sessions"}

BAT = """@echo off
rem 格致 SciZhi 便携版启动器（OpenTUI 引擎）
chcp 65001 >nul
setlocal EnableExtensions
set "HERE=%~dp0"
set "GEZHI_DATA_DIR=%HERE%data"
if not exist "%GEZHI_DATA_DIR%" mkdir "%GEZHI_DATA_DIR%" >nul 2>&1
set "PATH=%HERE%python;%HERE%node;%PATH%"

echo 正在启动格致 SciZhi（便携版）...
node "%HERE%tui\\src\\cli.js" --version >nul 2>&1
if errorlevel 1 (
  echo [启动失败] Node 运行时异常（退出码 %errorlevel%）。
  echo 请从 CMD 手动运行本脚本查看完整错误；若杀毒软件拦截请放行后重试。
  pause
  exit /b 1
)
"%HERE%python\\python.exe" -c "import yaml" >nul 2>&1
if errorlevel 1 (
  echo [启动失败] Python 运行时异常（退出码 %errorlevel%）。
  pause
  exit /b 1
)
node "%HERE%tui\\src\\cli.js" %*
if errorlevel 1 (
  echo.
  echo [异常退出] 退出码 %errorlevel%。
  echo 请从 CMD 手动运行本脚本查看完整错误。
  pause
)
endlocal
"""

README = """# 格致 SciZhi 便携版 {ver}（Windows x64）

## 直接运行

双击 `Gezhi.bat` 即可启动（OpenTUI 引擎，双模式 ◇ 通用 / ◎ 科研）。

## 命令行

    Gezhi.bat                     交互式 TUI
    Gezhi.bat --print "研究请求"   单轮问答（headless）

## 说明

- 数据目录：`./data`（与本机构成自包含，不写系统目录）
- 便携 Python：`./python`（3.13 embeddable，依赖已预装）
- 便携 Node：`./node/node.exe`
- 全部功能（含科研创新面板：/hypotheses /consensus /matrix /experiments /facts /goal /novelty /peer-review /reproduce）
- 交互引擎：OpenTUI（0.9.0 起唯一引擎，node_modules 已剔除 Ink 遗留包）
- 模型配置：首次运行 `gezhi --print "test"` 或 TUI 内 /settings、/connections 配置；离线范例 /demo

## 目录

    src/       Python 引擎（bridge + orchestrator + agents + connectors）
    tui/       Node TUI（OpenTUI 引擎，node_modules 已随附）
    python/    便携 Python 运行时
    node/      便携 Node 运行时
    data/      运行时数据（任务/会话/导出）
"""


def run(cmd: list[str], **kw) -> None:
    print("[portable]", " ".join(str(c) for c in cmd)[:160])
    subprocess.run(cmd, check=True, **kw)


def download_embed(app_dir: Path) -> None:
    zip_path = app_dir.parent / EMBED_ZIP
    if not zip_path.is_file():
        print(f"[portable] 下载 embeddable Python：{EMBED_URL}")
        urllib.request.urlretrieve(EMBED_URL, zip_path)
    python_dir = app_dir / "python"
    python_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(python_dir)
    # embeddable 默认关闭 site；开启并注册 site-packages
    pth = next(python_dir.glob("python313._pth"), None)
    if pth is None:
        raise SystemExit("未找到 python313._pth（embeddable 版本不符？）")
    lines = [line for line in pth.read_text(encoding="utf-8").splitlines() if line.strip()]
    lines = [line for line in lines if line != "import site"]
    lines.append("import site")
    lines.append("..")  # embeddable 默认 isolated 且相对路径基于 exe 目录：上级即项目根（src/ 所在）
    lines.append("Lib\\site-packages")
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install_deps(app_dir: Path) -> None:
    target = app_dir / "python" / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "pip", "install", "--upgrade-strategy", "only-if-needed",
         "--no-compile", "--target", str(target), "-r", str(PORTABLE_REQ)])


def _source_ignore(_dir, names):
    """源码树排除：构建产物/缓存/数据目录 + 字节码。"""
    return [n for n in names if n in EXCLUDE_DIRS or n.endswith((".pyc", ".pyo"))]


INK_PACKAGES = {"ink", "ink-select-input", "ink-spinner", "ink-text-input", "ink-testing-library"}


def _node_modules_ignore(_dir, names):
    """node_modules 排除：字节码 + Ink 遗留包（0.9.0 起产品侧唯一引擎为 OpenTUI）。

    警告：不得在此应用 EXCLUDE_DIRS——依赖包内合法的 build/dist/data/cache
    目录（如 @mbears/*/dist）会被误删导致引擎无法启动。
    """
    dropped = [n for n in names if n.endswith((".pyc", ".pyo"))]
    for n in names:
        if n in INK_PACKAGES or any(n.startswith(p + "@") for p in INK_PACKAGES):
            dropped.append(n)
    return dropped


def copy_sources(app_dir: Path) -> None:
    for item in SOURCE_DIRS:
        src = ROOT / item
        dst = app_dir / item
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            continue
        if not src.is_dir():
            raise SystemExit(f"缺少源目录：{item}")
        ignore = _node_modules_ignore if item == "tui/node_modules" else _source_ignore
        shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)
    (app_dir / "data").mkdir(exist_ok=True)


def copy_node(app_dir: Path) -> None:
    node = shutil.which("node")
    if not node:
        raise SystemExit("构建机 PATH 中未找到 node.exe")
    dst_dir = app_dir / "node"
    dst_dir.mkdir(exist_ok=True)
    shutil.copy2(node, dst_dir / "node.exe")


def smoke(app_dir: Path) -> None:
    """便携包最小验证：解释器依赖、node_modules 完整性、CLI --version。"""
    py = app_dir / "python" / "python.exe"
    run([str(py), "-c", "import yaml, httpx, pandas, numpy, matplotlib, sklearn, pypdf; print('portable python deps ok')"])
    # node_modules 完整性：引擎入口文件必须在（曾因 ignore 规则误删依赖 build 目录导致引擎挂起）
    nm = app_dir / "tui" / "node_modules"
    required = [nm / "@mbears" / "opentui-react",
                nm / "@mbears" / "opentui-core",
                nm / "react"]
    missing = [str(p.relative_to(app_dir)) for p in required if not p.exists()]
    if missing:
        raise SystemExit(f"node_modules 不完整，缺少：{missing}")
    ink_leftover = nm / "ink"
    if ink_leftover.exists():
        raise SystemExit("node_modules 仍包含 Ink 遗留包（0.9.0 起应剔除）")
    print("[portable] node_modules 完整性 OK（@mbears/* + react，无 Ink 遗留）")
    env_extra = {"GEZHI_DATA_DIR": str(app_dir / "data")}
    import os
    env = dict(os.environ, **env_extra)
    run([str(app_dir / "node" / "node.exe"), str(app_dir / "tui" / "src" / "cli.js"), "--version"], env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-download", action="store_true", help="复用已解压的便携 Python")
    parser.add_argument("--skip-deps", action="store_true", help="跳过依赖安装（已装过）")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    work = ROOT / "_testtmp" / "portable-build"
    app_dir = work / f"格致SciZhi-便携版-{APP_VERSION}-win64"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    app_dir.mkdir(parents=True)

    if not args.skip_download:
        download_embed(app_dir)
    if not args.skip_deps:
        install_deps(app_dir)
    copy_sources(app_dir)
    copy_node(app_dir)
    (app_dir / "Gezhi.bat").write_text(BAT, encoding="utf-8")
    (app_dir / "README.md").write_text(README.format(ver=APP_VERSION), encoding="utf-8")

    smoke(app_dir)

    out = ROOT / "dist" / f"格致SciZhi-便携版-{APP_VERSION}-win64.zip"
    out.parent.mkdir(exist_ok=True)
    if out.exists():
        out.unlink()
    print(f"[portable] 打包 {out} …")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file() and "data" not in path.parts:
                zf.write(path, path.relative_to(work))
    size_mb = out.stat().st_size / 1048576
    print(f"[portable] 完成：{out}（{size_mb:.0f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
