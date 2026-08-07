@echo off
chcp 65001 >nul
title MAAOrch-Manager Install
cd /d "%~dp0"

echo ============================================
echo   MAAOrch-Manager 安装
echo ============================================
echo.

:: 1. Ensure manager dir is self-contained (copy self to E:\MAAOrch-Manager if running from elsewhere)
if /i not "%~dp0"=="E:\MAAOrch-Manager\" (
    echo 当前目录不是 E:\MAAOrch-Manager，正在复制...
    if not exist "E:\MAAOrch-Manager" mkdir "E:\MAAOrch-Manager"
    copy /Y "%~dp0manager.py" "E:\MAAOrch-Manager\manager.py" >nul
    copy /Y "%~dp0manager.bat" "E:\MAAOrch-Manager\manager.bat" >nul
    echo 已复制到 E:\MAAOrch-Manager
    cd /d "E:\MAAOrch-Manager"
)

:: 2. Ensure config.json (auto-generates token on first run)
if not exist "config.json" (
    echo 首次运行，将自动生成 token...
)

:: 3. Register auto-start (registry Run key)
set REG_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run
reg add "%REG_KEY%" /v "MAAOrchManager" /t REG_SZ /d "\"%CD%\manager.bat\"" /f >nul
echo 已注册开机自启

:: 4. Start manager
echo 启动管理器...
start "" pythonw "%~dp0manager.py"
timeout /t 2 /nobreak >nul

echo.
echo ============================================
echo   安装完成！管理器已在后台运行
echo   端口: 19998 | 项目目录: E:\MAAOrch
echo   查看 token: E:\MAAOrch-Manager\config.json
echo ============================================
pause
