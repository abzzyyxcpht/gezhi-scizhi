@echo off
chcp 65001 >nul
cd /d %~dp0..
set "SCIZHI_RELEASE_VERSION="
for /f "delims=" %%V in ('python -c "from src.version import APP_VERSION; print(APP_VERSION)"') do set "SCIZHI_RELEASE_VERSION=%%V"
if not defined SCIZHI_RELEASE_VERSION exit /b 1
python -m PyInstaller --noconfirm --distpath "dist\%SCIZHI_RELEASE_VERSION%" --workpath "build\desktop-%SCIZHI_RELEASE_VERSION%" Gezhi.spec
if errorlevel 1 exit /b 1
python scripts\pack_release.py --app-dir "dist\%SCIZHI_RELEASE_VERSION%\Gezhi"
if errorlevel 1 exit /b 1
