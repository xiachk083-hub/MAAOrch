@echo off
chcp 65001 >nul
title MAAOrch
cd /d "%~dp0"

echo [MAAOrch] Installing dependencies...
pip install -r requirements.txt -q

echo [MAAOrch] Starting...
python main_web.pyw --no-elevate

echo [MAAOrch] Exited (errorlevel=%errorlevel%)
pause
