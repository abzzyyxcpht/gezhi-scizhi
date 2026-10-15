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
!include "FileFunc.nsh"
Var IsolatedMode
Var IsolatedDir
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

; /ISOLATED /D=<new absolute local directory> is for installation QA only.
; NSIS removes /D from $CMDLINE, so validate it using the original command line.
Function .onInit
    StrCpy $IsolatedMode 0
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} "$0" "/ISOLATED" $1
    IfErrors init_done
    StrCmp $1 "" 0 isolated_invalid
    StrCpy $IsolatedMode 1
    System::Call 'kernel32::GetCommandLineW() w .r0'
    ClearErrors
    ${GetOptions} "$0" "/D=" $1
    IfErrors isolated_invalid
    StrCmp $1 "" isolated_invalid
    ; Require C:\... syntax; PathIsRelativeW also accepts C:relative and \relative.
    StrCpy $2 "$1" 1 1
    StrCmp $2 ":" 0 isolated_invalid
    StrCpy $2 "$1" 1 2
    StrCmp $2 "\" 0 isolated_invalid
    System::Call 'shlwapi::PathGetDriveNumberW(w r1) i .r2'
    IntCmp $2 0 0 isolated_invalid 0
    ; NSIS GetFullPathName requires an existing path. The Win32 API also handles new targets.
    System::Call 'kernel32::GetFullPathNameW(w r1, i ${NSIS_MAX_STRLEN}, w .r2, p 0) i .r3'
    IntCmp $3 0 isolated_invalid isolated_invalid 0
    IntCmp $3 ${NSIS_MAX_STRLEN} isolated_invalid 0 isolated_invalid
    StrCpy $IsolatedDir $2
    ; GetOptions stops at forward slashes. Never install into a truncated path.
    System::Call 'kernel32::GetFullPathNameW(w "$INSTDIR", i ${NSIS_MAX_STRLEN}, w .r2, p 0) i .r3'
    IntCmp $3 0 isolated_invalid isolated_invalid 0
    IntCmp $3 ${NSIS_MAX_STRLEN} isolated_invalid 0 isolated_invalid
    StrCmp $2 $IsolatedDir 0 isolated_invalid
    StrCmp $IsolatedDir "$LOCALAPPDATA\Programs\格致" isolated_invalid
    StrCpy $INSTDIR $IsolatedDir
    Call ValidateIsolatedDirectory
    SetSilent silent
    Goto init_done
isolated_invalid:
    SetErrorLevel 2
    Abort "/ISOLATED requires /D= with a new absolute local installation directory."
init_done:
FunctionEnd

Function ValidateIsolatedDirectory
    StrCmp $INSTDIR $IsolatedDir 0 isolated_directory_invalid
    IfFileExists "$INSTDIR" isolated_directory_invalid
    IfFileExists "$INSTDIR\*.*" isolated_directory_invalid
    Return
isolated_directory_invalid:
    SetErrorLevel 2
    Abort "The isolated installation directory must not already exist."
FunctionEnd

; GetFileAttributesW inspects the entry itself. Preflight visits parents first.
Function un.AssertPlainPath
    Exch $0
    Push $1
    Push $2
    System::Call 'kernel32::GetFileAttributesW(w r0) i .r1 ?e'
    Pop $2
    IntCmp $1 -1 path_missing path_exists path_exists
path_missing:
    IntCmp $2 2 path_verified
    IntCmp $2 3 path_verified
    Goto path_unsafe
path_exists:
    IntOp $1 $1 & 0x400
    IntCmp $1 0 path_verified
path_unsafe:
    MessageBox MB_OK|MB_ICONSTOP "卸载已停止：安装路径包含链接或无法检查。请恢复原目录，或手动处理后重试。$\r$\n$0" /SD IDOK
    SetErrorLevel 2
    Abort "Installation path changed; no files or global entries were removed."
path_verified:
    Pop $2
    Pop $1
    Pop $0
FunctionEnd

Section "Install"
    StrCmp $IsolatedMode 1 0 normal_destination
    Call ValidateIsolatedDirectory
    Goto install_ready
normal_destination:
    IfFileExists "$INSTDIR\.scizhi-isolated" 0 install_ready
    SetErrorLevel 2
    Abort "Choose a different directory from the isolated test installation."
install_ready:
    SetOutPath "$INSTDIR"
    StrCmp $IsolatedMode 1 0 install_files
    ClearErrors
    FileOpen $0 "$INSTDIR\.scizhi-isolated" w
    IfErrors marker_failed
    FileWrite $0 "SciZhi isolated installation ${APP_VER}"
    FileClose $0
    Goto install_files
marker_failed:
    SetErrorLevel 2
    Abort "Could not mark the isolated installation."
install_files:
    File /r "app\*.*"
    File /oname=gezhi-${APP_VER}.ico "gezhi.ico"
    WriteUninstaller "$INSTDIR\卸载.exe"
    StrCmp $IsolatedMode 1 install_done
    CreateDirectory "$SMPROGRAMS\格致"
    CreateShortcut "$SMPROGRAMS\格致\格致 SciZhi.lnk" "$INSTDIR\Gezhi.exe" "" "$INSTDIR\gezhi-${APP_VER}.ico" 0
    CreateShortcut "$SMPROGRAMS\格致\卸载.lnk" "$INSTDIR\卸载.exe"
    CreateShortcut "$DESKTOP\格致 SciZhi.lnk" "$INSTDIR\Gezhi.exe" "" "$INSTDIR\gezhi-${APP_VER}.ico" 0
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi" "UninstallString" '"$INSTDIR\卸载.exe"' 
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi" "DisplayVersion" "${APP_VER}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi" "Publisher" "${PUBLISHER}"
install_done:
SectionEnd

Section "Uninstall"
    !include "uninstall-preflight.nsh"
    IfFileExists "$INSTDIR\.scizhi-isolated" uninstall_files
    Delete "$SMPROGRAMS\格致\格致 SciZhi.lnk"
    Delete "$SMPROGRAMS\格致\卸载.lnk"
    RMDir "$SMPROGRAMS\格致"
    Delete "$DESKTOP\格致 SciZhi.lnk"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Gezhi"
uninstall_files:
    !include "uninstall-files.nsh"
    Delete "$INSTDIR\gezhi-${APP_VER}.ico"
    Delete "$INSTDIR\.scizhi-isolated"
    Delete "$INSTDIR\卸载.exe"
    SetOutPath "$TEMP"
    RMDir "$INSTDIR"
SectionEnd
