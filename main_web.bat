@echo off
chcp 65001 >nul
title MAAOrch
cd /d "%~dp0"

echo ========================================
echo   MAAOrch Launcher
echo   First launch will download MAA (~200MB)
echo ========================================
echo.

echo [MAAOrch] Installing dependencies...
pip install -r requirements.txt -q

echo [MAAOrch] Starting server...
echo [MAAOrch] MAA first download runs in background (~200MB), UI works immediately
start /min "" python main_web.pyw

echo [MAAOrch] Waiting for server (up to 60s)...
set WAIT_SEC=0
:wait
if %WAIT_SEC% geq 60 (
    echo ========================================
    echo   Timeout. Check: netstat -ano ^| findstr 19999
    echo   Or check debug.log
    echo ========================================
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
set /a WAIT_SEC+=2
curl -s http://127.0.0.1:19999/ >nul 2>&1 && (
    echo.
    echo ========================================
    echo   MAAOrch is ready!
    echo   Browser opened to http://127.0.0.1:19999
    echo ========================================
    start http://127.0.0.1:19999/
    timeout /t 3 /nobreak >nul
    exit /b 0
)
goto wait