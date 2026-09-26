@echo off
rem MAAOrch tool server (local mode). Remote deploy uses deploy_server.py.
cd /d "%~dp0"
python server\main.py 127.0.0.1
pause
