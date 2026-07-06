@echo off
chcp 65001 >nul
title MAAOrch
cd /d "%~dp0"

echo ========================================
echo   MAAOrch Launcher
echo   First launch will download MAA (~200MB)
echo ========================================
echo.

where python >nul 2>nul || (
    echo ========================================
    echo   Python not found. Please install Python 3.12+
    echo   https://www.python.org/downloads/
    echo ========================================
    pause
    exit /b 1
)

echo [MAAOrch] Installing dependencies...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [MAAOrch] Install failed. Try: pip install -r requirements.txt
    pause
)

echo [MAAOrch] Starting server...
start /min "" python "main_web.pyw"

echo [MAAOrch] Waiting for server (up to 120s)...
set WAIT_SEC=0
:loop
timeout /t 3 /nobreak >nul
set /a WAIT_SEC+=3
curl -s http://127.0.0.1:19999/ >nul 2>&1
if not errorlevel 1 (
    echo.
    echo ========================================
    echo   MAAOrch is ready! (%WAIT_SEC%s)
    echo   Browser opened
    echo ========================================
    start http://127.0.0.1:19999/
    timeout /t 3 /nobreak >nul
    exit /b 0
)
if %WAIT_SEC% lss 120 goto loop

echo ========================================
echo   Timeout. Check: netstat -ano ^| findstr 19999
echo   Or check debug.log
echo ========================================
pause