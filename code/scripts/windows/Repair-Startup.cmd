@echo off
setlocal
set "TFI_REPAIR_ROOT=%LOCALAPPDATA%\TheFourthInstitute"
if not "%~1"=="" set "TFI_REPAIR_ROOT=%~1"
if not exist "%TFI_REPAIR_ROOT%\dependencies\python\python.exe" (
  echo Installed Python was not found. Run Repair-Startup.cmd with the installation directory as its argument.
  pause
  exit /b 1
)
"%TFI_REPAIR_ROOT%\dependencies\python\python.exe" "%~dp0repair_startup.py" "%TFI_REPAIR_ROOT%"
set "TFI_REPAIR_RESULT=%errorlevel%"
pause
exit /b %TFI_REPAIR_RESULT%
