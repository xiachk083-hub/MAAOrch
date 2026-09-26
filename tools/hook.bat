@echo off
rem AUTO-MAS 完成钩子 — 常驻运行（新运行即抓取，Ctrl+C 停止）
chcp 65001 >nul
title AUTO-MAS-Hook
cd /d "%~dp0"
python auto_mas_hook.py
pause
