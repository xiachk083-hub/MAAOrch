@echo off
chcp 65001 >nul
title MAAOrch
cd /d "%~dp0"

echo [MAAOrch] Installing dependencies...
pip install -r requirements.txt -q

echo [MAAOrch] Starting...
start /min "" python main_web.pyw --no-elevate

echo [MAAOrch] Waiting for server (up to 30s)...
set WAIT_SEC=0
:wait
if %WAIT_SEC% geq 30 (
    echo [MAAOrch] Timeout. Check if port 19999 is in use:
    echo [MAAOrch]   netstat -ano ^| findstr 19999
    echo [MAAOrch] Or check debug.log for errors.
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
set /a WAIT_SEC+=2
curl -s http://127.0.0.1:19999/ >nul 2>&1 && (
    echo [MAAOrch] Ready!
    start http://127.0.0.1:19999/
    exit /b 0
)
goto wait
