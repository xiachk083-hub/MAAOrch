@echo off
chcp 65001 >nul
title MAAOrch
cd /d "%~dp0"

echo ========================================
echo   MAAOrch
echo   首次启动会自动下载 MAA（约200MB）
echo   请耐心等待
echo ========================================
echo.

where python >nul 2>nul || (
    echo ========================================
    echo   未检测到 Python
    echo   请安装 Python 3.12+
    echo   https://www.python.org/downloads/
    echo ========================================
    pause
    exit /b 1
)

echo [MAAOrch] 检查依赖...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [MAAOrch] 安装依赖失败，尝试手动: pip install -r requirements.txt
    pause
)

echo [MAAOrch] 启动服务器...
start /min "" python "main_web.pyw"

echo [MAAOrch] 等待服务器就绪（最多 120 秒）...
set WAIT_SEC=0
:loop
timeout /t 3 /nobreak >nul
set /a WAIT_SEC+=3
curl -s http://127.0.0.1:19999/ >nul 2>&1
if not errorlevel 1 (
    echo.
    echo ========================================
    echo   MAAOrch 已就绪！（约 %WAIT_SEC% 秒）
    echo   浏览器已打开
    echo ========================================
    start http://127.0.0.1:19999/
    timeout /t 3 /nobreak >nul
    exit /b 0
)
if %WAIT_SEC% lss 120 goto loop

echo ========================================
echo   服务器启动超时
echo   排查: netstat -ano ^| findstr 19999
echo   或在项目目录查看 debug.log
echo ========================================
pause
