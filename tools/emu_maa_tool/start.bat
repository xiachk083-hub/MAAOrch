@echo off
rem MAAOrch tool launcher (local mode): start server if needed, then open desktop window
cd /d "%~dp0"

rem 1. start server if not listening on 18889
set SERVER_READY=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":18889 " ^| findstr "LISTENING"') do (
  set SERVER_READY=1
)
if "%SERVER_READY%"=="0" (
  start "MAAOrchToolServer" /min cmd /c ""pythonw" "%~dp0server\main.py" 127.0.0.1 > "%~dp0data\server_run.log" 2>&1"
  timeout /t 4 /nobreak >nul
)

rem 2. open desktop window
python "%~dp0client\app.py" http://127.0.0.1:18889
