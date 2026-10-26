@echo off
rem Run uninstall.ps1 from cmd.exe or by double-click, without changing the PowerShell execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
exit /b %ERRORLEVEL%
