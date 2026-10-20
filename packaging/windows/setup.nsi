Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

Name "The Fourth Institute"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\TheFourthInstitute"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ManifestDPIAware true
VIProductVersion "0.9.0.0"
VIAddVersionKey /LANG=1033 "ProductName" "The Fourth Institute"
VIAddVersionKey /LANG=1033 "FileDescription" "Windows x64 preview installer"
VIAddVersionKey /LANG=1033 "FileVersion" "0.9.0-preview"
VIAddVersionKey /LANG=1033 "LegalCopyright" "The Fourth Institute contributors"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "Install the shared Portal platform and desktop instance manager.$\r$\n$\r$\nPython is included. Initializing an instance downloads PostgreSQL, Node.js and selected feature dependencies.$\r$\n$\r$\nWindows 11 x64 preview: native acceptance testing is pending. Instance data is retained when uninstalling."
!insertmacro MUI_PAGE_WELCOME
!define MUI_DIRECTORYPAGE_TEXT_TOP "Choose a new or empty folder. Existing files and instances will not be overwritten."
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Open The Fourth Institute"
!define MUI_FINISHPAGE_RUN_FUNCTION Launch
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "This package requires 64-bit Windows."
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "This preview targets Windows 11 x64."
    Abort
  ${EndIf}
  SetRegView 64
FunctionEnd

Function .onVerifyInstDir
  ; Never install into a populated directory whose files we do not own.
  FindFirst $0 $1 "$INSTDIR\*"
  check_entry:
    StrCmp $1 "" directory_empty
    StrCmp $1 "." next_entry
    StrCmp $1 ".." next_entry
    FindClose $0
    Abort
  next_entry:
    FindNext $0 $1
    Goto check_entry
  directory_empty:
    FindClose $0
FunctionEnd

Section "Platform"
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD}/*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\The Fourth Institute"
  CreateShortcut "$SMPROGRAMS\The Fourth Institute\Portal.lnk" "$INSTDIR\dependencies\python\pythonw.exe" '"$INSTDIR\code\scripts\tfi_windows_launcher.py"'
  CreateShortcut "$DESKTOP\The Fourth Institute.lnk" "$INSTDIR\dependencies\python\pythonw.exe" '"$INSTDIR\code\scripts\tfi_windows_launcher.py"'
  CreateShortcut "$SMPROGRAMS\The Fourth Institute\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "DisplayName" "The Fourth Institute (Windows preview)"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "DisplayVersion" "0.9 Windows preview"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "InstallLocation" "$INSTDIR"
SectionEnd

Function Launch
  Exec '"$INSTDIR\dependencies\python\pythonw.exe" "$INSTDIR\code\scripts\tfi_windows_launcher.py"'
FunctionEnd

Section "Uninstall"
  SetRegView 64
  SetShellVarContext current
  nsExec::ExecToLog '"$INSTDIR\dependencies\python\python.exe" "$INSTDIR\code\scripts\tfi_windows_launcher.py" --uninstall-platform'
  Pop $0
  StrCmp $0 "0" stopped
    MessageBox MB_ICONSTOP "Could not prepare uninstall. Nothing was deleted. Close the instance manager before uninstalling. If this persists, stop instances in the launcher, close it, and retry."
    Abort
  stopped:
  ; Deliberately keep instance/, dependency caches and .build/ for recovery.
  RMDir /r "$INSTDIR\code"
  RMDir /r "$INSTDIR\dependencies\python"
  RMDir /r "$INSTDIR\packaging"
  Delete "$INSTDIR\prepare_install.py"
  Delete "$INSTDIR\example-seed.zip"
  Delete "$INSTDIR\windows-package.json"
  Delete "$INSTDIR\WINDOWS.md"
  Delete "$INSTDIR\INSTALL.md"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\README_CN.md"
  Delete "$DESKTOP\The Fourth Institute.lnk"
  RMDir /r "$SMPROGRAMS\The Fourth Institute"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute"
  Delete "$INSTDIR\Uninstall.exe"
  MessageBox MB_ICONINFORMATION "Platform removed. Your instance data remains under:$\r$\n$INSTDIR\instance"
SectionEnd
