@echo off
title MAAOrch 终端
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto nopython

echo ========================================
echo   MAAOrch 终端模式
echo   日志实时显示在下方
echo   关闭此窗口 或 按 Ctrl+C 即停止项目
echo ========================================
echo.
python -u main_web.pyw --console
echo.
echo [MAAOrch] 项目已停止，按任意键关闭窗口
pause >nul
exit /b 0

:nopython
echo [MAAOrch] 未找到 Python，请先安装 Python 3.12+
pause
exit /b 1
