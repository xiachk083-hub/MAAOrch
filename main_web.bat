@echo off
chcp 65001 >nul
title MAAOrch
cd /d "%~dp0"

echo [MAAOrch] Installing dependencies...
pip install -r requirements.txt -q

echo [MAAOrch] Starting...
start /min "" python main_web.pyw --no-elevate

echo [MAAOrch] Waiting for server...
:wait
timeout /t 2 /nobreak >nul
curl -s http://127.0.0.1:19999/ >nul 2>&1 && (
    start http://127.0.0.1:19999/
    exit /b 0
)
goto wait
