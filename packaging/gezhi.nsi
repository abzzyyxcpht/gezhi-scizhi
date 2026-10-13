; 格致 SciZhi 安装包脚本（NSIS，免管理员安装到用户目录）
; 构建：python scripts\pack_release.py（从 src/version.py 注入版本）
!define APP_NAME "格致 SciZhi"
!ifndef APP_VER
!error "Pass APP_VER from src/version.py through scripts/pack_release.py"
!endif
!define PUBLISHER "格致团队"

Unicode true
RequestExecutionLevel user
Name "${APP_NAME}"
OutFile "SciZhi-Agent-Setup-${APP_VER}.exe"
Icon "gezhi.ico"
InstallDir "$LOCALAPPDATA\Programs\格致"
SetCompressor /SOLID zlib

!include "MUI2.nsh"
!define MUI_ICON "gezhi.ico"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

VIProductVersion "${APP_VER}.0"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "FileDescription" "SciZhi Agent Installer"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "FileVersion" "${APP_VER}"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "ProductVersion" "${APP_VER}"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "LegalCopyright" "${PUBLISHER}"

Section "Install"
    SetOutPath "$INSTDIR"
    File /r "app\*.*"
    WriteUninstaller "$INSTDIR\卸载.exe"
    CreateDirectory "$SMPROGRAMS\格致"
    CreateShortcut "$SMPROGRAMS\格致\格致 SciZhi.lnk" "$INSTDIR\Gezhi.exe" "" "$INSTDIR\Gezhi.exe" 0
    CreateShortcut "$SMPROGRAMS\格致\卸载.lnk" "$INSTDIR\卸载.exe"
    CreateShortcut "$DESKTOP\格致 SciZhi.lnk" "$INSTDIR\Gezhi.exe" "" "$INSTDIR\Gezhi.exe" 0
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi" "UninstallString" '"$INSTDIR\卸载.exe"' 
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi" "DisplayVersion" "${APP_VER}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi" "Publisher" "${PUBLISHER}"
SectionEnd

Section "Uninstall"
    RMDir /r "$INSTDIR"
    RMDir /r "$SMPROGRAMS\格致"
    Delete "$DESKTOP\格致 SciZhi.lnk"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi"
SectionEnd
