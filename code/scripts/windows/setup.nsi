Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "nsDialogs.nsh"
Var RemoveInstances
Var RemoveInstancesCheckbox
Var UninstallDialog
!define MUI_ICON "${PAYLOAD}/branding/tfi.ico"
!define MUI_UNICON "${PAYLOAD}/branding/tfi.ico"

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
!define MUI_WELCOMEPAGE_TEXT "Your own research space for people and AI.$\r$\n$\r$\nAfter setup, Portal automatically prepares the included example and opens in your browser. First use downloads the required environment; keep your Internet connection available.$\r$\n$\r$\nWindows 11 x64 preview. Uninstall keeps instance data unless you explicitly choose to remove it."
!insertmacro MUI_PAGE_WELCOME
!define MUI_DIRECTORYPAGE_TEXT_TOP "Choose a new or empty folder. Existing files and instances will not be overwritten."
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TEXT "Portal will now prepare your research space and open your browser. First use needs an Internet connection and can take several minutes."
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.InstanceOptions un.InstanceOptionsLeave
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"

Function un.onInit
  StrCpy $RemoveInstances ${BST_UNCHECKED}
FunctionEnd

Function un.InstanceOptions
  !insertmacro MUI_HEADER_TEXT "数据与缓存 / Data and caches" "默认保留数据 / Keep data by default"
  nsDialogs::Create 1018
  Pop $UninstallDialog
  ${If} $UninstallDialog == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 58u "默认保留实例和缓存。勾选后永久删除本目录下所有实例的数据库、文件、账号、对话，以及依赖缓存和日志。$\r$\nKeep data by default. Selecting below permanently deletes ALL local instances, dependencies, caches and logs."
  Pop $0
  ${NSD_CreateLabel} 0 64u 100% 24u "$INSTDIR"
  Pop $0
  ${NSD_CreateCheckbox} 0 96u 100% 24u "同时删除实例和缓存 / Delete instances and caches"
  Pop $RemoveInstancesCheckbox
  ${NSD_SetState} $RemoveInstancesCheckbox $RemoveInstances
  nsDialogs::Show
FunctionEnd

Function un.InstanceOptionsLeave
  ${NSD_GetState} $RemoveInstancesCheckbox $RemoveInstances
  ${If} $RemoveInstances == ${BST_CHECKED}
    MessageBox MB_YESNO|MB_ICONEXCLAMATION|MB_DEFBUTTON2 "永久删除此目录内的全部实例、依赖缓存和日志？$\r$\n$INSTDIR$\r$\n数据库、账号、对话与文件无法恢复。$\r$\nPermanently delete ALL local instances, dependencies, caches and logs? This cannot be undone." IDYES confirmed
    Abort
    confirmed:
  ${EndIf}
FunctionEnd

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
  CreateShortcut "$SMPROGRAMS\The Fourth Institute\Portal.lnk" "$INSTDIR\dependencies\python\pythonw.exe" '"$INSTDIR\code\scripts\tfi_windows_launcher.py"' "$INSTDIR\branding\tfi.ico" 0
  CreateShortcut "$DESKTOP\The Fourth Institute.lnk" "$INSTDIR\dependencies\python\pythonw.exe" '"$INSTDIR\code\scripts\tfi_windows_launcher.py"' "$INSTDIR\branding\tfi.ico" 0
  CreateShortcut "$SMPROGRAMS\The Fourth Institute\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "DisplayName" "The Fourth Institute (Windows preview)"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "DisplayVersion" "0.9 Windows preview"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "DisplayIcon" "$INSTDIR\branding\tfi.ico"
SectionEnd

Function .onInstSuccess
  Exec '"$INSTDIR\dependencies\python\pythonw.exe" "$INSTDIR\code\scripts\tfi_windows_launcher.py"'
FunctionEnd

Section "Uninstall"
  SetRegView 64
  SetShellVarContext current
  ; Do not hold the directory that a complete uninstall needs to remove.
  SetOutPath "$TEMP"
  ${If} $RemoveInstances == ${BST_CHECKED}
    nsExec::ExecToLog '"$INSTDIR\dependencies\python\python.exe" "$INSTDIR\code\scripts\tfi_windows_launcher.py" --uninstall-platform --remove-instances'
  ${Else}
    nsExec::ExecToLog '"$INSTDIR\dependencies\python\python.exe" "$INSTDIR\code\scripts\tfi_windows_launcher.py" --uninstall-platform'
  ${EndIf}
  Pop $0
  StrCmp $0 "0" stopped
    MessageBox MB_ICONSTOP "卸载未完成，平台文件已保留。若已选择删除实例，部分实例可能已清理。请查看日志后重试。$\r$\nUninstall incomplete; platform files retained. Some selected instances may already have been removed. See .build\windows\launcher-error.log."
    Abort
  stopped:
  ; ExecToLog has waited for Python to release its runtime and launcher lock.
  ; These validated trees are deleted only with explicit data-removal consent.
  ${If} $RemoveInstances == ${BST_CHECKED}
    RMDir /r "$INSTDIR\.build"
    RMDir /r "$INSTDIR\dependencies"
  ${Else}
    RMDir /r "$INSTDIR\dependencies\python"
  ${EndIf}
  RMDir /r "$INSTDIR\code"
  RMDir /r "$INSTDIR\packaging"
  RMDir /r "$INSTDIR\branding"
  Delete "$INSTDIR\prepare_install.py"
  Delete "$INSTDIR\example-seed.zip"
  Delete "$INSTDIR\windows-package.json"
  Delete "$INSTDIR\WINDOWS.md"
  Delete "$INSTDIR\INSTALL.md"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\README_CN.md"
  ; A newer preview may live in a different folder. Only remove shared shell
  ; entries when this installation still owns the registered location.
  ReadRegStr $1 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute" "InstallLocation"
  StrCmp $1 "$INSTDIR" remove_shell_entries keep_shell_entries
  remove_shell_entries:
    Delete "$DESKTOP\The Fourth Institute.lnk"
    RMDir /r "$SMPROGRAMS\The Fourth Institute"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TheFourthInstitute"
  keep_shell_entries:
  Delete "$INSTDIR\Uninstall.exe"
  ${If} $RemoveInstances == ${BST_CHECKED}
    ; Never recursively remove the installation root: unknown user files stay.
    ; A nonempty directory also catches locked files left by any earlier step.
    ClearErrors
    RMDir "$INSTDIR"
    ${If} ${Errors}
      SetErrorLevel 1
      MessageBox MB_ICONEXCLAMATION "卸载仍有残留文件：$\r$\n$INSTDIR$\r$\n可能有文件被占用，或存在自行添加的文件。请关闭相关程序后检查此目录。$\r$\nSome files remain (in use or added separately). Check this folder after closing related programs."
    ${Else}
      MessageBox MB_ICONINFORMATION "平台、实例、依赖缓存和日志已删除。$\r$\nPlatform, instances, dependencies, caches and logs removed."
    ${EndIf}
  ${Else}
    MessageBox MB_ICONINFORMATION "平台已卸载，实例数据保留于：$\r$\nPlatform removed. Instance data retained:$\r$\n$INSTDIR\instance"
  ${EndIf}
SectionEnd
