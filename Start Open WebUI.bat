@echo off
setlocal
title Open WebUI
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
if errorlevel 1 (
    echo Could not open the Open WebUI folder: %APP_DIR%
    pause
    exit /b 1
)
echo Starting Open WebUI at http://localhost:8080
echo Keep this window open while using the app.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%\start-open-webui.ps1"
set "APP_EXIT_CODE=%ERRORLEVEL%"
echo.
echo Open WebUI stopped with exit code %APP_EXIT_CODE%.
pause
exit /b %APP_EXIT_CODE%
