@echo off
rem Start the Buddy Runner from Command Prompt, or where PowerShell scripts are
rem disabled, without changing the execution policy. It takes the same
rem parameters as start-buddy-runner.ps1, written as in PowerShell. Quote a
rem value with spaces, and separate several roots or origins with commas:
rem   start-buddy-runner.cmd -Port 8766 -Root 'C:\Projects\One','C:\Projects\Two'
setlocal
rem PowerShell reads the arguments itself, so quotes and lists work as they do
rem there; powershell.exe -File would pass each one on as plain text. The
rem variables are cleared before the Runner starts, so its CLIs do not get them.
set "BUDDY_RUNNER_LAUNCHER=%~dp0start-buddy-runner.ps1"
set BUDDY_RUNNER_ARGUMENTS=%*
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$launcher = $env:BUDDY_RUNNER_LAUNCHER; $arguments = $env:BUDDY_RUNNER_ARGUMENTS;" ^
  "Remove-Item Env:BUDDY_RUNNER_LAUNCHER, Env:BUDDY_RUNNER_ARGUMENTS -ErrorAction Ignore;" ^
  "Invoke-Expression ('& $launcher ' + $arguments); exit $LASTEXITCODE"
exit /b %ERRORLEVEL%
