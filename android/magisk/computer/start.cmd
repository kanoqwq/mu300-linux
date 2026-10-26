@echo off
rem MU300 Linux from this zip, on a Windows computer: the installer, with the system and the kernel of this zip, from
rem its own files - nothing is downloaded. It asks first whether to open its window (start-gui.cmd opens it without
rem asking). START-HERE.md says what it needs. Options are install.ps1's: start.cmd -Check
setlocal
set "MU300_PACKAGE=%~dp0."
rem adb from Google's platform-tools unpacked into this folder (platform-tools\adb.exe) needs no PATH entry
if exist "%~dp0platform-tools\adb.exe" set "PATH=%~dp0platform-tools;%PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0computer\install.ps1" %*
set "MU300_RC=%ERRORLEVEL%"
pause
exit /b %MU300_RC%
