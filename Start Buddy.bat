@echo off
setlocal
title Buddy
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
if errorlevel 1 (
    echo Could not open the Buddy folder: %APP_DIR%
    pause
    exit /b 1
)

echo Starting Buddy. The server log shows its configured address.
echo Keep this window open while using the app.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%start-buddy.ps1" %*
set "APP_EXIT_CODE=%ERRORLEVEL%"
echo.
echo Buddy stopped with exit code %APP_EXIT_CODE%.
pause
exit /b %APP_EXIT_CODE%
