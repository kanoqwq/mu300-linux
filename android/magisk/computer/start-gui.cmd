@echo off
rem MU300 Linux from this zip, on a Windows computer, in a window: the graphical installer, with the system and the
rem kernel of this zip, from its own files. It needs a Python 3.8 or newer with tkinter; the installer looks for one,
rem offers to install it with winget, and goes on as the text installer when there is no window to be had.
rem START-HERE.md says more.
setlocal
set "MU300_PACKAGE=%~dp0."
set "MU300_GUI=1"
if exist "%~dp0platform-tools\adb.exe" set "PATH=%~dp0platform-tools;%PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0computer\install.ps1" %*
set "MU300_RC=%ERRORLEVEL%"
if not "%MU300_RC%"=="0" pause
exit /b %MU300_RC%
