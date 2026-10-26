@echo off
rem Remove MU300 Linux from the device again, from a Windows computer: the project's uninstaller (it asks before
rem anything is written; START-HERE.md). Options are uninstall.ps1's.
setlocal
if exist "%~dp0platform-tools\adb.exe" set "PATH=%~dp0platform-tools;%PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0computer\uninstall.ps1" %*
set "MU300_RC=%ERRORLEVEL%"
pause
exit /b %MU300_RC%
