@echo off
chcp 65001 >nul
title MAAOrch-Manager
cd /d "%~dp0"
pythonw "%~dp0manager.py"
