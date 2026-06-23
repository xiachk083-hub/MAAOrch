@echo off
chcp 65001 >nul
title MAAOrch
cd /d "%~dp0"

where python >nul 2>nul || (
    echo [MAAOrch] Python not found. Please install Python 3.12+
    echo [MAAOrch] https://www.python.org/downloads/
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
start /min "" python "main_web.pyw" --no-elevate

echo [MAAOrch] Waiting for server (up to 120s)...
set WAIT_SEC=0
:loop
timeout /t 3 /nobreak >nul
set /a WAIT_SEC+=3
curl -s http://127.0.0.1:19999/ >nul 2>&1
if not errorlevel 1 (
    echo [MAAOrch] Server ready! (%WAIT_SEC%s)
    start http://127.0.0.1:19999/
    exit /b 0
)
if %WAIT_SEC% lss 120 goto loop

echo [MAAOrch] Timeout. Check debug.log
pause
