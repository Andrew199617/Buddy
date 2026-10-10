@echo off
rem Start the Buddy Runner from Command Prompt, or where PowerShell scripts are
rem disabled, without changing the execution policy. It takes the same
rem parameters as start-buddy-runner.ps1, for example -Port 8765.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-buddy-runner.ps1" %*
exit /b %ERRORLEVEL%
