@echo off
chcp 65001 >nul
title MAAOrch
cd /d "%~dp0"

echo ========================================
echo   MAAOrch 启动器
echo   首次启动会自动下载 MAA（约200MB）
echo   请耐心等待
echo ========================================
echo.

echo [MAAOrch] 检查依赖...
pip install -r requirements.txt -q

echo [MAAOrch] 启动服务器...
start /min "" python main_web.pyw

echo [MAAOrch] 等待服务器就绪（最多 60 秒）...
set WAIT_SEC=0
:wait
if %WAIT_SEC% geq 60 (
    echo ========================================
    echo   服务器启动超时
    echo   排查: netstat -ano ^| findstr 19999
    echo   或在项目目录查看 debug.log
    echo ========================================
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
set /a WAIT_SEC+=2
curl -s http://127.0.0.1:19999/ >nul 2>&1 && (
    echo.
    echo ========================================
    echo   MAAOrch 已就绪！浏览器已打开
    echo   如需手动访问: http://127.0.0.1:19999
    echo ========================================
    start http://127.0.0.1:19999/
    timeout /t 3 /nobreak >nul
    exit /b 0
)
goto wait
