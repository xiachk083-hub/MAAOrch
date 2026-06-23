@echo off
chcp 65001 >nul
title MAAOrch 启动器
cd /d "%~dp0"

:: 检查 Python
where python >nul 2>nul || (
    echo [MAAOrch] 未检测到 Python，请先安装 Python 3.12+
    echo [MAAOrch] 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 安装依赖
echo [MAAOrch] 检查依赖...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [MAAOrch] 依赖安装失败，尝试手动: pip install -r requirements.txt
    pause
)

:: 启动服务器
echo [MAAOrch] 启动 MAAOrch...
start /min "" python "%~dp0main_web.pyw" --no-elevate

:: 等待服务器就绪（最多 120 秒）
echo [MAAOrch] 等待服务器启动...
set WAIT_SEC=0
:WAIT_LOOP
timeout /t 3 /nobreak >nul
set /a WAIT_SEC+=3
>nul 2>nul curl -s http://127.0.0.1:19999/ && (
    echo [MAAOrch] 服务器已就绪 ^(约 %WAIT_SEC% 秒^)
    start http://127.0.0.1:19999/
    exit /b 0
)
if %WAIT_SEC% lss 120 goto WAIT_LOOP

echo [MAAOrch] 服务器启动超时，请检查 debug.log
pause
