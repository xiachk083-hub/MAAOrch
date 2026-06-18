@echo off
chcp 65001 >nul
title MAAOrch Agent Installer
echo ========================================
echo  MAAOrch Agent Installer
echo ========================================
echo.

:: Check if already running
tasklist /FI "IMAGENAME eq maorch-agent.exe" 2>NUL | find /I /N "maorch-agent.exe" >NUL
if "%ERRORLEVEL%"=="0" (
    echo [*] Agent is already running, stopping...
    taskkill /F /IM maorch-agent.exe >NUL 2>&1
    timeout /T 2 /NOBREAK >NUL
)

:: Copy agent to a permanent location
set "AGENT_DIR=%USERPROFILE%\.maorch-agent"
if not exist "%AGENT_DIR%" mkdir "%AGENT_DIR%"
copy /Y "%~dp0maorch-agent.exe" "%AGENT_DIR%\" >NUL
if exist "%~dp0agent_config.json" copy /Y "%~dp0agent_config.json" "%AGENT_DIR%\" >NUL

:: Generate default token if empty
if not exist "%AGENT_DIR%\agent_config.json" (
    echo { "port": 19998, "token": "", "work_dir": "" } > "%AGENT_DIR%\agent_config.json"
)

echo.
echo  Config: %AGENT_DIR%\agent_config.json
echo  Edit this file to set:
echo    - port:    Agent port (default 19998)
echo    - token:   Auth token (leave empty for no auth)
echo    - work_dir: MAAOrch installation path
echo.
echo  Example:
echo    { "port": 19998, "token": "mysecret", "work_dir": "E:\\1-1\\MAAOrch" }
echo.

:: Ask for work directory
set /p WORK_DIR="Enter MAAOrch path (or press Enter to skip): "
if not "%WORK_DIR%"=="" (
    powershell -Command "(Get-Content '%AGENT_DIR%\agent_config.json') -replace '\"work_dir\": \"\"', '\"work_dir\": \"%WORK_DIR:\=\\%\"' | Set-Content '%AGENT_DIR%\agent_config.json'"
)

:: Ask for token
set /p TOKEN="Enter auth token (or press Enter for no auth): "
if not "%TOKEN%"=="" (
    powershell -Command "(Get-Content '%AGENT_DIR%\agent_config.json') -replace '\"token\": \"\"', '\"token\": \"%TOKEN%\"' | Set-Content '%AGENT_DIR%\agent_config.json'"
)

:: Start agent
start /min "" "%AGENT_DIR%\maorch-agent.exe"
timeout /T 2 /NOBREAK >NUL

:: Verify
tasklist /FI "IMAGENAME eq maorch-agent.exe" 2>NUL | find /I /N "maorch-agent.exe" >NUL
if "%ERRORLEVEL%"=="0" (
    echo [✓] Agent started successfully
) else (
    echo [✗] Agent failed to start
)

echo.
echo  Use 'taskkill /F /IM maorch-agent.exe' to stop
echo.
pause
